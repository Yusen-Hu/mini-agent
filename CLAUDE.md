# Mini Agent — CLAUDE.md

基于 LangGraph 的 AI Agent 应用，支持 RAG 知识库检索、文档分析、多 Agent 调度和流式对话。
本文件为 AI 助手提供项目上下文，协助开发、调试和功能扩展。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| Agent 引擎 | LangGraph `create_react_agent`（ReAct）+ 确定性流程（analysis_agent） |
| LLM 接口 | LiteLLM（统一 100+ Provider，当前 `openai/kimi-k2.5`） |
| 向量库 | Milvus（本地 Docker）+ etcd + MinIO |
| 检索策略 | Hybrid Retrieval（Dense + BM25 + BM25-primary RRF / 对称 RRF 可切换） |
| Embedding | HuggingFace `paraphrase-multilingual-MiniLM-L12-v2`，384 维，本地推理 |
| 数据库 | PostgreSQL + SQLAlchemy ORM |
| 认证 | JWT（passlib bcrypt） |
| 前端 | Vite + Vue 3 多组件 + Pinia + Vue Router + marked.js |
| Admin 页面 | 独立 `admin.html`（Vue 3 CDN，单文件） |
| 日志 | Python logging + JSON Formatter + contextvars |
| 基础设施 | Docker Compose（Milvus + PostgreSQL + etcd + MinIO + 后端 + 前端 Nginx） |

---

## 项目结构

```
mini_agent/
├── src/
│   ├── api/
│   │   ├── app.py                          # FastAPI 入口：CORS、路由注册、SPA fallback、/health
│   │   ├── middleware.py                    # 限流（slowapi）+ 全局异常处理器
│   │   └── routers/
│   │       ├── auth_router.py              # /auth/register、/auth/login、/auth/me（含 bootstrap admin）
│   │       ├── chat_router.py              # /chat、/chat/stream（SSE 流式）
│   │       ├── document_router.py          # 文档 CRUD + 上传 + PATCH 公开/私有
│   │       ├── session_router.py           # 会话 CRUD（列表、消息、删除、改标题）
│   │       └── admin_router.py             # 管理端点（users/sessions/messages/documents/delete/stats）
│   ├── agents/
│   │   ├── supervisor.py                   # Supervisor 路由（FAST_ROUTES 正则 + LLM 路由 + route_method 日志）
│   │   ├── general_chat.py                 # 通用对话（create_react_agent + get_current_time）
│   │   ├── rag_agent.py                    # RAG Agent（create_react_agent + 5 工具）
│   │   ├── analysis_agent.py               # 文档分析（仅保留 ANALYSIS_SYSTEM_PROMPT，确定性流程在 chat.py）
│   │   └── tools.py                        # 共享工具（get_current_time、search_knowledge_base、find_source、get_document_info、list_documents）
│   ├── services/
│   │   ├── llm.py                          # LLM 初始化（ChatLiteLLM）
│   │   ├── chat.py                         # 核心对话服务（Supervisor 调度 + 流式 SSE + 三 Agent 分发 + citations + Query Rewriting + tool_start 事件）
│   │   └── auth.py                         # 认证（JWT + bcrypt）+ get_current_user + require_admin
│   ├── types/
│   │   ├── user.py                         # User ORM + Base（SQLAlchemy declarative base）
│   │   ├── document.py                     # Document ORM
│   │   ├── session.py                      # ChatSession + ChatMessage ORM
│   │   └── chat.py                         # Pydantic 模型（ChatRequest、ChatResponse）
│   └── utils/
│       └── truncation.py                   # smart_truncate（head 40% + tail 40% + mid 20% 固定 seed）
│
├── skills/
│   └── rag/
│       ├── collection.py                   # Milvus 连接 + Embedding + Collection 管理
│       ├── ingestion.py                    # 文档加载/分块/入库 + get_document_full_text
│       ├── retrieval.py                    # 混合检索（Dense + BM25 + RRF / BM25-primary 可切换）
│       └── bm25_index.py                   # BM25 索引（jieba 分词 + 权限过滤 + 分词缓存）
│
├── config/
│   ├── settings.py                         # pydantic-settings 全局配置
│   ├── database.py                         # SQLAlchemy engine + SessionLocal
│   ├── logging.py                          # JSON Formatter + setup_logging + get_logger
│   └── logging_context.py                  # contextvars（session_id、user_id、run_id）
│
├── frontend/
│   ├── index.html                          # Vite 入口
│   ├── admin.html                          # 独立 Admin 页面（Vue 3 CDN，三 tab：stats/users/sessions）
│   ├── src/
│   │   ├── main.js                         # Vue app 创建
│   │   ├── App.vue                         # 根组件
│   │   ├── router/index.js                 # Vue Router（/ → Chat, /login → Login）
│   │   ├── views/
│   │   │   ├── ChatView.vue                # 聊天主页面（含 admin 入口链接）
│   │   │   └── LoginView.vue               # 登录/注册页
│   │   ├── components/
│   │   │   ├── Sidebar.vue                 # 侧边栏（新建会话、上传文档、文档列表、会话列表）
│   │   │   ├── MessageList.vue             # 消息列表 + Markdown + KaTeX 公式 + 引用上标
│   │   │   ├── InputBar.vue                # 输入框
│   │   │   ├── SessionList.vue             # 会话列表组件
│   │   │   └── CitationCard.vue            # 引用卡片
│   │   ├── stores/
│   │   │   ├── auth.js                     # Pinia auth store（token、login、fetchUser）
│   │   │   ├── chat.js                     # Pinia chat store（消息、会话、SSE 流式 + tool_start 事件）
│   │   │   └── documents.js               # Pinia documents store
│   │   └── api/
│   │       ├── client.js                   # axios 封装（自动附 Bearer token、401 拦截）
│   │       ├── auth.js                     # auth API
│   │       ├── chat.js                     # chat SSE API
│   │       ├── sessions.js                 # sessions API
│   │       └── documents.js               # documents API
│   ├── dist/                               # Vite build 产出（uvicorn 实际 serve 的文件）
│   ├── vite.config.js
│   └── package.json
│
├── data/documents/                         # 上传文档存放目录
├── scripts/                                # 评估脚本 + 数据迁移
│   ├── eval_retrieval.py                   # RAG 检索评测（HR@8 / MRR@8）
│   ├── gen_eval_questions.py               # LLM 自动生成评测问题
│   └── eval_results/                       # 评测结果 JSON
├── tests/                                  # 测试目录（unit/ 单测 14 case）
├── docker-compose.yml
├── .env.example                            # 本地开发环境变量模板
├── .env.docker.example                     # Docker 部署环境变量模板
├── CLAUDE.md                               # 本文件
└── LICENSE                                 # Apache 2.0
```

---

## 启动方式

```bash
# 1. 启动基础设施 + 后端 + 前端（Docker）
docker compose up -d

# 2. 本地开发后端（先停 Docker 后端容器释放 8000 端口）
docker stop mini_agent-backend-1
python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 3. 本地开发前端
cd frontend
npm install
npm run dev          # Vite dev server（默认 5173 端口）
npm run build        # 打包到 dist/，uvicorn 才能 serve

# 4. 访问
# 聊天界面：http://127.0.0.1:8000（Docker 或 build 后）
# Admin 页面：http://127.0.0.1:8000/admin.html
# API 文档：http://127.0.0.1:8000/docs
```

> **注意：** 修改 `.vue` 文件后必须 `npm run build`，uvicorn serve 的是 `frontend/dist/`，不是源码。

---

## 环境变量（.env）

详见 `.env.example`（本地开发）和 `.env.docker.example`（Docker 部署）。

关键变量：
- `LLM_API_KEY`：LLM API Key（必填）
- `LLM_MODEL`：litellm 格式，默认 `openai/kimi-k2.5`
- `DATABASE_URL`：PostgreSQL 连接串
- `JWT_SECRET_KEY`：JWT 签名密钥（生产环境必须修改）
- `RETRIEVAL_STRATEGY`：`symmetric_rrf` | `bm25_primary`

---

## 核心逻辑说明

### Multi-Agent 对话流程

```
用户消息
  → src/services/chat.py：Supervisor 路由
  → src/agents/supervisor.py：
      ├── 正则快速路径（问时间 → general_chat，不调 LLM）
      └── LLM 路由（判断意图 + 提取 document_ids）
  → 分发到子 Agent：
      ├── general_chat：通用对话（create_react_agent + get_current_time）
      ├── rag_agent：知识库检索（create_react_agent + search_knowledge_base）
      └── analysis_agent：文档分析（代码确定性获取全文 + LLM 生成分析）
  → 保存消息到 PostgreSQL（ChatSession + ChatMessage）
  → 流式 SSE 返回前端
```

### RAG 检索流程（Hybrid Retrieval）

```
文档上传
  → skills/rag/ingestion.py：加载 + 分块（chunk_size=500, overlap=50）
  → skills/rag/collection.py：HuggingFace Embedding 向量化（384 维）
  → 写入 Milvus（knowledge_base collection，含 document_id/user_id/is_public）

用户提问
  → Dense 检索：embed_query → Milvus search（top_k=20）
  → BM25 检索：jieba 分词 → BM25 索引（top_k=20）
  → 融合策略（可切换）：
      ├── bm25_primary：BM25 候选池 + Dense rerank 加分（推荐，HR@8=88%）
      └── symmetric_rrf：Dense + BM25 对称 RRF 融合
  → 取 top_k=8，返回 [{text, source, score, chunk_index}] 拼接为上下文
```

### 文档分析流程（确定性，非 ReAct）

```
用户要求分析文档
  → Supervisor 提取 document_ids
  → chat.py 代码直接调用 get_document_full_text（不依赖 Agent 工具调用）
  → smart_truncate 截断（head 40% + tail 40% + mid 20%，固定 seed）
  → 文档全文拼入 HumanMessage，传给 LLM
  → LLM 基于 ANALYSIS_SYSTEM_PROMPT 生成分析
```

### 流式输出协议（SSE）

每个事件格式：`data: {JSON}\n\n`

- agent 通知：`data: {"type": "agent", "agent": "rag_agent"}`
- token：`data: {"type": "token", "content": "你好"}`
- session：`data: {"type": "session", "session_id": "uuid"}`
- 引用：`data: {"type": "citation", "items": [...]}`
- 工具调用：`data: {"type": "tool_start", "tool": "search_knowledge_base", "label": "正在搜索知识库..."}`
- 结束：`data: {"type": "done"}`

---

## 常见开发任务

### 加新工具

在 `src/agents/tools.py` 中：

```python
@tool
def my_new_tool(query: str) -> str:
    """描述清楚什么时候调用这个工具，LLM 靠 docstring 决定是否调用。"""
    return "结果"

tools = [get_current_time, search_knowledge_base, find_source, get_document_info, list_documents, my_new_tool]
```

工具分配到 Agent：
- `general_chat`：只传 `get_current_time`（通用能力）
- `rag_agent`：传全部 5 个工具（search_knowledge_base、find_source、get_document_info、list_documents、get_current_time）
- `analysis_agent`：不走 ReAct，工具调用由 chat.py 代码控制

### 调整检索参数

在 `config/settings.py` 中：

```python
DENSE_TOP_K = 20       # Dense 检索候选数
BM25_TOP_K = 20        # BM25 检索候选数
FINAL_TOP_K = 8        # 融合后最终返回数
RRF_K = 60             # RRF 平滑常数（symmetric_rrf 模式）
RRF_ALPHA = 0.5        # Dense/BM25 权重（symmetric_rrf 模式）
RETRIEVAL_STRATEGY = "bm25_primary"  # "symmetric_rrf" | "bm25_primary"
DENSE_BONUS_WEIGHT = 0.3  # bm25_primary 模式下 Dense 加分权重
CHUNK_SIZE = 500       # 分块大小
ANALYSIS_CHAR_BUDGET = 8000  # 文档分析字符预算
```

### 添加 Supervisor 路由规则

在 `src/agents/supervisor.py` 的 `FAST_ROUTES` 中：

```python
FAST_ROUTES: list[dict] = [
    {
        "pattern": r"(现在|当前|今天).*(时间|几点|日期)",
        "agent": "general_chat",
        "desc": "问时间/日期",
    },
    # 添加新规则...
]
```

> **注意：** 正则只适合表达极度固定的意图（如"几点了"）。语义路由交给 LLM。

---

## 已知问题与技术债

| 问题 | 风险等级 | 说明 |
|------|----------|------|
| `chat_stream` 中途断开 | 低 | 断开时会把不完整回复写入历史记录（已在 finally 块保存已生成部分） |
| CORS 全开 | 中 | `allow_origins=["*"]`，生产环境需收紧 |
| 无 Alembic | 中 | 用 `create_all()`，不能 ALTER 已有表，schema 变更需手动 SQL |
| LangSmith 配置已就位 | 低 | .env 中 LANGCHAIN_* 字段已配置，注释状态可随时启用 |

---

## 注意事项

- `.env` 包含 API Key，**不要提交到版本控制**
- Docker 后端容器绑了 8000 端口，本地跑 uvicorn 需先 `docker stop mini_agent-backend-1`
- HuggingFace 本地测试需要 `HF_ENDPOINT=https://hf-mirror.com`，且需要关掉 VPN（TUN 模式劫持 SSL）
- 改了 `.vue` 文件必须 `npm run build`，uvicorn serve 的是 `frontend/dist/`
- `EMBEDDING_DIM` 改动后必须删除并重建 Milvus Collection
