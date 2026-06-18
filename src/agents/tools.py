import re
from datetime import datetime
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from skills.rag.retrieval import search_documents
from config.logging_context import current_user_id
from config.logging import get_logger

logger = get_logger("tools")

# 引用数据存储：按 request_id 隔离，避免 ContextVar 跨 Task 丢失
_citations_store: dict[str, list] = {}


def get_citations(request_id: str) -> list:
    result = _citations_store.pop(request_id, [])
    logger.info("get_citations: request_id=%s, found=%d citations, store_keys=%s", request_id, len(result), list(_citations_store.keys()))
    return result


def _resolve_document_ids(config: RunnableConfig | None, explicit_ids: list[str] | None = None) -> list[str] | None:
    """解析 document_ids：显式参数优先，configurable 兜底。"""
    if explicit_ids:
        return explicit_ids
    if config:
        ids = config.get("configurable", {}).get("document_ids")
        if ids:
            return ids
    return None


def _store_citations(request_id: str, results: list[dict]):
    """把检索结果写入 citations store。"""
    citations = [
        {
            "document_id": r.get("document_id", ""),
            "filename": r.get("source", ""),
            "chunk_index": r.get("chunk_index"),
            "rrf_score": round(r.get("score", 0), 6),
            "relevance_label": r.get("relevance_label", "参考"),
            "snippet": r.get("snippet", ""),
            "retrieval_method": r.get("retrieval_method", "hybrid"),
        }
        for r in results
    ]
    _citations_store[request_id] = citations
    return citations


# ── 工具 1: get_current_time ──────────────────────────────────

@tool
def get_current_time(query: str) -> str:
    """当用户询问当前时间、日期时调用此工具。不要用于其他任何问题。"""
    now = datetime.now()
    return f"当前时间是：{now.strftime('%Y年%m月%d日 %H:%M:%S')}"


# ── 工具 2: search_knowledge_base ─────────────────────────────

@tool
def search_knowledge_base(
    config: RunnableConfig,
    query: str,
    document_ids: list[str] | None = None,
    top_k: int = 5,
    focus: str | None = None,
) -> str:
    """搜索知识库中与用户问题相关的文档内容。

    适用场景：
    - 用户询问文档中提到的某个概念、方法、结论
    - 用户问"XX 是什么"、"文档里有没有提到 XX"
    - 需要在文档中查找相关内容

    不适用场景：
    - 用户要找某段已知内容的精确原文出处 → 用 find_source
    - 用户要查看文档结构/统计/定义列表 → 用 get_document_info
    - 用户要列出有哪些文档 → 用 list_documents

    参数：
    - query: 搜索关键词或问题
    - document_ids: 可选，限定在指定文档 ID 范围内搜索。不传则全库搜索。
    - top_k: 返回结果数量，默认 5，范围 1-20
    - focus: 可选的语义偏好提示（如 "definition"、"data"），第一版仅作轻量偏好
    """
    request_id = config.get("configurable", {}).get("request_id", "") if config else ""
    uid = current_user_id.get()

    # 解析 document_ids（显式参数优先，configurable 兜底）
    doc_ids = _resolve_document_ids(config, document_ids)

    # top_k 保护
    top_k = max(1, min(top_k, 20))

    if uid == 0:
        results = search_documents(query, top_k=top_k, user_id=uid, public_only=True, document_ids=doc_ids)
    else:
        results = search_documents(query, top_k=top_k, user_id=uid, document_ids=doc_ids)

    if not results:
        # 不覆盖已有 citations（预检索可能已写入）
        return "知识库中没有找到相关内容。"

    # 给 LLM 的文本
    output = ""
    for i, r in enumerate(results):
        output += f"【片段{i+1}】来源：{r['source']}\n{r['text']}\n\n"

    # 写入 citations
    _store_citations(request_id, results)
    logger.info("search_knowledge_base: stored %d citations, request_id=%s, doc_ids=%s", len(results), request_id, doc_ids)

    return output


# ── 工具 3: find_source ───────────────────────────────────────

@tool
def find_source(
    config: RunnableConfig,
    query: str,
    document_id: str | None = None,
    top_k: int = 3,
) -> str:
    """定位某段内容的原文出处，返回精确的文档来源和上下文。

    适用场景：
    - 用户看到回答后追问"这句话原文在哪"
    - 用户想找某个概念/方法在原文中的精确位置
    - 需要提供可验证的引用证据

    不适用场景：
    - 用户想搜索文档中的相关内容 → 用 search_knowledge_base
    - 用户要查看文档整体结构 → 用 get_document_info

    与 search_knowledge_base 的区别：
    - search_knowledge_base 是"找相关内容"，返回多个相关片段
    - find_source 是"定位出处"，更强调文档名、位置、上下文窗口

    参数：
    - query: 要定位的内容关键词
    - document_id: 可选，限定在单篇文档内定位。不传则全库查找。
    - top_k: 返回结果数量，默认 3
    """
    request_id = config.get("configurable", {}).get("request_id", "") if config else ""
    uid = current_user_id.get()

    doc_ids = [document_id] if document_id else None
    doc_ids = _resolve_document_ids(config, doc_ids)

    if uid == 0:
        results = search_documents(query, top_k=top_k, user_id=uid, public_only=True, document_ids=doc_ids)
    else:
        results = search_documents(query, top_k=top_k, user_id=uid, document_ids=doc_ids)

    if not results:
        _citations_store[request_id] = []
        return "未找到匹配的原文出处。"

    # 输出强调出处信息
    lines = []
    for i, r in enumerate(results, 1):
        chunk_idx = r.get("chunk_index", "?")
        source = r.get("source", "未知来源")
        doc_id = r.get("document_id", "")
        method = r.get("retrieval_method", "hybrid")
        lines.append(
            f"[出处 {i}] 文档：{source}（ID: {doc_id}, chunk #{chunk_idx}, 检索方式: {method}）\n"
            f"原文片段：\n{r['text']}"
        )

    # 写入 citations
    _store_citations(request_id, results)
    logger.info("find_source: stored %d citations, request_id=%s", len(results), request_id)

    return "\n\n".join(lines)


# ── 工具 4: get_document_info ─────────────────────────────────

@tool
def get_document_info(document_id: str, mode: str = "stats") -> str:
    """获取文档的轻量结构化信息。不做全文分析或摘要。

    适用场景：
    - 用户问"这篇文档有多少 chunk/多大/什么时候上传的" → mode="stats"
    - 用户问"这篇文档的结构/目录/有哪些章节" → mode="outline"
    - 用户问"文档里怎么定义 XX" → mode="definitions"

    不适用场景：
    - 用户要文档内容摘要/总结 → 这是分析链路的职责，不要用此工具
    - 用户要搜索文档中的具体内容 → 用 search_knowledge_base

    参数：
    - document_id: 文档 ID
    - mode: 信息模式，可选 "stats"、"outline"、"definitions"
    """
    uid = current_user_id.get()

    if mode == "stats":
        return _get_doc_stats(document_id, uid)
    elif mode == "outline":
        return _get_doc_outline(document_id, uid)
    elif mode == "definitions":
        return _get_doc_definitions(document_id, uid)
    else:
        return f"不支持的 mode: {mode}。可选值：stats、outline、definitions"


def _get_doc_stats(document_id: str, user_id: int) -> str:
    """获取文档统计信息。"""
    from config.database import SessionLocal
    from src.types.document import Document
    from skills.rag.collection import milvus_client, COLLECTION_NAME, init_collection

    # 从 DB 取元数据
    from sqlalchemy import or_
    with SessionLocal() as db:
        doc = db.query(Document).filter(
            Document.id == int(document_id),
            or_(Document.is_public == True, Document.user_id == user_id),
        ).first()

    if not doc:
        return "文档不存在或无权访问。"

    status_map = {"ready": "已就绪", "processing": "处理中", "error": "处理失败"}
    status = status_map.get(doc.status, doc.status)
    size_mb = round(doc.file_size / 1024 / 1024, 2) if doc.file_size else 0

    lines = [
        f"文档：{doc.filename}",
        f"状态：{status}",
        f"大小：{size_mb} MB",
        f"Chunk 数量：{doc.chunk_count}",
        f"上传时间：{doc.created_at.strftime('%Y-%m-%d %H:%M') if doc.created_at else '未知'}",
        f"公开：{'是' if doc.is_public else '否'}",
    ]

    # 从 Milvus 取总字数和首尾预览
    try:
        init_collection()
        chunks = milvus_client.query(
            COLLECTION_NAME,
            filter=f'document_id == "{document_id}" and (is_public == true or user_id == {user_id})',
            output_fields=["text", "chunk_index"],
            limit=10000,
        )
        if chunks:
            total_chars = sum(len(c.get("text", "")) for c in chunks)
            chunks.sort(key=lambda x: x.get("chunk_index", 0))
            lines.append(f"总字数：{total_chars}")
            lines.append(f"首段预览：{chunks[0]['text'][:100]}...")
            if len(chunks) > 1:
                lines.append(f"末段预览：{chunks[-1]['text'][:100]}...")
    except Exception:
        pass

    return "\n".join(lines)


def _get_doc_outline(document_id: str, user_id: int) -> str:
    """从 chunk 中提取文档结构/标题。"""
    from skills.rag.collection import milvus_client, COLLECTION_NAME, init_collection

    init_collection()
    chunks = milvus_client.query(
        COLLECTION_NAME,
        filter=f'document_id == "{document_id}" and (is_public == true or user_id == {user_id})',
        output_fields=["text", "chunk_index", "source"],
        limit=10000,
    )
    if not chunks:
        return "文档不存在或无权访问。"

    chunks.sort(key=lambda x: x.get("chunk_index", 0))
    source = chunks[0].get("source", "未知文档")

    # 标题提取正则
    title_patterns = [
        r'^#{1,3}\s+(.+)',           # Markdown 标题
        r'^[一二三四五六七八九十]+[、.]\s*(.+)',  # 中文编号
        r'^\d+(\.\d+)*\s+(.+)',      # 数字编号 1. / 1.1
        r'^第.{1,5}[章节部分]\s*(.+)',  # 第X章/节
    ]

    titles = []
    for c in chunks:
        text = c.get("text", "")
        chunk_idx = c.get("chunk_index", 0)
        # 逐行检查
        for line in text.split("\n"):
            line = line.strip()
            if not line or len(line) > 100:
                continue
            for pattern in title_patterns:
                m = re.match(pattern, line)
                if m:
                    titles.append(f"  [{chunk_idx}] {line}")
                    break

    if not titles:
        return f"文档：{source}\n未提取到明显的章节结构。文档可能没有使用标准标题格式。"

    return f"文档：{source}\n共提取到 {len(titles)} 个标题/章节：\n" + "\n".join(titles)


def _get_doc_definitions(document_id: str, user_id: int) -> str:
    """从 chunk 中提取定义句。"""
    from skills.rag.collection import milvus_client, COLLECTION_NAME, init_collection

    init_collection()
    chunks = milvus_client.query(
        COLLECTION_NAME,
        filter=f'document_id == "{document_id}" and (is_public == true or user_id == {user_id})',
        output_fields=["text", "chunk_index", "source"],
        limit=10000,
    )
    if not chunks:
        return "文档不存在或无权访问。"

    source = chunks[0].get("source", "未知文档")

    # 定义句匹配模式
    def_patterns = [
        r'(.{2,30})(?:是指|定义为|指的是|表示的是|是一种)',
        r'所谓\s*(.{2,30})[，,]',
        r'(.{2,30})[:：]\s*(?:定义|概念|含义|是指)',
    ]

    seen = set()
    definitions = []

    for chunk in chunks:
        chunk_idx = chunk.get("chunk_index", "?")
        text = chunk.get("text", "")
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            for pattern in def_patterns:
                matches = re.findall(pattern, line)
                for match in matches:
                    term = match.strip() if isinstance(match, str) else match[0].strip()
                    if term and term not in seen and len(term) <= 30:
                        seen.add(term)
                        definitions.append(f"- [chunk #{chunk_idx}] {line[:200]}")

    if not definitions:
        return f"文档：{source}\n未提取到定义句。文档可能不包含典型的定义表述。"

    return f"文档：{source}\n提取到 {len(definitions)} 条定义：\n" + "\n".join(definitions[:20])


# ── 工具 5: get_adjacent_chunks ──────────────────────────────

@tool
def get_adjacent_chunks(
    source: str,
    chunk_index: int,
    direction: str = "both",
) -> str:
    """当检索到的文本片段不完整、被截断、或需要更多上下文时调用。
    根据搜索结果中的来源文件名和片段编号，获取相邻片段的文本。

    Args:
        source: 片段来源文件名，从搜索结果的 [来源: xxx] 中获取。
        chunk_index: 当前片段编号，从搜索结果的 [片段 #N] 中获取。
        direction: 获取方向。'prev'=前一片段, 'next'=后一片段, 'both'=前后各一段。
    """
    from skills.rag.collection import milvus_client, COLLECTION_NAME, init_collection

    uid = current_user_id.get()

    # direction normalize
    dir_map = {"previous": "prev", "backward": "prev", "forward": "next", "前后": "both"}
    direction = dir_map.get(direction, direction)
    if direction not in ("prev", "next", "both"):
        direction = "both"

    if chunk_index < 0:
        return "chunk_index 不能为负数。"

    # 计算目标 indices
    targets = []
    if direction in ("prev", "both"):
        if chunk_index - 1 >= 0:
            targets.append(chunk_index - 1)
    if direction in ("next", "both"):
        targets.append(chunk_index + 1)

    if not targets:
        return "已是文档第一段，无前序片段。"

    try:
        init_collection()
        filter_expr = (
            f'source == "{source}" and '
            f'(is_public == true or user_id == {uid}) and '
            f'chunk_index in [{", ".join(str(t) for t in targets)}]'
        )
        chunks = milvus_client.query(
            COLLECTION_NAME,
            filter=filter_expr,
            output_fields=["text", "source", "chunk_index", "document_id"],
            limit=len(targets),
        )
    except Exception as e:
        logger.error("get_adjacent_chunks query failed: %s", e)
        return f"查询相邻片段失败: {e}"

    if not chunks:
        return "未找到相邻片段，可能已是文档的首尾部分。"

    chunks.sort(key=lambda x: x.get("chunk_index", 0))
    parts = []
    for c in chunks:
        idx = c.get("chunk_index", "?")
        src = c.get("source", source)
        parts.append(f"[片段 #{idx}] 来源: {src}\n{c.get('text', '')}")

    return "\n\n".join(parts)


# ── 工具 6: list_documents ────────────────────────────────────

@tool
def list_documents(query: str) -> str:
    """列出当前用户上传的所有文档。

    适用场景：
    - 用户明确问"有哪些文档"、"上传了什么"、"文档列表"

    不适用场景：
    - 用户问文档内容 → 用 search_knowledge_base
    - 用户问文档结构 → 用 get_document_info
    """
    uid = current_user_id.get()
    logger.debug("list_documents called, uid=%s", uid)
    from config.database import SessionLocal
    from src.types.document import Document

    with SessionLocal() as db:
        docs = (
            db.query(Document)
            .filter(Document.user_id == uid)
            .order_by(Document.created_at.desc())
            .all()
        )

    if not docs:
        return "您目前没有上传任何文档。"

    status_map = {"ready": "已就绪", "processing": "处理中", "error": "处理失败"}
    lines = [f"共 {len(docs)} 份文档："]
    for i, doc in enumerate(docs, 1):
        status = status_map.get(doc.status, doc.status)
        lines.append(f"{i}. {doc.filename}（{status}）[ID: {doc.id}]")
    return "\n".join(lines)


# ── 工具集合 ──────────────────────────────────────────────────

tools = [get_current_time, search_knowledge_base, find_source, get_document_info, get_adjacent_chunks, list_documents]
