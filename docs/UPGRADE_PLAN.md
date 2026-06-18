# 企业级文档检索与知识库问答助手 — 升级方案

## 背景

当前项目采用模块化架构（src/、skills/、config/ 等），支持 RAG 知识库检索和流式对话。
存在无认证、无持久化、文档管理缺失、检索质量有限等问题。本方案分 7 个阶段升级为企业级产品。

---

## 分阶段计划

### Phase 1: 基础设施与安全（约 2 天） ✅ 已完成

**目标：** 搭建配置管理、数据库、用户认证等基础能力。

| 文件 | 操作 | 内容 |
|------|------|------|
| `requirements.txt` | 新建 | 锁定所有依赖版本 |
| `config/settings.py` | 实现 | pydantic-settings 集中管理所有配置（DB、Milvus、JWT、Embedding、上传等），替换散落的 `os.getenv()` |
| `config/database.py` | 实现 | SQLAlchemy engine + `get_db()` FastAPI 依赖 |
| `src/types/user.py` | 新建 | User Pydantic/ORM 模型（id, username, email, hashed_password, role, created_at） |
| `src/services/auth.py` | 新建 | bcrypt 密码哈希、JWT 签发/验证、`get_current_user` 依赖 |
| `src/api/routers/auth_router.py` | 新建 | POST /auth/register、POST /auth/login、GET /auth/me |
| `src/api/middleware.py` | 实现 | 全局异常处理，结构化错误响应 |
| `src/api/app.py` | 修改 | 引入 auth_router、注入 `get_current_user` 到受保护端点、修复路径穿越（UUID 文件名 + 扩展名白名单）、收紧 CORS |
| `docker-compose.yml` | 修改 | 添加 PostgreSQL 16 服务 |
| `migrations/` | 新建 | Alembic 数据库迁移配置 + 首次迁移 |

**关键改动：**
- 文件上传不再使用 `file.filename` 拼路径，改用 `uuid4.hex + 扩展名`，校验 `ALLOWED_EXTENSIONS`
- CORS 从 `allow_origins=["*"]` 改为配置化白名单
- 所有 `.env` 配置项统一收归 `config/settings.py`

---

### Phase 2: 文档管理 + 企业可见性（约 1.5 天） ✅ 已完成

**目标：** 实现文档全生命周期管理，支持多格式，按用户去重，企业级检索隔离。

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/types/document.py` | 新建 | Document ORM 模型（filename, stored_name, file_hash, file_size, chunk_count, status, **is_public**, user_id, created_at）+ DocumentResponse / DocumentListResponse |
| `skills/rag/collection.py` | 修改 | 使用 `settings` 替换硬编码；Milvus schema 新增 `document_id`（VARCHAR）、`user_id`（INT64）、`is_public`（BOOL） |
| `skills/rag/ingestion.py` | 修改 | ① 用 `settings` 替换硬编码 ② 新增 TXT/MD/HTML 加载器 ③ `ingest_document` 增加可见性字段 ④ 新增 `delete_document_chunks`、`compute_file_hash` |
| `skills/rag/retrieval.py` | 修改 | `search_documents` 增加 keyword-only `user_id` 参数；Milvus filter：`is_public == true or user_id == {uid}` |
| `src/agents/tools.py` | 修改 | 用 `contextvars` 注入当前用户 ID，`search_knowledge_base` 检索时传入 `user_id` |
| `src/services/chat.py` | 修改 | `chat`/`chat_stream` 接受 `user_id` 参数，设置 contextvars |
| `src/api/routers/document_router.py` | 新建 | POST /documents/upload（按用户去重）、GET /documents（分页，仅自己的）、GET /documents/{id}、DELETE /documents/{id}、POST /documents/{id}/reindex |
| `src/api/routers/chat_router.py` | 修改 | 移除内联 upload；`chat`/`chat_stream` 传入 `current_user.id` |
| `src/api/app.py` | 修改 | import Document（确保 create_all）、include document_router |

**支持格式：** PDF、DOCX/DOC、TXT、MD、HTML/HTM

**关键改动：**
- **按用户 SHA-256 去重**（`UNIQUE user_id + file_hash`），不同用户可各存相同内容
- 文档状态流转：`processing` → `ready` / `error`
- Milvus chunk 通过 `document_id` + `user_id` + `is_public` 关联，支持按文档删除和检索隔离
- **企业可见性**：`is_public=true` 全员可检索，`is_public=false` 仅上传者可检索
- `search_documents` 签名为 `*, user_id: int`（keyword-only，无默认值，漏传直接 TypeError）

---

### Phase 3: RAG 检索质量提升（约 2 天） ✅ 已完成

**目标：** 混合检索 + Rerank + 引用溯源，大幅提升回答质量。

| 文件 | 操作 | 内容 |
|------|------|------|
| `skills/rag/retrieval.py` | 重构 | 混合检索引擎：Dense（Milvus 向量）+ Sparse（Milvus BM25 内置函数）+ RRF 融合 + Rerank |
| `skills/rag/collection.py` | 修改 | Milvus Collection 扩展 `sparse_embedding`（SPARSE_FLOAT_VECTOR）+ BM25 Function |
| `src/agents/tools.py` | 修改 | ① `search_knowledge_base` 返回结构化引用元数据 ② 新增 `rewrite_query` 工具做查询改写/分解 |
| `src/types/chat.py` | 扩展 | ChatRequest 增加 `top_k`、`score_threshold`、`enable_rerank` 可选参数 |

**检索流程（升级后）：**
```
用户问题 → 查询改写（可选）
  → Dense 向量检索（Milvus, top_k*2）
  → Sparse BM25 检索（Milvus, top_k*2）
  → RRF 融合排序
  → Cross-encoder Rerank（可选，~80MB 模型）
  → 返回 top_k 结果 + 引用信息
```

**关键改动：**
- 引用以独立 SSE 事件推送给前端：`data: {"type": "citation", ...}\n\n`
- Rerank 可通过请求参数开关，资源受限时可关闭
- Milvus schema 变更需要重建 Collection（写迁移脚本导出/导入数据）

---

### Phase 4: Multi-Agent 多智能体协作（约 1.5 天） ✅ 已完成

**目标：** 从单 Agent 架构升级为 Supervisor + 多子 Agent 协作模式，每条消息多约 1 次轻量级 LLM 调用（Supervisor 路由 + 查询预处理，约 ~100 token），成本可忽略。

**架构设计：**
```
用户消息 → Supervisor Agent（预处理 + 路由，max_tokens=200）
    │
    ├─ 通用对话意图 → GeneralChatAgent
    │   • 不调用工具，直接回答闲聊/通用问题
    │   • 省去不必要的 RAG 检索开销
    │
    ├─ 文档检索意图 → RAGAgent
    │   • 调用 search_knowledge_base 工具
    │   • 混合检索 + Rerank（继承 Phase 3 能力）
    │   • 返回带引用的回答
    │
    └─ 文档分析意图 → AnalysisAgent
        • 读取整篇/多篇文档内容
        • 做摘要、对比、关键信息提取
        • 调用专用工具（如 summarize_document、compare_documents）
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/agents/supervisor.py` | 实现 | Supervisor Agent：查询预处理（标准化模糊输入）+ 意图分析 + 路由到子 Agent。一次调用完成两项工作，输出 JSON `{"query": "标准化后", "agent": "target"}` |
| `src/agents/general_chat.py` | 实现 | GeneralChatAgent：通用对话，无工具调用，直接用 LLM 回答 |
| `src/agents/rag_agent.py` | 重构 | RAGAgent：继承现有 search_knowledge_base 能力，混合检索 + 引用 |
| `src/agents/analysis_agent.py` | 实现 | AnalysisAgent：文档分析专用，含 summarize_document、compare_documents 工具 |
| `src/agents/tools.py` | 扩展 | 新增 summarize_document、compare_documents 工具 |
| `skills/rag/ingestion.py` | 修改 | 新增 `get_document_full_text(document_id)` 函数，供 AnalysisAgent 读取整篇文档 |
| `src/types/chat.py` | 扩展 | ChatRequest 增加 `agent_hint` 可选字段（允许前端强制指定 Agent，用于调试） |
| `protocols/routing_rules.yml` | 新建 | Supervisor 路由规则定义 |

**Supervisor 路由逻辑（Prompt 设计）：**
```
你是智能助手的前置处理器。完成两件事：
1. 将用户输入标准化为清晰的查询语句（补全省略、展开缩写、纠正错别字）
2. 判断应交给哪个 Agent 处理

输出 JSON：{"query": "标准化后的查询", "agent": "general_chat / rag_agent / analysis_agent"}
```

**关键改动：**
- 每次提问增加 1 次 Supervisor 调用（~100 token，成本约 0.001 元），同时完成预处理和路由
- 子 Agent 复用同一个 LLM 实例，通过不同 system prompt 区分能力
- 前端可在消息中显示当前是哪个 Agent 在回答（提升透明度）
- AnalysisAgent 新增两个工具需要 Phase 2 的文档管理能力支撑

**API 调用量分析：**

| 场景 | 单 Agent | Multi-Agent | 增量 |
|------|---------|-------------|------|
| 闲聊 | 1 次 | 2 次（Supervisor + GeneralChat） | +1 次，~100 token |
| 文档检索 | 1-3 次 | 2-4 次（+Supervisor） | +1 次，~100 token |
| 文档分析 | 1-3 次 | 2-4 次（+Supervisor） | +1 次，~100 token |
| **月增成本**（日均 100 次提问） | — | — | **约 +3 元/月** |

---

### Phase 5: 会话持久化（约 1 天） ✅ 已完成

**目标：** 聊天记录持久化到 PostgreSQL，支持历史回溯。

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/types/session.py` | 新建 | ChatSession 模型（session_uuid, user_id, title, created_at, updated_at）+ ChatMessage 模型（session_id, role, content, tool_calls, agent_name, created_at） |
| `src/services/chat.py` | 修改 | 移除内存 `_session_store`，改为从 DB 加载/保存消息；记录每条消息由哪个 Agent 处理；流式断开时用 try/finally 保存不完整回复 |
| `src/api/routers/session_router.py` | 新建 | GET /sessions、GET /sessions/{uuid}/messages（分页）、DELETE /sessions/{uuid}、PATCH /sessions/{uuid}（改名） |
| `src/api/app.py` | 修改 | include session_router；chat 端点注入 db 依赖 |
| `src/types/chat.py` | 扩展 | SessionResponse、MessageResponse |

**关键改动：**
- session_id 从字符串改为 UUID
- 不传 session_id 时自动创建新会话，标题从首条消息自动生成
- ChatMessage 表增加 `agent_name` 字段，记录消息由哪个 Agent 处理（便于分析和调试）

---

### Phase 6: 前端升级（约 2 天） ✅ 已完成

**目标：** 从单文件升级为 Vite + Vue 3 多组件应用。

| 新文件 | 内容 |
|--------|------|
| `frontend/src/views/LoginView.vue` | 登录/注册页面，JWT 存储 |
| `frontend/src/views/ChatView.vue` | 主聊天界面 |
| `frontend/src/components/Sidebar.vue` | 侧栏（会话列表 + 文档列表两个 Tab） |
| `frontend/src/components/SessionList.vue` | 会话列表，点击切换历史 |
| `frontend/src/components/MessageBubble.vue` | 消息气泡 + Markdown 渲染 |
| `frontend/src/components/CitationCard.vue` | 引用卡片，可展开查看原文 |
| `frontend/src/components/DocumentUpload.vue` | 拖拽上传 + 进度条 + 状态轮询 |
| `frontend/src/components/DocumentList.vue` | 文档列表 + 删除按钮 |
| `frontend/src/components/AgentBadge.vue` | Agent 标识徽章，显示当前回答由哪个 Agent 处理 |
| `frontend/src/api/client.js` | axios 实例，base URL 可配置，自动附加 JWT |
| `frontend/src/stores/auth.js` | Pinia 用户/token 状态 |
| `frontend/src/stores/chat.js` | Pinia 会话/消息状态 |
| `frontend/src/stores/documents.js` | Pinia 文档列表状态 |

**关键改动：**
- 后端 URL 不再硬编码，通过 `VITE_API_URL` 环境变量配置
- 支持暗色模式（CSS 变量 + localStorage 持久化）
- 引用可点击展开，显示来源文档和原文片段
- 每条 AI 回复旁显示 Agent 徽章（如 `RAG`、`Chat`、`Analysis`），提升可追溯性

---

### Phase 7: 基础设施与运维（约 1 天） ✅ 已完成

| 文件 | 操作 | 内容 |
|------|------|------|
| `Dockerfile` | 新建 | Python 3.12-slim，多阶段构建（根目录） |
| `frontend/Dockerfile` | 新建 | Node 20 构建 + Nginx 部署 |
| `nginx/nginx.conf` | 新建 | 反向代理（/api/ → backend:8000，`proxy_buffering off` 保证 SSE），SPA 路由 |
| `docker-compose.yml` | 修改 | 添加 backend、frontend、nginx 服务定义 |
| `src/api/app.py` | 修改 | 添加 /health 端点（检查 DB + Milvus）、structlog 结构化日志、slowapi 限流（30/min） |

---

## 执行顺序

```
Phase 1（基础）──→ Phase 2（文档管理）──→ Phase 3（RAG 质量）──→ Phase 4（Multi-Agent）
                ──→ Phase 5（会话持久化）──→ Phase 6（前端）
                                            ──→ Phase 7（基础设施）
```

Phase 2 和 Phase 5 可并行开发。总工期约 11 天。

---

## 验证方式

每个 Phase 完成后：
1. 启动服务：`docker compose up -d` + `uvicorn src.api.app:app --reload`
2. 通过 `/docs` Swagger UI 测试新增端点
3. Phase 1：注册用户 → 登录获取 JWT → 带 token 访问 /chat
4. Phase 2：上传 PDF/TXT → 重复上传（409）→ 文档列表 → 删除 → 验证 Milvus chunk 清理 → 跨用户隔离测试 → 公开文档可被检索
5. Phase 3：对比同一问题在混合检索 vs 纯向量检索的结果差异
6. Phase 4：发送闲聊消息 → 确认走 GeneralChatAgent → 发送文档问题 → 确认走 RAGAgent → 发送"总结这份文档" → 确认走 AnalysisAgent
7. Phase 5：创建会话 → 多轮对话 → 重启服务 → 验证历史仍在
8. Phase 6：浏览器测试登录、聊天、上传、引用展开、Agent 徽章显示、暗色模式
9. Phase 7：`docker compose up` 全栈启动 → `curl localhost/health` → 通过 Nginx 访问完整应用

---

## 文件变更总览

| 文件 | 操作 | 阶段 |
|------|------|------|
| `config/settings.py` | 实现（已有占位） | 1 |
| `config/database.py` | 实现（已有占位） | 1 |
| `src/types/user.py` | 新建 | 1 |
| `src/types/document.py` | 新建 | 2 |
| `src/types/session.py` | 新建 | 5 |
| `src/types/chat.py` | 扩展 | 2, 3, 4, 5 |
| `src/services/auth.py` | 新建 | 1 |
| `src/api/middleware.py` | 实现（已有占位） | 1 |
| `src/api/routers/auth_router.py` | 新建 | 1 |
| `src/api/routers/document_router.py` | 新建 | 2 |
| `src/api/routers/session_router.py` | 新建 | 5 |
| `src/api/app.py` | 修改 | 1, 2, 5, 7 |
| `src/agents/supervisor.py` | 实现（已有占位） | 4 |
| `src/agents/general_chat.py` | 实现（已有占位） | 4 |
| `src/agents/analysis_agent.py` | 实现（已有占位） | 4 |
| `src/agents/rag_agent.py` | 重构 | 4 |
| `src/agents/tools.py` | 修改 | 2, 3, 4 |
| `src/services/chat.py` | 修改 | 2, 5 |
| `skills/rag/retrieval.py` | 修改 | 2, 3 |
| `skills/rag/collection.py` | 修改 | 2, 3 |
| `skills/rag/ingestion.py` | 修改 | 2, 4 |
| `protocols/routing_rules.yml` | 新建 | 4 |
| `frontend/` | 整体重建 | 6 |
| `docker-compose.yml` | 修改 | 1, 7 |
| `requirements.txt` | 新建 | 1 |
| `migrations/` | 新建 | 1 |
| `Dockerfile` | 新建（根目录） | 7 |
| `frontend/Dockerfile` | 新建 | 7 |
| `nginx/nginx.conf` | 新建 | 7 |
