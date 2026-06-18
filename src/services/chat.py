import os
import re
import uuid
import json
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.errors import GraphRecursionError

from config.settings import settings
from config.logging_context import current_user_id, current_session_id
from config.logging import get_logger
from src.types.session import ChatSession, ChatMessage

logger = get_logger("chat")

# ── doc_ids 后处理：防止 Supervisor 提取到错误或过时的 doc_ids ──────────────

_CLEAR_ALL_PAT = re.compile(r"所有|全部|每一篇|各篇|这些文档|那些文档|我上传的|上传的所有|所有文档")
_REFERENTIAL_PAT = re.compile(r"那篇|这篇|该篇|它|刚才|上面|之前那|上次那|第\d+篇|ID[:\s]*\d+")
_DEGRADE_MARKER = "未在知识库中找到相关内容"


def _postprocess_routing(message: str, routing: dict, doc_list_text: str) -> dict:
    """doc_ids 后处理：硬编码清空 + 切断上下文污染 + agent override。"""
    doc_ids = routing.get("document_ids", [])
    if doc_ids:
        # 规则 1：用户说"所有/全部" → 强制清空（全库检索）
        if _CLEAR_ALL_PAT.search(message):
            routing["document_ids"] = []
            logger.info("postprocess: cleared doc_ids (matched 'all' pattern)")
            return routing

        # 规则 2：无指代词 → 清空（切断上一轮 doc_ids 污染）
        if not _REFERENTIAL_PAT.search(message):
            routing["document_ids"] = []
            logger.info("postprocess: cleared doc_ids (no referential words)")

    # 规则 3：有文档时，LLM 漏判或 fallback 到 general_chat → 强制 rag_agent
    _OVERRIDE_METHODS = {"llm", "llm_fallback", "default_fallback"}
    if (
        doc_list_text != "无文档"
        and routing.get("agent") == "general_chat"
        and routing.get("method") in _OVERRIDE_METHODS
    ):
        routing["agent"] = "rag_agent"
        logger.info("postprocess: override general_chat → rag_agent (method=%s)", routing.get("method"))

    return routing


def _sanitize_title(text: str, max_len: int = 20) -> str:
    """清洗首条消息作为会话标题：去换行、collapse 空格、截取。"""
    import re
    cleaned = re.sub(r"\s+", " ", text.strip())
    return cleaned[:max_len] if cleaned else "新会话"


def _get_or_create_session(db: Session, session_uuid_str: str, user_id: int, first_message: str = "") -> ChatSession:
    """通过 UUID 获取会话，验证用户所有权。找不到则创建新会话。"""
    try:
        target_uuid = uuid.UUID(session_uuid_str)
    except ValueError:
        raise ValueError("无效的 session_id 格式")

    session = db.query(ChatSession).filter(
        ChatSession.session_uuid == target_uuid,
    ).first()

    if session:
        if session.user_id != user_id:
            raise PermissionError("无权访问该会话")
        return session

    # 找不到 → 创建新会话
    title = _sanitize_title(first_message) if first_message else "新会话"
    session = ChatSession(session_uuid=target_uuid, user_id=user_id, title=title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _create_session(db: Session, session_uuid: uuid.UUID, user_id: int, title: str) -> ChatSession:
    """创建新会话。"""
    session = ChatSession(session_uuid=session_uuid, user_id=user_id, title=title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _load_messages(db: Session, session_id_int: int) -> list:
    """从 DB 加载最近 N 条消息，转为 LangChain 消息对象。按时间正序返回。
    双重截断：先按条数限制，再按 token 数限制，取保守值。
    """
    import tiktoken

    limit = settings.CHAT_HISTORY_LIMIT * 2  # 每轮 2 条（user + assistant）
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id_int)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()  # 恢复时间正序

    messages = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            messages.append(AIMessage(content=row.content))

    # Token 截断：从头部丢弃消息，直到总 token 数 <= MAX_HISTORY_TOKENS
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        total_tokens = sum(len(enc.encode(m.content)) for m in messages)
        while total_tokens > settings.MAX_HISTORY_TOKENS and len(messages) > 1:
            removed = messages.pop(0)
            total_tokens -= len(enc.encode(removed.content))
    except Exception as e:
        logger.warning("tiktoken 截断失败，降级为仅条数截断: %s", e)

    return messages


def _save_message(db: Session, session_id_int: int, role: str, content: str,
                  agent_name: str | None = None, extra_data: dict | None = None):
    """保存消息到 DB，并更新 session 的 updated_at。"""
    msg = ChatMessage(session_id=session_id_int, role=role, content=content,
                      agent_name=agent_name, extra_data=extra_data)
    db.add(msg)
    # 更新 session 的 updated_at
    db.query(ChatSession).filter(ChatSession.id == session_id_int).update(
        {"updated_at": func.now()}, synchronize_session=False
    )
    db.commit()


def chat(message: str, session_id: str | None = None, user_id: int | None = None,
         db: Session = None, agent_hint: str | None = None) -> tuple:
    """同步对话。返回 (reply, session_uuid_str, agent_name)。"""
    from src.agents.supervisor import route
    from src.agents.general_chat import chat as general_chat_fn
    from src.agents.rag_agent import agent as rag_agent, RAG_SYSTEM_PROMPT

    # 1. 获取或创建 session
    if session_id:
        session = _get_or_create_session(db, session_id, user_id or 0, message)
    else:
        new_uuid = uuid.uuid4()
        title = _sanitize_title(message)
        session = _create_session(db, new_uuid, user_id or 0, title)

    # 2. 加载历史消息（截断）
    history_msgs = _load_messages(db, session.id)

    # 3. 构建文档列表（给 supervisor 做 ID 提取用）
    from src.types.document import Document as DocModel
    docs = (
        db.query(DocModel)
        .filter(DocModel.user_id == (user_id or 0))
        .order_by(DocModel.created_at.desc())
        .all()
    )
    if docs:
        lines = []
        for i, doc in enumerate(docs, 1):
            lines.append(f"{i}. {doc.filename} [ID: {doc.id}]")
        doc_list_text = "\n".join(lines)
    else:
        doc_list_text = "无文档"

    # 4. 注入请求上下文（必须在 Supervisor 路由之前）
    current_user_id.set(user_id or 0)
    current_session_id.set(str(session.session_uuid))

    # 5. Supervisor 路由
    routing = route(message, history_msgs, agent_hint, doc_list_text=doc_list_text)
    routing = _postprocess_routing(message, routing, doc_list_text)
    target_agent = routing["agent"]
    normalized_query = routing["query"]

    # 5.1 Query Rewriting（条件触发：指代词/口语化 → 拼入文件名提升 BM25 匹配）
    if target_agent != "general_chat" and routing.get("document_ids"):
        _rewrite_triggers = [r"[那这它哪]篇", r"上面|之前|刚才|上次", r"讲[了的]?|聊[了的]?|说[了的]?"]
        if any(re.search(p, normalized_query) for p in _rewrite_triggers):
            from src.types.document import Document as _DocModel
            doc_names = []
            for did in routing["document_ids"]:
                _doc = db.query(_DocModel).filter(_DocModel.id == int(did)).first()
                if _doc:
                    doc_names.append(os.path.splitext(_doc.filename)[0])
            if doc_names:
                normalized_query = " ".join(doc_names) + " " + normalized_query
                logger.info("query_rewrite: prepended %s", doc_names)

    # 组装消息（SystemMessage 由各 agent 自己补）
    messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]
    request_id = uuid.uuid4().hex

    # 5. 分发到子 Agent
    citations = None
    if target_agent == "general_chat":
        reply = general_chat_fn(messages_no_system)
    elif target_agent == "analysis_agent":
        from src.services.llm import llm
        from src.agents.analysis_agent import ANALYSIS_SYSTEM_PROMPT, COMPARE_SYSTEM_PROMPT
        from src.agents.tools import _citations_store
        from skills.rag.ingestion import get_document_full_text as _get_text
        from config.database import SessionLocal
        from src.types.document import Document

        doc_ids = routing.get("document_ids", [])

        # 关键词降级
        if not doc_ids:
            with SessionLocal() as sdb:
                docs = (
                    sdb.query(Document)
                    .filter(Document.user_id == (user_id or 0))
                    .order_by(Document.created_at.desc())
                    .all()
                )
            keywords = re.findall(r'[一-鿿]{2,}', normalized_query)
            matched = []
            for doc in docs:
                for kw in keywords:
                    if kw in doc.filename:
                        matched.append(str(doc.id))
                        break
            if len(matched) == 1:
                doc_ids = matched

        if not doc_ids:
            reply = "抱歉，我无法确定您想查看哪篇文档，请明确指定文档名称。"
        else:
            from src.utils.truncation import smart_truncate, MIN_PER_DOC
            is_compare = len(doc_ids) > 1
            doc_contents = []
            citations_list = []
            per_doc_budget = max(settings.ANALYSIS_CHAR_BUDGET // len(doc_ids), MIN_PER_DOC) if len(doc_ids) > 1 else settings.ANALYSIS_CHAR_BUDGET
            for doc_id in doc_ids:
                try:
                    full_text, source = _get_text(doc_id, user_id or 0)
                    original_len = len(full_text)
                    truncated_text = smart_truncate(full_text, doc_id, budget=per_doc_budget)
                    if original_len > per_doc_budget:
                        truncated_text += f"\n\n[提示：文档全文 {original_len} 字，已截取前 {per_doc_budget} 字]"
                    doc_contents.append(f"=== 文档：{source} ===\n\n{truncated_text}")
                    citations_list.append({
                        "document_id": doc_id,
                        "filename": source,
                        "chunk_index": None,
                        "rrf_score": 1.0,
                        "relevance_label": "主文档",
                        "snippet": truncated_text[:200],
                        "retrieval_method": "full_text",
                    })
                except ValueError as e:
                    doc_contents.append(f"=== 文档 ID {doc_id} 获取失败：{e} ===")

            if citations_list:
                _citations_store[request_id] = citations_list

            combined_content = "\n\n".join(doc_contents)
            system_prompt = COMPARE_SYSTEM_PROMPT if is_compare else ANALYSIS_SYSTEM_PROMPT
            if is_compare:
                human_content = f"{normalized_query}\n\n以下是待对比的文档全文：\n\n{combined_content}"
            else:
                human_content = f"{normalized_query}\n\n以下是文档全文，请基于此内容回答：\n\n{combined_content}"
            analysis_messages = (
                [SystemMessage(content=system_prompt)]
                + history_msgs
                + [HumanMessage(content=human_content)]
            )
            response = llm.invoke(analysis_messages)
            reply = response.content
    else:  # rag_agent
        doc_ids = routing.get("document_ids", [])

        # ── 预检索：确定性搜索，不依赖 ReAct 工具调用 ──
        skip_pre_retrieval = routing.get("method") == "regex_list_docs"
        if skip_pre_retrieval:
            logger.info("pre-retrieval: skipped (list_documents intent)")
            messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]
        else:
            from skills.rag.retrieval import search_documents
            try:
                pre_results = search_documents(
                    normalized_query,
                    user_id=user_id or 0,
                    document_ids=doc_ids if doc_ids else None,
                )
            except Exception as e:
                logger.warning("pre-retrieval failed: %s", e)
                pre_results = []

            from src.agents.tools import _store_citations
            if pre_results:
                _store_citations(request_id, pre_results)
                context_parts = []
                for i, r in enumerate(pre_results, 1):
                    context_parts.append(f"【片段{i}】来源：{r.get('source', '未知')}\n{r['text']}")
                context_text = "\n\n".join(context_parts)
                human_content = (
                    f"[已检索到的相关内容]\n{context_text}\n\n"
                    f"[用户问题]\n{normalized_query}"
                )
                logger.info("pre-retrieval: %d results injected", len(pre_results))
            else:
                human_content = (
                    "[已检索到的相关内容]\n"
                    "（知识库中没有找到相关内容）\n\n"
                    f"[用户问题]\n{normalized_query}"
                )
                logger.info("pre-retrieval: 0 results, empty marker injected")
            messages_no_system = history_msgs + [HumanMessage(content=human_content)]

        try:
            result = rag_agent.invoke(
                {"messages": [SystemMessage(content=RAG_SYSTEM_PROMPT)] + messages_no_system},
                config={"recursion_limit": 12, "configurable": {
                    "request_id": request_id,
                    "document_ids": doc_ids,
                }},
            )
            reply = result["messages"][-1].content
        except GraphRecursionError:
            logger.warning("rag_agent recursion_limit reached [session=%s]", session_uuid)
            reply = "问题较复杂，请尝试简化后重新提问。"
        from src.agents.tools import get_citations
        citations = get_citations(request_id)
        if citations and _DEGRADE_MARKER in reply:
            citations = None
            logger.info("citations cleared: degraded response detected")

    # 6. 保存到 DB
    _save_message(db, session.id, "user", message)
    extra = {"citations": citations} if citations else None
    _save_message(db, session.id, "assistant", reply, agent_name=target_agent, extra_data=extra)

    return reply, str(session.session_uuid), target_agent


async def chat_stream(message: str, session_id: str | None = None,
                      user_id: int | None = None, agent_hint: str | None = None):
    """流式对话。内部独立管理 DB session 生命周期。"""
    from config.database import SessionLocal
    from src.agents.supervisor import route
    from src.agents.general_chat import chat_stream as general_stream
    from src.agents.rag_agent import agent as rag_agent, RAG_SYSTEM_PROMPT

    # 第一个独立 db：获取 session、加载历史、保存用户消息
    with SessionLocal() as db:
        if session_id:
            session = _get_or_create_session(db, session_id, user_id or 0, message)
        else:
            new_uuid = uuid.uuid4()
            title = _sanitize_title(message)
            session = _create_session(db, new_uuid, user_id or 0, title)

        # 在 with 块内提取纯值，避免 ORM 对象 detach 后访问
        db_session_id = session.id
        db_session_uuid = str(session.session_uuid)

        history_msgs = _load_messages(db, db_session_id)

        # 先保存用户消息
        _save_message(db, db_session_id, "user", message)

        # 构建文档列表（给 supervisor 做 ID 提取用）
        from src.types.document import Document as DocModel
        docs = (
            db.query(DocModel)
            .filter(DocModel.user_id == (user_id or 0))
            .order_by(DocModel.created_at.desc())
            .all()
        )
        if docs:
            lines = []
            for i, doc in enumerate(docs, 1):
                lines.append(f"{i}. {doc.filename} [ID: {doc.id}]")
            doc_list_text = "\n".join(lines)
        else:
            doc_list_text = "无文档"

    # 注入请求上下文（必须在 Supervisor 路由之前）
    current_user_id.set(user_id or 0)
    current_session_id.set(db_session_uuid)

    # Supervisor 路由
    routing = route(message, history_msgs, agent_hint, doc_list_text=doc_list_text)
    routing = _postprocess_routing(message, routing, doc_list_text)
    target_agent = routing["agent"]
    normalized_query = routing["query"]

    # Query Rewriting（条件触发：指代词/口语化 → 拼入文件名提升 BM25 匹配）
    if target_agent != "general_chat" and routing.get("document_ids"):
        _rewrite_triggers = [r"[那这它哪]篇", r"上面|之前|刚才|上次", r"讲[了的]?|聊[了的]?|说[了的]?"]
        if any(re.search(p, normalized_query) for p in _rewrite_triggers):
            from src.types.document import Document as _DocModel
            with SessionLocal() as sdb:
                doc_names = []
                for did in routing["document_ids"]:
                    _doc = sdb.query(_DocModel).filter(_DocModel.id == int(did)).first()
                    if _doc:
                        doc_names.append(os.path.splitext(_doc.filename)[0])
            if doc_names:
                normalized_query = " ".join(doc_names) + " " + normalized_query
                logger.info("query_rewrite: prepended %s", doc_names)

    messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]

    # 通知前端当前 agent
    yield f"data: {json.dumps({'type': 'agent', 'agent': target_agent})}\n\n"

    # 流式分发
    full_reply = ""
    citations = None
    request_id = uuid.uuid4().hex
    try:
        if target_agent == "general_chat":
            async for text in general_stream(messages_no_system):
                full_reply += text
                yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"
        elif target_agent == "analysis_agent":
            from src.services.llm import llm
            from src.agents.analysis_agent import ANALYSIS_SYSTEM_PROMPT, COMPARE_SYSTEM_PROMPT
            from src.agents.tools import _citations_store
            from skills.rag.ingestion import get_document_full_text as _get_text
            from config.database import SessionLocal
            from src.types.document import Document

            # 取 supervisor 返回的 document_ids
            doc_ids = routing.get("document_ids", [])

            # 关键词降级：如果 supervisor 没提取到 ID，用文件名匹配
            if not doc_ids:
                with SessionLocal() as db:
                    docs = (
                        db.query(Document)
                        .filter(Document.user_id == (user_id or 0))
                        .order_by(Document.created_at.desc())
                        .all()
                    )
                # 从用户消息中提取中文关键词
                keywords = re.findall(r'[一-鿿]{2,}', normalized_query)
                matched = []
                for doc in docs:
                    for kw in keywords:
                        if kw in doc.filename:
                            matched.append(str(doc.id))
                            break
                if len(matched) == 1:
                    doc_ids = matched

            if not doc_ids:
                full_reply = "抱歉，我无法确定您想查看哪篇文档，请明确指定文档名称。"
                yield f"data: {json.dumps({'type': 'token', 'content': full_reply}, ensure_ascii=False)}\n\n"
            else:
                # 强制获取全文（代码控制，不依赖 Agent）
                from src.utils.truncation import smart_truncate, MIN_PER_DOC
                is_compare = len(doc_ids) > 1
                doc_contents = []
                citations_list = []
                per_doc_budget = max(settings.ANALYSIS_CHAR_BUDGET // len(doc_ids), MIN_PER_DOC) if len(doc_ids) > 1 else settings.ANALYSIS_CHAR_BUDGET
                for doc_id in doc_ids:
                    try:
                        full_text, source = _get_text(doc_id, user_id or 0)
                        original_len = len(full_text)
                        truncated_text = smart_truncate(full_text, doc_id, budget=per_doc_budget)
                        if original_len > per_doc_budget:
                            truncated_text += f"\n\n[提示：文档全文 {original_len} 字，已截取前 {per_doc_budget} 字]"
                        doc_contents.append(f"=== 文档：{source} ===\n\n{truncated_text}")
                        citations_list.append({
                            "document_id": doc_id,
                            "filename": source,
                            "chunk_index": None,
                            "rrf_score": 1.0,
                            "relevance_label": "主文档",
                            "snippet": truncated_text[:200],
                            "retrieval_method": "full_text",
                        })
                    except ValueError as e:
                        doc_contents.append(f"=== 文档 ID {doc_id} 获取失败：{e} ===")

                # 写入 citations
                if citations_list:
                    _citations_store[request_id] = citations_list

                # LLM 只负责生成分析（直接调 llm，不走 ReAct Agent）
                combined_content = "\n\n".join(doc_contents)
                system_prompt = COMPARE_SYSTEM_PROMPT if is_compare else ANALYSIS_SYSTEM_PROMPT
                if is_compare:
                    human_content = f"{normalized_query}\n\n以下是待对比的文档全文：\n\n{combined_content}"
                else:
                    human_content = f"{normalized_query}\n\n以下是文档全文，请基于此内容回答：\n\n{combined_content}"
                analysis_messages = (
                    [SystemMessage(content=system_prompt)]
                    + history_msgs
                    + [HumanMessage(content=human_content)]
                )
                async for chunk in llm.astream(analysis_messages):
                    if chunk.content:
                        text = chunk.content
                        full_reply += text
                        yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"
        else:  # rag_agent
            doc_ids = routing.get("document_ids", [])

            # ── 预检索：确定性搜索，不依赖 ReAct 工具调用 ──
            skip_pre_retrieval = routing.get("method") == "regex_list_docs"
            if skip_pre_retrieval:
                logger.info("pre-retrieval: skipped (list_documents intent)")
                messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]
            else:
                from skills.rag.retrieval import search_documents
                try:
                    pre_results = search_documents(
                        normalized_query,
                        user_id=user_id or 0,
                        document_ids=doc_ids if doc_ids else None,
                    )
                except Exception as e:
                    logger.warning("pre-retrieval failed: %s", e)
                    pre_results = []

                from src.agents.tools import _store_citations
                if pre_results:
                    _store_citations(request_id, pre_results)
                    context_parts = []
                    for i, r in enumerate(pre_results, 1):
                        context_parts.append(f"【片段{i}】来源：{r.get('source', '未知')}\n{r['text']}")
                    context_text = "\n\n".join(context_parts)
                    human_content = (
                        f"[已检索到的相关内容]\n{context_text}\n\n"
                        f"[用户问题]\n{normalized_query}"
                    )
                    logger.info("pre-retrieval: %d results injected", len(pre_results))
                else:
                    human_content = (
                        "[已检索到的相关内容]\n"
                        "（知识库中没有找到相关内容）\n\n"
                        f"[用户问题]\n{normalized_query}"
                    )
                    logger.info("pre-retrieval: 0 results, empty marker injected")
                messages_no_system = history_msgs + [HumanMessage(content=human_content)]

            all_msgs = [SystemMessage(content=RAG_SYSTEM_PROMPT)] + messages_no_system
            pending_tool_name = ""
            async for chunk in rag_agent.astream(
                {"messages": all_msgs},
                config={"recursion_limit": 12, "configurable": {
                    "request_id": request_id,
                    "document_ids": doc_ids,
                }},
                stream_mode="messages",
            ):
                msg, metadata = chunk
                node = metadata.get("langgraph_node")

                # agent 节点：捕获工具名（AIMessage.tool_calls）
                if node == "agent" and hasattr(msg, "tool_calls") and msg.tool_calls:
                    pending_tool_name = msg.tool_calls[0].get("name", "")

                # tools 节点：用记住的工具名推送 tool_start 事件
                if node == "tools" and hasattr(msg, "content") and msg.content:
                    tool_name = pending_tool_name
                    pending_tool_name = ""
                    tool_labels = {
                        "search_knowledge_base": "正在搜索知识库...",
                        "find_source": "正在定位原文来源...",
                        "get_document_info": "正在读取文档信息...",
                        "list_documents": "正在获取文档列表...",
                        "get_current_time": "正在获取当前时间...",
                        "get_adjacent_chunks": "正在获取相邻片段...",
                    }
                    label = tool_labels.get(tool_name, f"正在调用 {tool_name}...")
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'label': label}, ensure_ascii=False)}\n\n"
                    continue

                # Agent 最终输出
                if node == "agent" and hasattr(msg, "content") and msg.content:
                    text = msg.content
                    if isinstance(text, str):
                        full_reply += text
                        yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"
    except GraphRecursionError:
        logger.warning("rag_agent recursion_limit reached [session=%s]", db_session_uuid)
        yield f"data: {json.dumps({'type': 'error', 'message': '问题较复杂，请尝试简化后重新提问'}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error("流式生成异常 [session=%s, agent=%s]: %s", db_session_uuid, target_agent, e)
        yield f"data: {json.dumps({'type': 'error', 'message': '服务暂时异常，请稍后重试'}, ensure_ascii=False)}\n\n"
    finally:
        # 获取 citations（所有 Agent 路径，失败不影响保存）
        try:
            from src.agents.tools import get_citations
            citations = get_citations(request_id)
            if citations and _DEGRADE_MARKER in full_reply:
                citations = None
                logger.info("citations cleared: degraded response detected")
        except Exception:
            pass
        if target_agent == "analysis_agent" and not citations and full_reply:
            logger.warning("analysis_agent 未写入 citations，Agent 可能跳过了工具调用")
        # 保存 AI 回复（含 extra_data，断连也能存已生成的部分）
        if full_reply:
            extra = {"citations": citations} if citations else None
            with SessionLocal() as save_db:
                _save_message(save_db, db_session_id, "assistant", full_reply,
                              agent_name=target_agent, extra_data=extra)

    # 把 session_uuid 传给前端
    yield f"data: {json.dumps({'type': 'session', 'session_id': db_session_uuid})}\n\n"

    # 发送引用（所有 Agent 路径）
    if citations:
        citation_event = {
            "type": "citation",
            "schema_version": 1,
            "items": citations,
        }
        yield f"data: {json.dumps(citation_event, ensure_ascii=False)}\n\n"

    yield 'data: {"type": "done"}\n\n'
