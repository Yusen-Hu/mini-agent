# Phase 8：质量与稳定性 — 升级方案

## 背景

Phase 1-7 已完成（认证、文档管理、RAG 混合检索、Multi-Agent、会话持久化、前端、Docker 部署）。
当前存在 Agent 行为不可观测、长文档触发 Moonshot content_filter、缺少管理员权限体系等问题。
本方案分 4 个 Task 解决这些问题，总计约 1.5-2 天。

---

## 分步计划

### Task 1：LangSmith + 结构化日志（约 1 天）

**目标：** 同一次请求在 LangSmith trace 和本地日志里能通过 run_id 关联。调试 Agent 行为有两条互补的观测线。

#### 1.1 日志 Schema

```json
{
  "timestamp": "2026-05-22T10:00:00Z",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": 9,
  "run_id": "langsmith_run_id_or_null",
  "agent_type": "rag_agent",
  "tool_called": "search_knowledge_base",
  "latency_ms": 1240,
  "token_usage": { "input": 312, "output": 89 },
  "level": "INFO"
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| timestamp | ISO 8601 | 日志时间 |
| session_id | UUID string | 会话 ID，从 contextvars 读取 |
| user_id | int | 用户 ID，从 contextvars 读取，与 User ORM 的 Integer 类型一致 |
| run_id | string or null | LangSmith run ID，可选项。LangSmith 已配置时从 callback 捕获，未配置时为 null，不影响日志输出 |
| agent_type | string | 当前处理的 Agent：general_chat / rag_agent / analysis_agent |
| tool_called | string or null | 本次调用中触发的工具名，无工具调用时为 null |
| latency_ms | int | 请求耗时（毫秒），从 chat 入口到结束计时 |
| token_usage | object or null | {input, output}，Moonshot API 返回 usage 时填充，无则 null |
| level | string | INFO / ERROR |

#### 1.2 实现步骤

**Step 1.1 — `config/logging.py`（新建）**

- Python 标准 `logging` 模块 + 自定义 JSON formatter，不引入 structlog 以减少依赖
- JSON formatter 读 contextvars 中的 `session_id`、`user_id`、`run_id`，自动填充到每条日志
- logger 按模块命名：`logging.getLogger("supervisor")`、`logging.getLogger("rag_agent")`、`logging.getLogger("analysis_agent")`、`logging.getLogger("chat")`
- 日志同时输出到 stdout 和文件 `logs/agent.json.log`，文件按大小轮转（10MB × 5 份）

```python
# config/logging.py 骨架
import logging
import json
import sys
from datetime import datetime, timezone

class JSONFormatter(logging.Formatter):
    def format(self, record):
        from config.logging_context import current_session_id, current_user_id, current_run_id
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": current_session_id.get(),
            "user_id": current_user_id.get(),
            "run_id": current_run_id.get(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

def setup_logging():
    import os
    os.makedirs("logs", exist_ok=True)
    # 配置 root logger，handler 指向 JSONFormatter
    # stdout handler + file handler（RotatingFileHandler）
```

**Step 1.2 — `config/logging_context.py`（新建）**

- 集中管理请求级 contextvars
- 复用 `chat.py` 中现有的 `current_user_id`，迁移至此文件

```python
# config/logging_context.py
import contextvars

current_session_id: contextvars.ContextVar = contextvars.ContextVar("session_id", default=None)
current_user_id: contextvars.ContextVar = contextvars.ContextVar("user_id", default=None)
current_run_id: contextvars.ContextVar = contextvars.ContextVar("run_id", default=None)
```

- `tools.py` 中现有的 `current_user_id: ContextVar[int] = ContextVar(...)` 迁移至 `logging_context.py`，`tools.py` 改为 `from config.logging_context import current_user_id`
- `chat.py` 中的 `current_user_id` 同理迁移
- `src/agents/supervisor.py` 如果也引用了 current_user_id，同步迁移

**Step 1.3 — `.env` 加 LangSmith 配置**

```env
# LangSmith（可选，不配则 tracing 不生效，本地日志照常工作）
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_xxx
LANGCHAIN_PROJECT=mini-agent
```

- LangChain 检测到环境变量后自动启用 tracing，不需要在代码中添加 callback handler
- 如果 .env 中未配置 LangSmith 相关变量，LangSmith 不工作但系统不受影响

**Step 1.4 — `config/settings.py` 扩展**

```python
# 日志配置
LOG_LEVEL: str = "INFO"                    # 日志级别
LOG_FILE: str = "logs/agent.json.log"      # 日志文件路径
LOG_MAX_BYTES: int = 10 * 1024 * 1024     # 单文件最大 10MB
LOG_BACKUP_COUNT: int = 5                  # 保留 5 份轮转
```

**Step 1.5 — `src/api/app.py` 初始化日志**

- 在 FastAPI app 创建之前调用 `setup_logging()`
- 确保 `logs/` 目录存在

**Step 1.6 — 替换 print 为 logger**

| 文件 | 现有 print | 替换为 |
|------|-----------|--------|
| `src/agents/analysis_agent.py` | `print(f"=== ...")` | `logger.debug(...)` |
| `src/agents/tools.py` | `print(f"[DEBUG] list_documents...")` | `logger.debug(...)` |
| `src/services/chat.py` | 散落的 debug print | `logger.info(...)` 或 `logger.debug(...)` |

**Step 1.7 — contextvars 注入点**

在 `src/services/chat.py` 的 `chat()` 和 `chat_stream()` 入口处：

```python
from config.logging_context import current_session_id, current_user_id, current_run_id

def chat(message, session_id, user_id, db):
    current_user_id.set(user_id)
    current_session_id.set(str(session.session_uuid))
    # ... 后续流程
```

**Step 1.8 — run_id 捕获（可选项）**

- LangChain 自动 tracing 开启后，每次 LLM 调用会生成 run_id，存在于 callback context 中
- 尝试从 LangChain callback manager 或 `RunTree` 中提取 run_id
- 提取成功 → `current_run_id.set(run_id)`，后续日志自动携带
- 提取失败（LangSmith 未配置 / callback 无 run_id）→ `run_id` 保持 `null`，日志正常输出
- **不阻塞主流程，不抛异常**

```python
# chat.py 中的示例
try:
    from langchain_core.tracers.context import tracing_v2_enabled
    # 如果 tracing 已开启，run_id 在 callback 的 run_tree 中
    # 具体提取方式取决于 LangSmith 版本，需实测确认
    # 提取不到则跳过，run_id 保持 null
except Exception:
    pass  # run_id 提取失败不影响正常流程
```

> **注意：** run_id 的具体提取方式需要在接入 LangSmith 后实测确认，不同版本的 langchain-core 回调接口有差异。此 step 可延后到 Task 1 其他部分验收通过后再做。

#### 1.3 验收标准

1. LangSmith 已配置时：发送一条消息 → LangSmith dashboard 出现完整 trace（含 Supervisor 路由、工具调用、LLM 调用）→ 本地日志文件中同一条请求的 `run_id` 与 LangSmith 一致
2. LangSmith 未配置时：本地日志正常输出 JSON，`run_id` 为 null，系统无报错
3. 日志文件 `logs/agent.json.log` 持续增长，每行一个 JSON 对象，可通过 `jq` 或 `grep` 过滤

---

### Task 2：长文档截断（约半天）

**目标：** 消灭 Moonshot content_filter 400，同一文档每次截取结果确定。

#### 2.1 实现逻辑

```python
RATIO = {"head": 0.4, "tail": 0.4, "mid": 0.2}
MIN_PER_DOC = 2000  # 多文档均分时单篇最低预算，低于此值不做均分

def smart_truncate(text: str, doc_id: str, budget: int) -> str:
    """
    按预算做加权截取：开头 40% + 结论 40% + 中间抽样 20%（固定 seed 保证确定性）。
    短文档不截断，直接返回。纯文本处理，不附加任何提示语。
    """
    if len(text) <= budget:
        return text

    head_len = int(budget * RATIO["head"])
    tail_len = int(budget * RATIO["tail"])

    # 重叠检测：head + tail 超过文本长度时，各取一半
    if head_len + tail_len >= len(text):
        half = len(text) // 2
        head_len = tail_len = half

    head = text[:head_len]
    tail = text[-tail_len:]

    mid_len = budget - head_len - tail_len
    if mid_len <= 0:
        return head + "\n...\n" + tail

    # 用 doc_id 做 seed，同一文档每次抽样位置一致
    import random as _random
    rng = _random.Random(doc_id)
    mid_start = rng.randint(head_len, len(text) - tail_len - mid_len)

    # 对齐到段落边界（找最近的换行符），避免截断中文字符
    newline_pos = text.rfind("\n", max(0, mid_start - 50), min(len(text), mid_start + 50))
    if newline_pos != -1:
        mid_start = newline_pos + 1

    # 确保 mid 不侵入 head 区域（段落对齐后可能回退到 head 范围内）
    mid_start = max(mid_start, head_len)

    mid = text[mid_start : mid_start + mid_len]
    return head + "\n...\n" + mid + "\n...\n" + tail
```

#### 2.2 实现步骤

**Step 2.1 — 新建目录和文件**

- 创建 `src/utils/__init__.py`（空文件，使 utils 成为包）
- 创建 `src/utils/truncation.py`，放置 `smart_truncate` 函数 + `RATIO` + `MIN_PER_DOC` 常量

**Step 2.2 — `config/settings.py` 替换字段**

- 删除 `ANALYSIS_MAX_CHARS: int = 50000`
- 新增 `ANALYSIS_CHAR_BUDGET: int = 8000`

**Step 2.3 — `src/services/chat.py` 接入（同步版 `chat()` + 异步版 `chat_stream()`）**

两个版本的 analysis_agent 分支改动一致。替换现有的 `ANALYSIS_MAX_CHARS` 截断逻辑：

```python
from src.utils.truncation import smart_truncate, MIN_PER_DOC

# 多文档均分预算（加下限保护）
if len(doc_ids) > 1:
    per_doc_budget = max(settings.ANALYSIS_CHAR_BUDGET // len(doc_ids), MIN_PER_DOC)
else:
    per_doc_budget = settings.ANALYSIS_CHAR_BUDGET

for doc_id in doc_ids:
    full_text, source = _get_text(doc_id, user_id or 0)
    original_len = len(full_text)

    # 截断（smart_truncate 纯文本处理，不附加提示语）
    truncated_text = smart_truncate(full_text, doc_id, budget=per_doc_budget)

    # 调用方追加截断提示语（让 LLM 知道这不是完整文档）
    if len(full_text) > per_doc_budget:
        truncated_text += f"\n\n[提示：文档全文 {original_len} 字，已截取前 {per_doc_budget} 字]"

    doc_contents.append(f"=== 文档：{source} ===\n\n{truncated_text}")

    # citations 的 snippet 在截断之后取（确保展示内容和喂给 LLM 的一致）
    citations_list.append({
        "document_id": doc_id,
        "filename": source,
        "chunk_index": None,
        "rrf_score": 1.0,
        "relevance_label": "主文档",
        "snippet": truncated_text[:200],  # 截断后的文本，不是原文
        "retrieval_method": "full_text",
    })
```

- 截断提示语由调用方追加，smart_truncate 保持纯文本处理职责
- snippet 在截断之后取值，确保引用卡片展示内容与 LLM 收到的一致

**Step 2.4 — `src/agents/analysis_agent.py` 同步更新（可选）**

analysis_agent.py 中的 `get_document_full_text` 和 `get_documents_for_compare` 工具仍引用 `ANALYSIS_MAX_CHARS`。这些工具在确定性流程中已不被调用（dead code），但为保持一致性，也替换为 `ANALYSIS_CHAR_BUDGET`。后续 Task 5 清理旧代码时会删除这些工具。

**Step 2.5 — CHAR_BUDGET 调优**

- 初始值 8000 字符（约 4000 token，Moonshot 8K 模型的安全范围）
- 如果仍触发 content_filter，逐步下调：8000 → 6000 → 4000
- 如果不触发，可适当上调以保留更多信息

#### 2.3 验收标准

1. 上传之前触发 content_filter 的文档（郭建那篇），分析请求正常返回，不报 400
2. 同一文档连续请求两次，返回内容一致（mid 抽样位置固定）
3. 短文档（< 8000 字符）不截断，分析内容完整
4. 对比两篇文档时，每篇预算不小于 MIN_PER_DOC（2000 字符）
5. 引用卡片的 snippet 与 LLM 收到的内容一致

---

### Task 3：Admin API + 角色模型（约半天）

**目标：** 角色隔离到位，管理接口可用。不做前端管理页面，Swagger UI 够用。

#### 3.1 数据库变更

**现状：** `User` ORM 已有 `role = Column(String(16), default="user")` 字段，无需修改 ORM。

**需要确认：** 数据库中 `users` 表是否已有 `role` 列。如果表是 Phase 1 创建的且之后未迁移，需要手动执行：

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) DEFAULT 'user';
```

或通过 Alembic migration 添加。

#### 3.2 初始 Admin 创建

- 不在注册接口中暴露角色选择（防滥用）
- 采用 bootstrap 逻辑：如果 `users` 表为空，第一个注册的用户自动设为 `role='admin'`
- 实现在 `src/api/routers/auth_router.py` 的注册接口中

```python
@router.post("/auth/register", response_model=UserResponse)
def register(req: UserCreate, db: Session = Depends(get_db)):
    # ... 现有检查 ...
    is_first_user = db.query(User).count() == 0
    user = User(
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        role="admin" if is_first_user else "user",
    )
    # ...
```

#### 3.3 `require_admin` 依赖

```python
# src/services/auth.py 中新增
def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
```

#### 3.4 `src/api/routers/admin_router.py`（新建）

| 方法 | 路径 | 功能 | 依赖 |
|------|------|------|------|
| GET | `/admin/users` | 用户列表，含各自文档数、会话数 | require_admin |
| GET | `/admin/sessions?user_id=&limit=` | 查指定用户的会话列表（按 updated_at 倒序） | require_admin |
| GET | `/admin/sessions/{uuid}/messages` | 跨用户查看任意会话的消息（分页） | require_admin |
| GET | `/admin/documents?user_id=` | 全库文档列表，可按用户筛选 | require_admin |
| DELETE | `/admin/documents/{doc_id}` | 强制删除任意文档（含 Milvus chunks 清理） | require_admin |
| GET | `/admin/stats` | 系统统计（路由分布、错误率、总调用次数） | require_admin |

**`/admin/stats` 实现：**

从 PostgreSQL 聚合，不重复建设 LangSmith 已有的数据。

```python
@router.get("/admin/stats")
def admin_stats(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from sqlalchemy import func as sa_func

    # 路由分布：按 agent_name 统计 assistant 消息数
    route_dist = (
        db.query(ChatMessage.agent_name, sa_func.count(ChatMessage.id))
        .filter(ChatMessage.role == "assistant", ChatMessage.agent_name.isnot(None))
        .group_by(ChatMessage.agent_name)
        .all()
    )

    # 总调用次数（user 消息数）
    total_messages = db.query(sa_func.count(ChatMessage.id)).filter(ChatMessage.role == "user").scalar()

    # 总用户数、总文档数、总会话数
    total_users = db.query(sa_func.count(User.id)).scalar()
    from src.types.document import Document
    total_docs = db.query(sa_func.count(Document.id)).scalar()
    total_sessions = db.query(sa_func.count(ChatSession.id)).scalar()

    return {
        "total_messages": total_messages,
        "total_users": total_users,
        "total_documents": total_docs,
        "total_sessions": total_sessions,
        "route_distribution": {name: count for name, count in route_dist},
    }
```

**`/admin/documents` 实现：**

联查 Document 表和 User 表，返回文档列表含上传者信息：

```python
@router.get("/admin/documents")
def admin_documents(user_id: int = None, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    query = db.query(Document, User.username).join(User, Document.user_id == User.id)
    if user_id:
        query = query.filter(Document.user_id == user_id)
    results = query.order_by(Document.created_at.desc()).all()
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "file_size": doc.file_size,
            "chunk_count": doc.chunk_count,
            "uploaded_by": username,
            "created_at": doc.created_at,
        }
        for doc, username in results
    ]
```

#### 3.5 `src/api/app.py` 注册

```python
from src.api.routers.admin_router import router as admin_router
app.include_router(admin_router)
```

#### 3.6 验收标准

1. 第一个注册的用户自动获得 `role='admin'`
2. admin 用户在 Swagger UI 调通所有 `/admin/*` 接口
3. 普通用户调 `/admin/*` 接口返回 403
4. `/admin/stats` 返回路由分布、总调用次数等统计
5. `/admin/documents` 能看到所有用户的文档，含上传者信息

---

### Task 4：Supervisor 路由优化（配合 Task 1）

**目标：** 高频简单请求不过 LLM，正则直接命中。规则表硬限制 20 条，防止无限膨胀。

#### 4.1 规则表

```python
# src/agents/supervisor.py

FAST_ROUTES: list[dict] = [
    {
        "pattern": r"(现在|当前|今天|今天是).*(时间|几点|日期|星期|几号)",
        "agent": "general_chat",
        "desc": "问时间/日期",
    },
    {
        "pattern": r"(分析|总结|概括|摘要|解读|梳理|讲了什么|主要内容|介绍了什么).{0,6}(文档|文件|论文|这篇|那篇)",
        "agent": "analysis_agent",
        "desc": "文档分析请求",
    },
    {
        "pattern": r"(对比|比较|异同|区别|相同|不同).{0,10}(文档|文件|论文|两篇|几篇)",
        "agent": "analysis_agent",
        "desc": "文档对比请求",
    },
    {
        "pattern": r"(搜索|查找|检索|知识库里|查一下|有没有关于)",
        "agent": "rag_agent",
        "desc": "知识库检索",
    },
]

FAST_ROUTE_HARD_LIMIT = 20  # 超过此数应重构路由逻辑，而非继续加规则
```

**注意：** 不加"列文档"规则。`list_documents` 是 rag_agent 的工具，但用户说"列出文档"时意图多样（可能只是想看列表，也可能接着要分析），让 LLM 判断更准确。

#### 4.2 路由逻辑修改

在 `route()` 函数开头加正则快速路径：

```python
import re
from config.logging_context import current_run_id
from config.logging import get_logger

logger = get_logger("supervisor")

def route(message: str, history: list | None = None, agent_hint: str | None = None,
          doc_list_text: str | None = None) -> dict:
    """路由用户消息到对应的子 Agent。"""

    # agent_hint 直接返回
    if agent_hint and agent_hint in VALID_AGENTS:
        logger.info(f"route: agent_hint={agent_hint}")
        return {"query": message, "agent": agent_hint, "document_ids": []}

    # 快速路径：正则匹配
    for rule in FAST_ROUTES:
        if re.search(rule["pattern"], message):
            logger.info(f"route: method=regex, agent={rule['agent']}, rule={rule['desc']}")
            return {"query": message, "agent": rule["agent"], "document_ids": []}

    # 慢路径：LLM 路由
    logger.info("route: method=llm")
    # ... 现有 LLM 路由逻辑 ...
```

#### 4.3 日志标记

- 正则命中：日志中 `route_method: "regex"`，附带命中规则的 `desc`
- LLM 路由：日志中 `route_method: "llm"`
- LangSmith 中正则命中的请求不会有 LLM trace（因为没调 LLM），可通过 trace 数量差异直观判断正则覆盖率

#### 4.4 验收标准

1. 问"现在几点" → 不产生 LangSmith LLM trace，日志里 `route_method` 为 `regex`，`agent` 为 `general_chat`
2. 问"帮我分析张宇那篇文档" → 命中正则，`agent` 为 `analysis_agent`
3. 问"你好" → 未命中正则，走 LLM 路由，`route_method` 为 `llm`
4. 问"列出我的文档" → 未命中正则（故意不加规则），走 LLM 路由到 `rag_agent`

---

### Task 5：清理旧代码（穿插进行）

不单独排时间，与 Task 1-4 同步完成：

| 文件 | 操作 |
|------|------|
| `src/agents/analysis_agent.py` | 删除旧 ReAct 相关注释、`create_react_agent` 引用、debug print 替换为 logger |
| `src/agents/tools.py` | debug print 替换为 logger（Task 1 顺手） |
| `src/agents/supervisor.py` | 路由结果日志化（Task 4 顺手） |
| `CLAUDE.md` | 更新项目结构，标注新增文件（logging.py、logging_context.py、admin_router.py、truncation.py） |
| `docs/UPGRADE_PLAN.md` | Phase 8 标记为已完成 |
| `MEMORY.md` | 更新进度 |

---

## 文件变更总览

| 文件 | 操作 | 所属 Task |
|------|------|-----------|
| `config/logging.py` | 新建 | Task 1 |
| `config/logging_context.py` | 新建 | Task 1 |
| `config/settings.py` | 修改 — 加日志配置项；ANALYSIS_MAX_CHARS 替换为 ANALYSIS_CHAR_BUDGET | Task 1, Task 2 |
| `src/api/app.py` | 修改 — setup_logging() 初始化 + 注册 admin_router | Task 1, Task 3 |
| `src/services/chat.py` | 修改 — contextvars 迁移 + run_id 注入 + smart_truncate 接入（替换 ANALYSIS_MAX_CHARS 截断逻辑） | Task 1, Task 2 |
| `src/agents/analysis_agent.py` | 修改 — debug print → logger；ANALYSIS_MAX_CHARS → ANALYSIS_CHAR_BUDGET | Task 1, Task 2, Task 5 |
| `src/agents/tools.py` | 修改 — debug print → logger，current_user_id 迁移 | Task 1, Task 5 |
| `src/agents/supervisor.py` | 修改 — FAST_ROUTES 正则快速路径 + 日志 | Task 4 |
| `src/utils/__init__.py` | 新建 — 空文件，使 utils 成为包 | Task 2 |
| `src/utils/truncation.py` | 新建 — smart_truncate 函数 | Task 2 |
| `src/api/routers/admin_router.py` | 新建 — 管理接口 | Task 3 |
| `src/api/routers/auth_router.py` | 修改 — bootstrap admin 逻辑 | Task 3 |
| `src/services/auth.py` | 修改 — require_admin 依赖 | Task 3 |
| `.env` | 修改 — 加 LangSmith 配置 | Task 1 |

---

## 执行顺序

```
Day 1 上午：Task 1 Step 1.1-1.6（logging 框架 + contextvars + print 替换）
Day 1 下午：Task 1 Step 1.7-1.8（contextvars 注入 + run_id 可选捕获）
            Task 4（Supervisor 路由优化，依赖 Task 1 的日志能力）
Day 2 上午：Task 2（长文档截断）
Day 2 下午：Task 3（Admin API + 角色模型）
穿插全程：Task 5（清理旧代码）
```

**总计约 1.5-2 天。**

---

## 依赖关系

```
Task 1（日志基础设施）──→ Task 4（路由优化，需要日志验证正则命中率）
                       ──→ Task 3（Admin stats 依赖日志 schema 设计）

Task 2（长文档截断）──→ 独立，不依赖其他 Task

Task 5（清理旧代码）──→ 穿插在 Task 1 中，不单独排期
```

---

## 验证方式

每个 Task 完成后，按上述各自的"验收标准"逐条验证。
全部完成后：

1. `docker compose up -d` + `uvicorn src.api.app:app --reload` 启动服务
2. 注册第一个用户 → 确认为 admin 角色
3. 登录 → 发送各类消息（闲聊、文档分析、知识库检索）→ 检查：
   - LangSmith dashboard 有完整 trace（如已配置）
   - `logs/agent.json.log` 有 JSON 日志，每条含 session_id、user_id
   - 正则命中的请求 `route_method` 为 `regex`，无 LLM trace
4. 上传之前触发 content_filter 的文档 → 分析请求正常返回
5. 用 admin 账号在 Swagger UI 调通所有 `/admin/*` 接口
6. 用普通账号调 `/admin/*` 返回 403

---

# Phase 9：能力扩展

## 背景

Phase 1-8 已完成基础设施建设（认证、文档管理、RAG 混合检索、Multi-Agent、会话持久化、前端、Docker 部署、结构化日志、长文档截断、Admin API）。
Phase 9 在稳定基础上扩展真实有用的能力，不补基础设施，而是提升产品体验和系统灵活性。

## 执行原则

- Task 1、2 先做，不依赖外部数据
- Task 3 启用后观察真实数据
- Task 4 根据 Task 3 数据决定做不做，不拍脑袋
- Task 5 穿插进行，改动独立

---

### Task 1：跨文档对比（确定性流程增强）

**目标：** 用户问"郭建和李根那两篇有什么区别"时，系统能稳定识别多文档对比意图，提取多个 document_ids，并以结构化方式呈现对比结果。

**现状问题：**
- Supervisor 能提取多个 document_ids，但 chat.py 的 analysis_agent 分支对多文档只是简单拼接全文，没有专门的对比 prompt
- 用户问"区别""异同""对比"时，LLM 收到的指令和单文档摘要一样，对比质量不稳定

**功能要求：**
- 用户说"对比""异同""区别"等关键词时，系统识别为对比意图
- 自动提取涉及的多篇文档 ID
- 返回结构化对比（分点、表格），而非简单并列两篇摘要
- 对比维度由 LLM 基于文档内容自行判断，不硬编码

**实现方式：** 在 chat.py 的确定性流程中增强，不走工具调用。具体改动点待读代码后确认。

**验收标准：**
1. "郭建和李根那两篇有什么区别" → 正确提取 2 个 doc_ids → 返回结构化对比
2. "对比一下张宇和李根的方法" → 同上
3. 单文档摘要功能不受影响

---

### Task 2：LiteLLM 供应商解耦

**目标：** LLM 调用从硬编码 Moonshot 改为 LiteLLM 统一接口，切换模型只改 .env，业务代码不动。

**现状问题：**
- `src/services/llm.py` 硬编码 `ChatOpenAI(model="kimi-k2.5", base_url=...)`
- 想换 Claude / GPT-4o / 本地 Ollama 要改代码
- 无法快速做模型对比测试和成本控制

**功能要求：**
- .env 里配置模型供应商和模型名，业务代码不感知具体供应商
- 支持 Moonshot（OpenAI 兼容）、Claude、GPT-4o、Ollama 等主流供应商
- 流式和非流式调用都正常工作
- Token 用量能被记录（接入 LangSmith 后可追踪成本）

**改动范围：** 集中在 `src/services/llm.py`，可能涉及少量配置调整。

**验收标准：**
1. .env 只改 `MODEL` 和 `BASE_URL`，三个 Agent 都正常工作
2. 流式对话（chat_stream）SSE 输出正常，无 token 丢失或乱序
3. 换一个模型（如切换到另一个 Moonshot 模型名）零代码改动即可生效

**注意事项：**
- Moonshot 的 SSE 格式和 OpenAI 不完全一致，LiteLLM 适配层可能有 edge case
- 做完后立刻跑一遍三个 Agent（general_chat、rag_agent、analysis_agent）确认兼容

---

### Task 3：LangSmith 正式启用

**目标：** 接入 LangSmith tracing，积累真实调用数据，为后续优化决策提供依据。

**功能要求：**
- 取消 .env 中 LangSmith 配置的注释，填入 API key
- 每次对话请求在 LangSmith dashboard 产生完整 trace（Supervisor 路由 → 子 Agent → 工具调用 → LLM 调用）
- trace 中包含 session_id、user_id，与本地 JSON 日志关联
- 跑两天真实数据，观察：
  - Supervisor 路由分布（general_chat / rag_agent / analysis_agent 各占多少）
  - analysis_agent 端到端耗时
  - 有没有静默错误（工具调用失败但 LLM 没报告）
  - RAG 检索的 miss case 具体是哪些

**前置条件：** 需要 LangSmith API key。

**验收标准：**
1. LangSmith dashboard 能看到完整 trace 链路
2. trace 中 session_id 和本地日志一致
3. 两天数据积累后能回答"RAG miss rate 是多少""miss 的都是什么问题"

---

### Task 4：Tavily 搜索 fallback（视数据决定）

**目标：** 如果 RAG 未命中率过高且原因是"知识库没内容"，接入 Tavily 作为兜底搜索。

**决策规则：**
- Task 3 数据到手后，人工分析 miss case
- 区分"系统 miss"（知识库有内容但检索没命中）和"正常 miss"（问题超出文档范围）
- 如果"正常 miss"占比高 → 接 Tavily 作为 fallback 有价值
- 如果"系统 miss"为主 → 应优化检索策略而非加搜索
- 只有确认要做才进入实现，否则跳过此 Task

**功能要求（如果要做）：**
- rag_agent 检索结果为空或得分过低时，自动调用 Tavily 搜索
- Tavily 结果作为补充上下文传给 LLM，标注来源为"网络搜索"
- 不替换 RAG 检索，只在 RAG 无结果时兜底

**前置条件：** Task 3 数据 + 人工分析结论。

---

### Task 5：引用卡片 UI 优化

**目标：** 将当前不显眼的"查看引用"改为内联脚注样式，提升回答可信度和引用可读性。

**现状问题：**
- "查看引用"是一行小字，用户很少点击
- 引用信息和回答内容分离，用户难以对应"这句话的依据是什么"

**功能要求：**
- 回答正文中对应位置显示 `[1]` 上标标记
- 底部引用卡片更突出，显示文档名 + 片段预览
- 点击 `[1]` 跳转到对应引用卡片
- 纯前端改动，不涉及后端 API 变更

**改动范围：** `frontend/src/components/MessageList.vue`、`CitationCard.vue`

**验收标准：**
1. RAG 回复中关键位置有 `[1]` 上标
2. 底部引用卡片样式清晰，和正文引用一一对应
3. 非 RAG 回复（general_chat、analysis_agent）不受影响

---

## 文件变更总览

| 文件 | 操作 | 所属 Task |
|------|------|-----------|
| `src/services/llm.py` | 重构 — LiteLLM 统一接口 | Task 2 |
| `config/settings.py` | 修改 — LLM 供应商配置项 | Task 2 |
| `src/services/chat.py` | 修改 — 多文档对比 prompt 增强 | Task 1 |
| `.env` | 修改 — LangSmith 取消注释 | Task 3 |
| `frontend/src/components/MessageList.vue` | 修改 — 内联引用标记 | Task 5 |
| `frontend/src/components/CitationCard.vue` | 修改 — 引用卡片样式 | Task 5 |

## 执行顺序

```
Task 2（LiteLLM 解耦，改动最小收益最确定）
  ↓
Task 1（跨文档对比，确定性流程增强，先读 chat.py 确认改动点）
  ↓
Task 3（LangSmith 启用，等 API key）
  ↓
观察两天数据 → 人工分析 miss case → 决定 Task 4 做不做
  ↓
Task 5（引用卡片 UI，穿插在任何时候）
```
