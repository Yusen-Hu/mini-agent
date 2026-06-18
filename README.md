# Mini Agent

基于 LangGraph 的 AI Agent 应用，支持 Multi-Agent 路由、RAG 混合检索、文档分析、会话持久化和流式对话。

## 核心特性

- **Multi-Agent 调度**：Supervisor 路由（正则快速路径 + LLM 语义路由），分发到 GeneralChat / RAG / Analysis 三个 Agent
- **Hybrid Retrieval**：Dense（HuggingFace Embedding）+ BM25（jieba 分词）+ BM25-primary RRF 融合（HR@8=88%），支持文档级权限过滤、策略可切换
- **文档分析**：单篇分析 + 跨文档对比，smart_truncate 长文档截断（head 40% + tail 40% + mid 20%）
- **流式输出**：SSE 流式返回，支持 agent 通知、token 流、工具调用提示（tool_start）、引用卡片
- **Admin 管理**：独立管理页面（用户/会话/文档/统计），首用户自动 admin

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| Agent 引擎 | LangGraph `create_react_agent`（ReAct）+ 确定性流程（analysis_agent） |
| LLM 接口 | LiteLLM（统一 100+ Provider，当前 `openai/kimi-k2.5`） |
| 向量库 | Milvus（本地 Docker）+ etcd + MinIO |
| 检索策略 | Hybrid Retrieval（Dense + BM25 + BM25-primary RRF / 对称 RRF 可切换） |
| Embedding | HuggingFace `paraphrase-multilingual-MiniLM-L12-v2`，384 维，本地推理 |
| 数据库 | PostgreSQL 16 + SQLAlchemy |
| 认证 | JWT（passlib bcrypt） |
| 前端 | Vite + Vue 3 + Pinia + Vue Router + marked.js + KaTeX |
| Admin 页面 | 独立 `admin.html`（Vue 3 CDN，单文件） |
| 日志 | Python logging + JSON Formatter + contextvars |
| 基础设施 | Docker Compose（Milvus + PostgreSQL + etcd + MinIO + 后端 + 前端 Nginx） |

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
│   │   ├── analysis_agent.py               # 文档分析（确定性流程，无 ReAct）
│   │   └── tools.py                        # 共享工具（get_current_time、search_knowledge_base、find_source、get_document_info、list_documents）
│   ├── services/
│   │   ├── llm.py                          # LLM 初始化（ChatLiteLLM）
│   │   ├── chat.py                         # 核心对话服务（Supervisor 调度 + 流式 SSE + 三 Agent 分发 + citations + Query Rewriting）
│   │   └── auth.py                         # JWT 认证 + require_admin
│   ├── types/
│   │   ├── chat.py                         # Pydantic 模型
│   │   ├── user.py                         # User ORM + Base
│   │   ├── document.py                     # Document ORM
│   │   └── session.py                      # ChatSession + ChatMessage ORM
│   └── utils/
│       └── truncation.py                   # smart_truncate（head 40% + tail 40% + mid 20% 固定 seed）
├── skills/rag/
│   ├── collection.py                       # Milvus 连接 + Embedding
│   ├── ingestion.py                        # 文档加载/分块/入库 + get_document_full_text
│   ├── retrieval.py                        # 混合检索（Dense + BM25 + RRF / BM25-primary 可切换）
│   └── bm25_index.py                       # BM25 索引（jieba 分词 + 权限过滤 + 分词缓存）
├── config/
│   ├── settings.py                         # pydantic-settings 全局配置
│   ├── database.py                         # SQLAlchemy engine + SessionLocal
│   ├── logging.py                          # JSON Formatter + setup_logging + get_logger
│   └── logging_context.py                  # contextvars（session_id、user_id、run_id）
├── frontend/
│   ├── index.html                          # Vite 入口
│   ├── admin.html                          # 独立 Admin 页面（Vue 3 CDN，三 tab：stats/users/sessions）
│   ├── src/
│   │   ├── main.js                         # Vue app 创建
│   │   ├── App.vue                         # 根组件
│   │   ├── router/index.js                 # Vue Router
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
│   │   │   ├── auth.js                     # Pinia auth store
│   │   │   ├── chat.js                     # Pinia chat store（SSE 流式 + tool_start 事件）
│   │   │   └── documents.js               # Pinia documents store
│   │   └── api/
│   │       ├── client.js                   # axios 封装
│   │       ├── auth.js                     # auth API
│   │       ├── chat.js                     # chat SSE API
│   │       ├── sessions.js                 # sessions API
│   │       └── documents.js               # documents API
│   ├── dist/                               # Vite build 产出
│   ├── vite.config.js
│   └── package.json
├── data/documents/                         # 上传文档存放目录
├── docs/                                   # 各 Phase 升级方案
├── scripts/                                # 评估脚本 + 数据迁移
│   ├── eval_retrieval.py                   # RAG 检索评测（HR@8 / MRR@8）
│   ├── gen_eval_questions.py               # LLM 自动生成评测问题
│   └── eval_sets/                          # 评测集 JSON（eval_type: retrieval/routing/negative）
├── tests/                                  # 测试目录（unit/ 单测 14 case）
├── docker-compose.yml
├── .env                                    # 环境变量（不提交版本控制）
└── CLAUDE.md                               # AI 助手上下文
```

## 启动方式

```bash
# 1. 启动基础设施 + 后端 + 前端（Docker）
docker compose up -d

# 2. 本地开发后端（先停 Docker 后端容器释放 8000 端口）
docker stop mini_agent-backend-1
$env:HF_ENDPOINT = "https://hf-mirror.com"
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 3. 本地开发前端
cd frontend
npm install
npm run dev          # Vite dev server（默认 5173 端口）
npm run build        # 打包到 dist/，uvicorn 才能 serve

# 4. 访问
# 聊天界面：http://127.0.0.1:8000
# Admin 页面：http://127.0.0.1:8000/admin.html
# API 文档：http://127.0.0.1:8000/docs
```

> **注意：** 修改 `.vue` 文件后必须 `npm run build`，uvicorn serve 的是 `frontend/dist/`，不是源码。

## 升级进度

详见 [UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md) 和 [UPGRADE_PLAN2.md](docs/UPGRADE_PLAN2.md)。

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1: 基础设施与安全 | 完成 | JWT 认证、pydantic-settings、PostgreSQL、安全加固 |
| Phase 2: 文档管理 | 完成 | 全生命周期 CRUD、多格式、SHA-256 去重、用户隔离 |
| Phase 3A: RAG 混合检索 | 完成 | Dense + BM25 + RRF + 引用透传 |
| Phase 3B: 评估基线 | 完成 | 旧 20 题 HR@5=95%；新 25 题 HR@8=88%（BM25-primary），详见 scripts/eval_results/ |
| Phase 4: Multi-Agent | 完成 | Supervisor + GeneralChatAgent + RAGAgent + AnalysisAgent |
| Phase 5: 会话持久化 | 完成 | ChatSession + ChatMessage ORM，历史截断 |
| Phase 6: 前端升级 | 完成 | Vite + Vue 3 多组件 |
| Phase 7: 基础设施 | 完成 | Docker 部署、/health、限流、CPU-only torch、HuggingFace 缓存权限修复 |
| Phase 8: 生产加固 | 完成 | 结构化日志、长文档截断、Admin API + 管理页面、Supervisor 路由优化、清理旧代码 |
| Phase 9: 功能扩展 | 完成 | 跨文档对比、LiteLLM 解耦、引用卡片 UI + KaTeX、工具层升级（5 工具）、BM25 缓存、Streaming Tool Calls、Query Rewriting、PATCH 公开/私有 |

## 已知问题

- **CORS 全开**：`allow_origins=["*"]`，生产环境需收紧
- **无 Alembic**：用 `create_all()`，不能 ALTER 已有表，schema 变更需手动 SQL

## 文档

- [CLAUDE.md](CLAUDE.md) — 项目上下文、开发指南
- [UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md) — Phase 1-7 详细计划
- [UPGRADE_PLAN2.md](docs/UPGRADE_PLAN2.md) — Phase 8 详细计划
- [scripts/eval_results/](scripts/eval_results/) — 评测基线结果（JSON）
- [scripts/eval_sets/](scripts/eval_sets/) — 评测集（55 题：25 retrieval + 15 negative + 15 routing）
