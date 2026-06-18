import os
import hashlib
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    Docx2txtLoader,
    TextLoader,
    BSHTMLLoader,
)
from langchain_core.documents import Document as LCDocument

from config.settings import settings
from skills.rag.collection import milvus_client, COLLECTION_NAME, embeddings, init_collection


class _PyMuPDFLoader:
    """用 PyMuPDF 提取 PDF 文本，避免 PyPDFLoader 的 GBK 编码问题。"""
    def __init__(self, file_path: str):
        self._file_path = file_path

    def load(self) -> list[LCDocument]:
        import fitz
        docs = []
        pdf = fitz.open(self._file_path)
        for i, page in enumerate(pdf):
            text = page.get_text()
            if text.strip():
                docs.append(LCDocument(
                    page_content=text,
                    metadata={"source": self._file_path, "page": i},
                ))
        pdf.close()
        return docs


def _detect_encoding(file_path: str) -> str:
    """探测文件编码，返回编码字符串。读取前 10KB。"""
    from charset_normalizer import from_bytes

    with open(file_path, "rb") as f:
        raw = f.read(10240)

    # BOM 检测优先
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    result = from_bytes(raw).best()
    if result is None:
        return "utf-8"

    encoding = result.encoding.replace("_", "-")
    if encoding in ("utf-8", "ascii"):
        return "utf-8"
    if encoding in ("gb18030", "gb2312", "gbk"):
        return "gb18030"
    if encoding == "big5":
        # charset_normalizer 对短中文文本常误判 big5 → 用 gb18030 试解码
        try:
            raw.decode("gb18030")
            return "gb18030"
        except UnicodeDecodeError:
            pass

    return encoding


def _detect_text_loader(file_path: str) -> TextLoader:
    """用 charset_normalizer 探测文件编码，返回 TextLoader。"""
    return TextLoader(file_path, encoding=_detect_encoding(file_path))


# ── 加载器映射 ──────────────────────────────────────────────
LOADER_MAP = {
    ".pdf":  lambda p: _PyMuPDFLoader(p),
    ".docx": lambda p: Docx2txtLoader(p),
    ".doc":  lambda p: Docx2txtLoader(p),
    ".txt":  _detect_text_loader,
    ".md":   _detect_text_loader,
    ".html": lambda p: BSHTMLLoader(p, open_encoding=_detect_encoding(p)),
    ".htm":  lambda p: BSHTMLLoader(p, open_encoding=_detect_encoding(p)),
}


def load_and_split(file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    loader_factory = LOADER_MAP.get(ext)
    if loader_factory is None:
        raise ValueError(f"不支持的文件类型: {ext}")

    docs = loader_factory(file_path).load()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(docs)
    return chunks


def compute_file_hash(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def ingest_document(
    file_path: str,
    document_id: str = "",
    user_id: int = 0,
    is_public: bool = False,
    source_display_name: str = "",
):
    init_collection()
    chunks = load_and_split(file_path)
    display_name = source_display_name or os.path.basename(file_path)

    texts = [c.page_content for c in chunks]
    vectors = embeddings.embed_documents(texts)

    data = [
        {
            "embedding": vectors[i],
            "text": texts[i],
            "source": display_name,
            "document_id": str(document_id),
            "chunk_index": i,
            "user_id": int(user_id),
            "is_public": bool(is_public),
        }
        for i in range(len(texts))
    ]

    milvus_client.insert(COLLECTION_NAME, data)

    # 触发 BM25 索引更新
    try:
        from skills.rag.bm25_index import bm25_index
        bm25_index.refresh_index(milvus_client, COLLECTION_NAME)
    except Exception as e:
        print(f"BM25 refresh_index 失败: {e}")

    print(f"已入库 {len(data)} 个 chunk，来源：{display_name}")
    return len(data)


def delete_document_chunks(document_id: str):
    init_collection()
    milvus_client.delete(
        COLLECTION_NAME,
        filter=f'document_id == "{document_id}"',
    )
    # 触发 BM25 索引更新
    try:
        from skills.rag.bm25_index import bm25_index
        bm25_index.refresh_index(milvus_client, COLLECTION_NAME)
    except Exception as e:
        print(f"BM25 refresh_index 失败（删除文档后）: {e}")


def get_document_full_text(document_id: str, user_id: int) -> str:
    """从 Milvus 获取指定文档的全部 chunk，按 chunk_index 排序拼接返回全文。"""
    init_collection()
    results = milvus_client.query(
        COLLECTION_NAME,
        filter=f'document_id == "{document_id}" and user_id == {user_id}',
        output_fields=["text", "chunk_index", "source"],
        limit=10000,
    )
    if not results:
        raise ValueError("文档不存在或无权访问")
    results.sort(key=lambda x: x.get("chunk_index", 0))
    source = results[0].get("source", "")
    full_text = "\n\n".join(r["text"] for r in results)
    return full_text, source
