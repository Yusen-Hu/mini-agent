# Phase 4：Multi-Agent 多智能体协作 — 详细实施计划

## 架构

```
用户消息 → Supervisor（LLM 路由，~100 token）
    ├─ general_chat → 通用对话，无工具，直接 LLM 回答
    └─ rag_agent   → 文档检索，search_knowledge_base + list_documents + get_current_time
```

Phase 4 只做两个 Agent。AnalysisAgent 及相关工具（summarize_document、compare_documents、get_document_full_text）归入 Phase 4.5，等两个 Agent 稳定后再上。

## 成本影响

每次请求增加 1 次 Supervisor 调用（~100 token，约 0.001 元）。日均 100 次提问，月增约 3 元。

---

## Step 1：`src/types/session.py` — ChatMessage 加 agent_name 列

**做什么：** ChatMessage 增加 `agent_name` 字段（String(32), nullable），记录 assistant 消息由哪个 agent 处理。

```python
agent_name = Column(String(32), nullable=True)  # general_chat / rag_agent
```

**为什么第一步：** 后续所有步骤依赖此字段。nullable 保证向后兼容——旧消息无 agent_name 不影响读取，`create_all` 自动加列不需要数据迁移。

**验证：** 重启 uvicorn 确认无报错，手动查表确认列存在。

---

## Step 2：`src/types/chat.py` — 请求/响应模型扩展

**做什么：**

1. ChatRequest 增加 `agent_hint` 可选字段（调试用，允许前端强制指定 agent）
2. ChatResponse 增加 `agent` 字段（本次回答使用的 agent）
3. MessageResponse 增加 `agent_name` 可选字段（历史消息加载时前端需要）

```python
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    agent_hint: Optional[str] = None  # general_chat / rag_agent

class ChatResponse(BaseModel):
    reply: str
    session_id: str
    agent: str = "general_chat"

class MessageResponse(BaseModel):
    role: str
    content: str
    agent_name: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}
```

---

## Step 3：`src/agents/general_chat.py` — 通用对话 Agent（新建）

**做什么：** 不创建 ReAct Agent，直接封装 LLM 调用。通用对话不需要工具决策循环，`llm.invoke` 省一轮 ReAct 开销。

```python
from langchain_core.messages import SystemMessage
from src.services.llm import llm

GENERAL_SYSTEM_PROMPT = (
    "你是 Mini Agent，一个友好的 AI 助手。"
    "请自然、简洁地回答用户的问题。"
    "如果用户询问文档内容或知识库相关问题，"
    "请告知用户需要明确提问才能帮你检索。"
)


def chat(messages: list) -> str:
    """通用对话，无工具。messages 不含 SystemMessage，由本函数补。"""
    response = llm.invoke([SystemMessage(content=GENERAL_SYSTEM_PROMPT)] + messages)
    return response.content


async def chat_stream(messages: list):
    """流式通用对话。yield 文本片段。"""
    async for chunk in llm.astream(
        [SystemMessage(content=GENERAL_SYSTEM_PROMPT)] + messages
    ):
        if chunk.content:
            yield chunk.content
```

**设计点：**
- `messages` 参数不含 SystemMessage，各 agent 自己定义 system prompt
- 流式用 `llm.astream`，逐 token yield
- 约 25 行

**验证：** `chat([HumanMessage(content="你好")])` 返回自然回复，不触发工具。

---

## Step 4：`src/agents/rag_agent.py` — 重构为子 Agent

**做什么：**
1. 提取 `RAG_SYSTEM_PROMPT` 常量（从 `chat.py` 的 `SYSTEM_PROMPT` 迁移）
2. `tools` 列表保持不变（`get_current_time` + `search_knowledge_base` + `list_documents`）
3. 不加任何分析工具

```python
from langgraph.prebuilt import create_react_agent
from src.services.llm import llm
from src.agents.tools import tools

RAG_SYSTEM_PROMPT = (
    "你是 Mini Agent 的文档检索助手。你可以检索知识库回答用户问题，也可以获取当前时间。\n\n"
    "在使用知识库时，你必须遵守以下规则：\n"
    "1. 只基于检索到的内容回答，不得推测或补充知识库中没有的信息。"
    "如果检索结果中标记为\"参考\"或相似度较低，谨慎使用，"
    "宁可说\"相关内容不足\"也不要强行作答。\n"
    "2. 如果检索结果与用户问题无关，或知识库中没有相关文档，"
    "直接告知用户\"知识库中没有找到相关信息\"，不得编造。\n"
    "3. 回答时说明信息来源于哪个文档，不确定来源时不要猜测文档名称。\n\n"
    "不要因为历史对话中出现过的不确定表述影响当前判断，"
    "每次收到文档相关问题都应重新调用工具检索，"
    "不得沿用历史中的否定性结论。"
)

agent = create_react_agent(llm, tools)
```

**为什么把 system prompt 搬到这里：** 多 Agent 架构下，每个 agent 应拥有自己的 system prompt。`chat.py` 只做调度，不再持有 agent 级别的 prompt。

**验证：** 现有功能不变，文档检索和之前行为一致。

---

## Step 5：`src/agents/supervisor.py` — Supervisor 路由（新建）

**做什么：** 轻量级 LLM 调用，输出 JSON 路由结果。二分类（general_chat / rag_agent）。

```python
import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from src.services.llm import llm

SUPERVISOR_PROMPT = (
    "你是智能助手的前置路由器。完成两件事：\n"
    "1. 将用户输入标准化为清晰的查询语句（补全省略、展开缩写）\n"
    "2. 判断应交给哪个子助手处理\n\n"
    "子助手列表：\n"
    "- general_chat：通用对话、闲聊、打招呼、常识问答、翻译、写作等\n"
    "- rag_agent：用户询问已上传文档中的具体内容，需要检索知识库\n\n"
    "判断规则：\n"
    "- 如果用户没有明确提及文档或知识库内容，选 general_chat\n"
    "- 如果用户问\"文档里有什么\"、\"关于XX的资料\"、\"上传了什么\"，选 rag_agent\n"
    "- 如果问题模糊，优先选 general_chat\n\n"
    "严格输出以下 JSON，不要输出任何其他内容：\n"
    '{"query": "标准化后的查询", "agent": "general_chat"}'
)

VALID_AGENTS = {"general_chat", "rag_agent"}


def route(message: str, agent_hint: str | None = None) -> dict:
    """路由用户消息到对应的子 Agent。返回 {"query": str, "agent": str}。"""
    if agent_hint and agent_hint in VALID_AGENTS:
        return {"query": message, "agent": agent_hint}

    response = llm.invoke([
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=message),
    ])

    text = response.content.strip()

    # 1. 尝试 JSON 解析（处理 LLM 可能包裹在 ```json ... ``` 中的情况）
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        if result.get("agent") in VALID_AGENTS:
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # 2. JSON 失败 → 正则兜底提取 agent 名
    match = re.search(r'(general_chat|rag_agent)', text)
    if match:
        return {"query": message, "agent": match.group(1)}

    # 3. 真正降级
    return {"query": message, "agent": "general_chat"}
```

**降级策略（三级）：**
1. JSON 解析
2. 正则提取 agent 名（容错 LLM 输出格式偏差）
3. 降级 general_chat

**独立验证方法（硬性门控，不通过不接 chat.py）：**

```python
from src.agents.supervisor import route

# 典型测试用例
assert route("你好")["agent"] == "general_chat"
assert route("今天天气怎么样")["agent"] == "general_chat"
assert route("帮我找一下关于Python的文档")["agent"] == "rag_agent"
assert route("我的知识库里有什么")["agent"] == "rag_agent"
assert route("上传了哪些文件")["agent"] == "rag_agent"
assert route("你是谁")["agent"] == "general_chat"
assert route("文档里关于机器学习说了什么")["agent"] == "rag_agent"

# agent_hint 跳过路由
assert route("你好", agent_hint="rag_agent")["agent"] == "rag_agent"
```

路由不达标的调 prompt，达标后再进入 Step 6。

---

## Step 6：`src/services/chat.py` — 核心调度改造

### 6.1 删掉 SYSTEM_PROMPT 常量

当前 `chat.py` 顶部的 `SYSTEM_PROMPT` 已搬到 `rag_agent.py` 的 `RAG_SYSTEM_PROMPT`。删除 `chat.py` 中的 `SYSTEM_PROMPT` 定义。

### 6.2 _save_message 加 agent_name 默认参数

```python
def _save_message(db: Session, session_id_int: int, role: str, content: str, agent_name: str | None = None):
    msg = ChatMessage(session_id=session_id_int, role=role, content=content, agent_name=agent_name)
    db.add(msg)
    db.query(ChatSession).filter(ChatSession.id == session_id_int).update(
        {"updated_at": func.now()}, synchronize_session=False
    )
    db.commit()
```

默认值 `None` 保证旧的调用方不改也能工作。

### 6.3 chat() 改写

```python
def chat(message: str, session_id: str | None = None, user_id: int | None = None,
         db: Session = None, agent_hint: str | None = None) -> tuple:
    from src.agents.supervisor import route
    from src.agents.general_chat import chat as general_chat_fn
    from src.agents.rag_agent import agent as rag_agent, RAG_SYSTEM_PROMPT

    # 1. session 管理（不变）
    if session_id:
        session = _get_or_create_session(db, session_id, user_id or 0, message)
    else:
        new_uuid = uuid.uuid4()
        title = _sanitize_title(message)
        session = _create_session(db, new_uuid, user_id or 0, title)

    # 2. 加载历史
    history_msgs = _load_messages(db, session.id)

    # 3. Supervisor 路由
    routing = route(message, agent_hint)
    target_agent = routing["agent"]
    normalized_query = routing["query"]

    # 4. 组装消息（SystemMessage 由各 agent 自己补）
    messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]
    request_id = uuid.uuid4().hex
    current_user_id.set(user_id or 0)

    # 5. 分发
    if target_agent == "general_chat":
        reply = general_chat_fn(messages_no_system)
    else:  # rag_agent
        result = rag_agent.invoke(
            {"messages": [SystemMessage(content=RAG_SYSTEM_PROMPT)] + messages_no_system},
            config={"recursion_limit": 10, "configurable": {"request_id": request_id}},
        )
        reply = result["messages"][-1].content

    # 6. 保存
    _save_message(db, session.id, "user", message)
    _save_message(db, session.id, "assistant", reply, agent_name=target_agent)

    return reply, str(session.session_uuid), target_agent
```

返回值从 `(reply, session_uuid)` 变为 `(reply, session_uuid, agent_name)`。

### 6.4 chat_stream() 改写

```python
async def chat_stream(message: str, session_id: str | None = None,
                      user_id: int | None = None, agent_hint: str | None = None):
    from src.agents.supervisor import route
    from src.agents.general_chat import chat_stream as general_stream
    from src.agents.rag_agent import agent as rag_agent, RAG_SYSTEM_PROMPT

    # 第一个 with 块：获取/创建 session、加载历史、保存用户消息
    with SessionLocal() as db:
        if session_id:
            session = _get_or_create_session(db, session_id, user_id or 0, message)
        else:
            new_uuid = uuid.uuid4()
            title = _sanitize_title(message)
            session = _create_session(db, new_uuid, user_id or 0, title)

        db_session_id = session.id
        db_session_uuid = str(session.session_uuid)
        history_msgs = _load_messages(db, db_session_id)
        _save_message(db, db_session_id, "user", message)

    # Supervisor 路由（在 with 块外，因为不需要 db）
    routing = route(message, agent_hint)
    target_agent = routing["agent"]
    normalized_query = routing["query"]
    messages_no_system = history_msgs + [HumanMessage(content=normalized_query)]

    # 通知前端当前 agent
    yield f"data: {json.dumps({'type': 'agent', 'agent': target_agent})}\n\n"

    # 流式分发
    full_reply = ""
    request_id = uuid.uuid4().hex
    current_user_id.set(user_id or 0)

    try:
        if target_agent == "general_chat":
            async for text in general_stream(messages_no_system):
                full_reply += text
                yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"
        else:  # rag_agent
            all_msgs = [SystemMessage(content=RAG_SYSTEM_PROMPT)] + messages_no_system
            async for chunk in rag_agent.astream(
                {"messages": all_msgs},
                config={"recursion_limit": 10, "configurable": {"request_id": request_id}},
                stream_mode="messages",
            ):
                msg, metadata = chunk
                if hasattr(msg, "content") and msg.content and metadata.get("langgraph_node") == "agent":
                    text = msg.content
                    if isinstance(text, str):
                        full_reply += text
                        yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"
    except Exception as e:
        print(f"流式生成异常 [session={db_session_uuid}, agent={target_agent}]: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': '服务暂时异常，请稍后重试'}, ensure_ascii=False)}\n\n"
    finally:
        if full_reply:
            with SessionLocal() as save_db:
                _save_message(save_db, db_session_id, "assistant", full_reply, agent_name=target_agent)

    # session_uuid
    yield f"data: {json.dumps({'type': 'session', 'session_id': db_session_uuid})}\n\n"

    # citations（仅 RAG Agent）
    if target_agent == "rag_agent":
        from src.agents.tools import get_citations
        citations = get_citations(request_id)
        if citations:
            yield f"data: {json.dumps({'type': 'citation', 'schema_version': 1, 'items': citations}, ensure_ascii=False)}\n\n"

    yield 'data: {"type": "done"}\n\n'
```

---

## Step 7：`src/api/routers/chat_router.py` — 传递 agent_hint + agent 响应

```python
@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reply, session_uuid, agent_name = chat(
        req.message, req.session_id,
        user_id=current_user.id, db=db, agent_hint=req.agent_hint,
    )
    return ChatResponse(reply=reply, session_id=session_uuid, agent=agent_name)


@router.post("/chat/stream")
async def chat_stream_endpoint(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    return StreamingResponse(
        chat_stream(req.message, req.session_id, user_id=current_user.id, agent_hint=req.agent_hint),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## Step 8：前端适配

### 8.1 `frontend/src/stores/chat.js` — SSE agent 事件

在 switch 里加 agent case（放在 token case 前面）：

```javascript
case 'agent':
  messages.value[botIdx].agent = data.agent
  break
```

`loadSession` 映射历史消息时带上 `agent` 字段（依赖后端 MessageResponse 返回 agent_name）：

```javascript
messages.value = data.messages.map(m => ({
  role: m.role === 'user' ? 'user' : 'ai',
  content: m.content,
  agent: m.agent_name || undefined,
}))
```

### 8.2 `frontend/src/components/MessageList.vue` — Agent 标签

AI 气泡上方加标签：

```html
<div v-if="msg.role === 'ai' && msg.agent" class="agent-badge">
  {{ agentLabel(msg.agent) }}
</div>
```

```javascript
const AGENT_LABELS = { general_chat: '通用对话', rag_agent: '文档检索' }
function agentLabel(agent) { return AGENT_LABELS[agent] || agent }
```

```css
.agent-badge {
  font-size: 11px;
  color: #888;
  margin-bottom: 2px;
  padding-left: 48px;
}
```

---

## 文件变更总览

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/types/session.py` | 修改 | ChatMessage 加 1 列 |
| `src/types/chat.py` | 修改 | 3 个模型各加 1 字段 |
| `src/agents/general_chat.py` | 新建 | ~25 行 |
| `src/agents/rag_agent.py` | 重构 | 提取 RAG_SYSTEM_PROMPT，~10 行改动 |
| `src/agents/supervisor.py` | 重写 | 替换占位注释，~50 行 |
| `src/services/chat.py` | 重构 | 删 SYSTEM_PROMPT + 调度改造，~60 行改动 |
| `src/api/routers/chat_router.py` | 修改 | ~5 行改动 |
| `frontend/src/stores/chat.js` | 修改 | ~5 行 |
| `frontend/src/components/MessageList.vue` | 修改 | ~15 行 |

共 9 个文件。不创建 `analysis_agent.py`，不改 `ingestion.py`，不改 `tools.py`。

---

## 执行顺序

```
Step 1 (agent_name 列)  ──┐
Step 2 (agent_hint 字段) ─┤ 可并行
                           ↓
Step 3 (general_chat.py) ─┐
Step 4 (rag_agent.py)    ─┤ 可并行
                           ↓
Step 5 (supervisor.py)    ← 依赖 Step 3/4 存在
  ↓
  ┌─ 独立验证：路由准确率测试 ─┐
  │  不达标 → 调 prompt，重测    │
  │  达标 → 继续                │
  └────────────────────────────┘
  ↓
Step 6 (chat.py 核心改造)  ← 依赖 Step 1+5
  ↓
Step 7 (chat_router.py)    ← 依赖 Step 2+6
  ↓
Step 8 (前端适配)          ← 依赖 Step 7
```

Supervisor 路由验证是硬性门控——路由不准不接 chat.py。

---

## 关于 Supervisor 延迟

当前前端已有 `thinking: true` 状态（三个跳动的点）。Supervisor 调用期间用户看到的就是 thinking 动画，体验上等价于"正在理解"。`agent` 事件到达后前端设 agent 标签，`token` 事件到达后取消 thinking。

如需区分"路由中"和"生成中"，后续可在 SSE 加 `{"type": "routing"}` 事件，前端收到后显示"正在理解问题…"。这是体验优化，不影响功能。

---

## Phase 4.5 预留：AnalysisAgent

等两个 Agent 稳定后，Phase 4.5 加第三条路径：

- `skills/rag/ingestion.py` 加 `get_document_full_text()`
- `src/agents/tools.py` 加 `summarize_document`、`compare_documents`
- `src/agents/analysis_agent.py` 实现
- `supervisor.py` 路由加 `analysis_agent` 选项 + prompt 更新
- `chat.py` 分发加 `elif target_agent == "analysis_agent"` 分支
- `MessageList.vue` 加第三个标签

改动集中在工具层和 supervisor prompt，chat.py 只需加一个 elif，不大规模重构。
