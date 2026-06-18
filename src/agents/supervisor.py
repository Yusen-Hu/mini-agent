import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from src.services.llm import llm
from config.logging import get_logger

logger = get_logger("supervisor")

SUPERVISOR_PROMPT = (
    "你是智能助手的前置路由器。按顺序完成以下任务：\n\n"
    "【任务一：路由判断】\n"
    "判断应交给哪个子助手处理：\n"
    "- general_chat：通用对话、闲聊、打招呼、常识问答、翻译、写作等\n"
    "- rag_agent：知识库检索、文档内容查询、技术问题、概念解释、文档结构/统计/定义\n"
    "- analysis_agent：深度分析、总结、摘要、对比某篇或多篇文档\n\n"
    "判断规则（用户有文档时）：\n"
    "- 明确是闲聊（打招呼、写诗、翻译、天气、闲谈）→ general_chat\n"
    "- 明确要求总结/摘要/对比/分析某篇文档 → analysis_agent\n"
    "- 其余所有问题 → rag_agent（含开放技术问题、通用知识问题、文档查询）\n\n"
    "判断规则（用户无文档时）：\n"
    "- 全走 general_chat\n\n"
    "【任务二：文档ID提取】\n"
    "document_ids 默认填 []，表示全库检索。仅当用户消息明确缩小范围时才填 ID：\n"
    "- 含指代词（那篇/这篇/它/刚才/上面）→ 结合最近 2 轮历史确定文档，从列表匹配\n"
    "- 含明确文档名或序号（\"第3篇\"、\"ID:173\"）→ 从文档列表匹配\n"
    "- 用户说\"所有/全部/这些文档\" → document_ids 必须为 []\n"
    "- 禁止仅因历史对话讨论过某文档就自动沿用其 ID\n"
    "- 当前消息无上述信号时，document_ids 填 []\n\n"
    "严格输出以下 JSON，不要输出任何其他内容：\n"
    '{"query": "标准化后的查询", "agent": "rag_agent", "document_ids": []}'
)

VALID_AGENTS = {"general_chat", "rag_agent", "analysis_agent"}

FAST_ROUTES: list[dict] = [
    {
        "pattern": r"(现在|当前|今天|今天是).*(时间|几点|日期|星期|几号)",
        "agent": "general_chat",
        "desc": "问时间/日期",
    },
]

FAST_ROUTE_HARD_LIMIT = 20


def route(message: str, history: list | None = None, agent_hint: str | None = None,
          doc_list_text: str | None = None) -> dict:
    """路由用户消息到对应的子 Agent。返回 {"query": str, "agent": str, "document_ids": list}。"""
    if agent_hint and agent_hint in VALID_AGENTS:
        logger.info("route: method=agent_hint, agent=%s", agent_hint)
        return {"query": message, "agent": agent_hint, "document_ids": [], "method": "agent_hint"}

    # 快速路径：正则匹配
    for rule in FAST_ROUTES:
        if re.search(rule["pattern"], message):
            logger.info("route: method=regex, agent=%s, rule=%s", rule["agent"], rule["desc"])
            return {"query": message, "agent": rule["agent"], "document_ids": [], "method": "regex_time"}

    # 快速路径：明确闲聊（有文档时跳过 LLM）
    if doc_list_text and doc_list_text != "无文档":
        _chitchat_pat = re.compile(
            r"^(你好|嗨|hello|hi|谢谢|再见|拜拜)|写.*诗|翻译成|天气|讲个笑话|闲聊一下"
        )
        if _chitchat_pat.search(message):
            logger.info("route: method=regex, agent=general_chat, rule=chitchat")
            return {"query": message, "agent": "general_chat", "document_ids": [], "method": "regex_chitchat"}

    # 快速路径：明确问文档列表
    _LIST_DOCS_PAT = re.compile(
        r"有哪些文档|有什么文档|上传了哪些|文档列表|我的文档|几个文档|文档都有|都有哪些"
    )
    if _LIST_DOCS_PAT.search(message):
        logger.info("route: method=regex_list_docs, agent=rag_agent, rule=list_documents")
        return {"query": message, "agent": "rag_agent", "document_ids": [], "method": "regex_list_docs"}

    # 慢路径：LLM 路由
    logger.info("route: method=llm")

    # 拼接用户消息和文档列表（仅 supervisor 可见）
    enriched_message = message
    if doc_list_text:
        enriched_message = f"{message}\n\n[当前用户的文档列表]\n{doc_list_text}"

    # 取最近 2 轮（4 条）历史，让 supervisor 理解指代
    context = history[-4:] if history else []
    response = llm.invoke(
        context + [
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=enriched_message),
        ]
    )

    text = response.content.strip()

    # 1. 尝试 JSON 解析（处理 LLM 可能包裹在 ```json ... ``` 中的情况）
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        if result.get("agent") in VALID_AGENTS:
            result.setdefault("document_ids", [])
            # 确保 document_ids 是字符串列表
            result["document_ids"] = [str(d) for d in result["document_ids"] if d]
            result["method"] = "llm"
            logger.info("route: method=llm, agent=%s, doc_ids=%s", result["agent"], result["document_ids"])
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # 2. JSON 失败 → 正则兜底提取 agent 名
    match = re.search(r"(general_chat|rag_agent|analysis_agent)", text)
    if match:
        logger.warning("route: method=llm_fallback, agent=%s (JSON parse failed)", match.group(1))
        return {"query": message, "agent": match.group(1), "document_ids": [], "method": "llm_fallback"}

    # 3. 真正降级
    fallback_agent = "rag_agent" if doc_list_text and doc_list_text != "无文档" else "general_chat"
    logger.warning("route: method=default_fallback, agent=%s (LLM response unparseable)", fallback_agent)
    return {"query": message, "agent": fallback_agent, "document_ids": [], "method": "default_fallback"}
