import hashlib

from config.settings import settings
from skills.rag.collection import milvus_client, COLLECTION_NAME, embeddings, init_collection


def _dense_search(query: str, user_id: int, public_only: bool, top_k: int,
                  document_ids: list[str] | None = None) -> list[dict]:
    """Dense 向量检索（Milvus filter 表达式过滤权限 + 文档范围）。"""
    query_vector = embeddings.embed_query(query)

    # 权限过滤
    if public_only:
        perm_filter = "is_public == true"
    else:
        perm_filter = f"is_public == true or user_id == {user_id}"

    # 文档范围过滤
    if document_ids:
        doc_ids_str = ", ".join(f'"{d}"' for d in document_ids)
        doc_filter = f"document_id in [{doc_ids_str}]"
        filter_expr = f"({doc_filter}) and ({perm_filter})"
    else:
        filter_expr = perm_filter

    results = milvus_client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        limit=top_k,
        filter=filter_expr,
        output_fields=["text", "source", "document_id", "chunk_index"],
    )

    docs = []
    for hit in results[0]:
        entity = hit["entity"]
        docs.append({
            "text": entity["text"],
            "source": entity["source"],
            "document_id": entity["document_id"],
            "chunk_index": entity.get("chunk_index"),
            "score": hit["distance"],
            "retrieval_method": "dense",
        })
    return docs


def _bm25_search(query: str, user_id: int, public_only: bool, top_k: int,
                 document_ids: list[str] | None = None) -> list[dict]:
    """BM25 检索（失败降级为空列表）。"""
    try:
        from skills.rag.bm25_index import bm25_index
        results = bm25_index.search(query, user_id=user_id, public_only=public_only, top_k=top_k)
        # 文档范围过滤
        if document_ids:
            doc_id_set = set(document_ids)
            results = [r for r in results if r.get("document_id") in doc_id_set]
        return results
    except Exception:
        return []


def _make_dedup_key(doc: dict) -> str:
    """生成去重唯一键。chunk_index 缺失时退化为 text hash。"""
    doc_id = doc.get("document_id", "")
    chunk_idx = doc.get("chunk_index")
    if chunk_idx is not None:
        return f"{doc_id}:{chunk_idx}"
    # fallback: 取 text 前 200 字的 hash
    text_prefix = doc.get("text", "")[:200]
    text_hash = hashlib.md5(text_prefix.encode()).hexdigest()[:8]
    return f"{doc_id}:hash_{text_hash}"


def _rrf_fusion(dense_results: list[dict], bm25_results: list[dict], k: int = 60, alpha: float = 0.5) -> list[dict]:
    """RRF 融合排序 + 去重。"""
    score_map: dict[str, dict] = {}

    for rank, doc in enumerate(dense_results, 1):
        key = _make_dedup_key(doc)
        rrf_score = alpha * (1.0 / (k + rank))
        if key in score_map:
            score_map[key]["score"] += rrf_score
            score_map[key]["retrieval_method"] = "hybrid"
        else:
            score_map[key] = {**doc, "score": rrf_score}

    for rank, doc in enumerate(bm25_results, 1):
        key = _make_dedup_key(doc)
        rrf_score = (1 - alpha) * (1.0 / (k + rank))
        if key in score_map:
            score_map[key]["score"] += rrf_score
            score_map[key]["retrieval_method"] = "hybrid"
        else:
            score_map[key] = {**doc, "score": rrf_score}

    fused = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
    return fused


def _bm25_primary_fusion(dense_results: list[dict], bm25_results: list[dict],
                         dense_bonus_weight: float = 0.3) -> list[dict]:
    """BM25 primary + Dense rerank 融合。

    BM25 结果作为候选池（全部保留），Dense 结果仅作为 rerank 加分信号。
    Dense-only 的 chunk 不进入候选池。
    """
    dense_lookup: dict[str, int] = {}
    for rank, doc in enumerate(dense_results, 1):
        key = _make_dedup_key(doc)
        dense_lookup[key] = rank

    score_map: dict[str, dict] = {}
    for rank, doc in enumerate(bm25_results, 1):
        key = _make_dedup_key(doc)
        bm25_score = 1.0 / (1.0 + rank)
        dense_bonus = 0.0
        if key in dense_lookup:
            dense_bonus = dense_bonus_weight * (1.0 / (1.0 + dense_lookup[key]))
        final_score = (1.0 - dense_bonus_weight) * bm25_score + dense_bonus
        method = "hybrid" if key in dense_lookup else "bm25"
        score_map[key] = {**doc, "score": final_score, "retrieval_method": method}

    return sorted(score_map.values(), key=lambda x: x["score"], reverse=True)


def _assign_relevance_label(rank: int) -> str:
    """按排序位次生成语义标签。"""
    if rank <= 2:
        return "高度相关"
    elif rank <= 4:
        return "相关"
    else:
        return "参考"


def _extract_snippet(text: str, match_pos: int | None = None) -> str:
    """截取 snippet：取匹配点前后各 100 字。"""
    if not text:
        return ""
    if match_pos is not None:
        start = max(0, match_pos - 100)
        end = min(len(text), match_pos + 100)
    else:
        # 无 match_pos，取前 200 字
        start = 0
        end = min(len(text), 200)
    return text[start:end]


def search_documents(query: str, top_k: int = None, *, user_id: int, public_only: bool = False,
                     document_ids: list[str] | None = None) -> list:
    """混合检索：Dense + BM25 + RRF 融合。支持 document_ids 范围过滤。"""
    init_collection()
    if top_k is None:
        top_k = settings.FINAL_TOP_K

    # 1. Dense 检索
    dense_results = _dense_search(query, user_id, public_only, settings.DENSE_TOP_K, document_ids)

    # 2. BM25 检索（失败降级为空列表）
    bm25_results = _bm25_search(query, user_id, public_only, settings.BM25_TOP_K, document_ids)

    # 3. 融合策略
    if settings.RETRIEVAL_STRATEGY == "bm25_primary":
        fused = _bm25_primary_fusion(dense_results, bm25_results, settings.DENSE_BONUS_WEIGHT)
    else:
        fused = _rrf_fusion(dense_results, bm25_results, settings.RRF_K, settings.RRF_ALPHA)

    # 4. 取 top_k + 生成 relevance_label + snippet
    results = []
    for rank, r in enumerate(fused[:top_k], 1):
        r["relevance_label"] = _assign_relevance_label(rank)
        r["snippet"] = _extract_snippet(r.get("text", ""))
        results.append(r)

    return results
