from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
from src.services.llm import llm
from src.agents.tools import get_current_time

GENERAL_SYSTEM_PROMPT = (
    "你是 Mini Agent，一个友好的 AI 助手。"
    "请自然、简洁地回答用户的问题。"
    "如果用户问的是技术或专业问题，先用你的知识回答；"
    "同时可提示：\"如需从知识库检索，可问：文档里关于 XX 的内容\"。"
    "不要说\"我无法访问你的文档或文件\"。"
)

_agent = create_react_agent(llm, [get_current_time])


def chat(messages: list) -> str:
    """通用对话，含 get_current_time 工具。messages 不含 SystemMessage，由本函数补。"""
    result = _agent.invoke(
        {"messages": [SystemMessage(content=GENERAL_SYSTEM_PROMPT)] + messages},
        config={"recursion_limit": 7},
    )
    return result["messages"][-1].content


async def chat_stream(messages: list):
    """流式通用对话。yield 文本片段。"""
    all_msgs = [SystemMessage(content=GENERAL_SYSTEM_PROMPT)] + messages
    async for chunk in _agent.astream(
        {"messages": all_msgs},
        config={"recursion_limit": 7},
        stream_mode="messages",
    ):
        msg, metadata = chunk
        if (
            hasattr(msg, "content")
            and msg.content
            and not getattr(msg, "tool_calls", None)
            and metadata.get("langgraph_node") == "agent"
        ):
            text = msg.content
            if isinstance(text, str):
                yield text
