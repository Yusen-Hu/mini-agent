import threading
import time
from datetime import datetime

from rank_bm25 import BM25Okapi


_jieba_dict_loaded = False


def _tokenize(text: str) -> list[str]:
    """中文友好分词：jieba 分词 + 小写化 + 自定义词典。"""
    global _jieba_dict_loaded
    import jieba
    if not _jieba_dict_loaded:
        import os
        dict_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "jieba_userdict.txt")
        if os.path.exists(dict_path):
            jieba.load_userdict(dict_path)
        _jieba_dict_loaded = True
    return list(jieba.cut(text.lower()))


class BM25Index:
    """BM25 索引模块：生命周期管理 + 并发安全 + 失败降级。"""

    def __init__(self):
        self._index: BM25Okapi | None = None
        self._doc_chunks: dict[str, list[str]] = {}           # {doc_id: [chunk_texts]}
        self._tokenized_chunks: dict[str, list[list[str]]] = {}  # {doc_id: [tokenized_chunk, ...]}
        self._chunk_metadata: dict[str, list[dict]] = {}      # {doc_id: [{user_id, is_public, chunk_index}]}
        self._lock = threading.Lock()
        self._last_built_at: str | None = None
        self._last_error: str | None = None

    # ── 索引构建 ──────────────────────────────────────────────

    def build_index(self, milvus_client, collection_name: str):
        """服务启动时全量从 Milvus 拉取 chunk 文本，重建索引。"""
        try:
            all_rows = milvus_client.query(
                collection_name=collection_name,
                filter="",
                output_fields=["document_id", "text", "user_id", "is_public", "chunk_index", "source"],
                limit=16384,
            )
            self._build_from_rows(all_rows)
            self._last_error = None
        except Exception as e:
            self._last_error = str(e)
            print(f"BM25 build_index 失败，降级为纯 Dense: {e}")

    def refresh_index(self, milvus_client, collection_name: str):
        """文档上传/删除后全量重建索引。"""
        self.build_index(milvus_client, collection_name)

    def remove_document(self, doc_id: str):
        """从索引中移除指定文档的所有 chunk（本地内存操作，不查 Milvus）。"""
        with self._lock:
            self._doc_chunks.pop(doc_id, None)
            self._tokenized_chunks.pop(doc_id, None)
            self._chunk_metadata.pop(doc_id, None)
            if self._index is not None and self._doc_chunks:
                self._rebuild_index_locked()
            elif not self._doc_chunks:
                self._index = None

    # ── 检索 ──────────────────────────────────────────────────

    def search(self, query: str, user_id: int, public_only: bool = False, top_k: int = 20) -> list[dict]:
        """BM25 检索，支持权限过滤。索引未就绪时返回空列表（降级）。"""
        if self._index is None:
            return []

        t0 = time.monotonic()
        tokenized_query = _tokenize(query)
        all_docs = []
        all_tokenized = []
        all_meta = []
        for doc_id, tokens_list in self._tokenized_chunks.items():
            texts = self._doc_chunks.get(doc_id, [])
            meta_list = self._chunk_metadata.get(doc_id, [])
            for i, tokens in enumerate(tokens_list):
                meta = meta_list[i] if i < len(meta_list) else {}
                # 权限过滤
                if public_only and not meta.get("is_public", False):
                    continue
                if not public_only and not meta.get("is_public", False) and meta.get("user_id", 0) != user_id:
                    continue
                all_docs.append(texts[i] if i < len(texts) else "")
                all_tokenized.append(tokens)
                all_meta.append({"document_id": doc_id, **meta})

        if not all_tokenized:
            return []

        # 基于过滤后的文档子集重建 BM25 索引，确保 scores 索引与 all_tokenized 对齐
        filtered_index = BM25Okapi(all_tokenized)
        scores = filtered_index.get_scores(tokenized_query)

        # 取 top_k
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            results.append({
                "text": all_docs[idx],
                "document_id": all_meta[idx]["document_id"],
                "source": all_meta[idx].get("source", ""),
                "chunk_index": all_meta[idx].get("chunk_index"),
                "score": float(scores[idx]),
                "retrieval_method": "bm25",
            })

        elapsed = (time.monotonic() - t0) * 1000
        import logging
        logging.getLogger("bm25").info("BM25 search: %d chunks filtered, %.0fms, %d results", len(all_tokenized), elapsed, len(results))

        return results

    # ── 状态 ──────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "ready": self._index is not None,
            "chunk_count": sum(len(v) for v in self._doc_chunks.values()),
            "document_count": len(self._doc_chunks),
            "last_built_at": self._last_built_at,
            "error": self._last_error,
        }

    # ── 内部方法 ──────────────────────────────────────────────

    def _build_from_rows(self, rows: list[dict]):
        """从 Milvus 查询结果构建索引。"""
        doc_chunks: dict[str, list[str]] = {}
        chunk_metadata: dict[str, list[dict]] = {}

        for row in rows:
            doc_id = row.get("document_id", "")
            text = row.get("text", "")
            if not doc_id or not text:
                continue

            doc_chunks.setdefault(doc_id, []).append(text)
            chunk_metadata.setdefault(doc_id, []).append({
                "user_id": row.get("user_id", 0),
                "is_public": row.get("is_public", False),
                "chunk_index": row.get("chunk_index"),
                "source": row.get("source", ""),
            })

        with self._lock:
            self._doc_chunks = doc_chunks
            self._chunk_metadata = chunk_metadata
            if doc_chunks:
                self._rebuild_index_locked()
            else:
                self._index = None
            self._last_built_at = datetime.now().isoformat()

    def _rebuild_index_locked(self):
        """调用时必须已持有 _lock。预分词 + 构建 BM25Okapi 对象。"""
        self._tokenized_chunks = {}
        for doc_id, texts in self._doc_chunks.items():
            self._tokenized_chunks[doc_id] = [_tokenize(t) for t in texts]

        all_tokenized = []
        for tokens in self._tokenized_chunks.values():
            all_tokenized.extend(tokens)
        self._index = BM25Okapi(all_tokenized) if all_tokenized else None


# 全局单例
bm25_index = BM25Index()
