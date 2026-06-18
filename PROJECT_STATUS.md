# Mini Agent — 项目状态与技术文档

> 最后更新：2026-05-14
> 本文档面向开发者，全面描述项目当前实现状态、架构细节和未来规划。

---

## 一、项目概述

Mini Agent 是一个基于 LangGraph 的 AI 对话应用，支持 RAG 知识库检索和流式对话。用户可上传文档到知识库，Agent 在回答时自动检索相关内容。

### 技术栈

| 层级 | 技术 | 版本/说明 |
|------|------|----------|
| 后端框架 | FastAPI + Uvicorn | Python 3.12 |
| Agent 引擎 | LangGraph `create_react_agent` | ReAct 模式 |
| LLM | Moonshot API（OpenAI 兼容） | `moonshot-v1-8k` |
| 向量库 | Milvus（Docker） | v2.4.0 + etcd + MinIO |
| Embedding | HuggingFace | `paraphrase-multilingual-MiniLM-L12-v2`，384 维 |
| 关系数据库 | PostgreSQL 16（Docker） | SQLAlchemy ORM |
| 认证 | JWT（python-jose） | bcrypt 密码哈希 |
| 前端 | Vue 3 + Vite | Pinia + Vue Router + axios |
| 基础设施 | Docker Compose | 4 服务（postgres + etcd + minio + milvus） |

### 启动方式

```bash
# 1. 启动基础设施
docker compose up -d

# 2. 启动后端（等待 1-2 分钟首次加载模型）
E:\1\python\envs\supermew\python.exe -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 3. 开发模式启动前端（另开终端）
cd frontend && npm run dev

# 4. 生产模式（不需要启动前端）
npm run build   # cd frontend && npm run build
# 然后只启动 uvicorn，自动托管 frontend/dist/

# 5. 访问
# 开发模式：http://localhost:5173
# 生产模式：http://127.0.0.1:8000
# API 文档：http://127.0.0.1:8000/docs
```

---

## 二、完成情况总览

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 1 | 已完成 | 认证、配置、数据库、安全加固 |
| Phase 2 | 已完成 | 文档管理全生命周期、多格式、去重、用户隔离 |
| Phase 3 | 待做 | 混合检索、Rerank、引用溯源 |
| Phase 4 | 待做 | Multi-Agent（Supervisor + 子 Agent） |
| Phase 5 | 已完成 | 会话持久化 |
| Phase 6 | 已完成 | 前端升级（Vite + Vue 3） |
| Phase 7 | 待做 | Docker 部署、Nginx、监控 |

---

## 三、Phase 1：基础设施与安全

### 3.1 配置管理

`config/settings.py` — pydantic-settings，从 `.env` 加载：

```python
class Settings(BaseSettings):
    ARK_API_KEY: str = ""           # Moonshot API Key
    MODEL: str = "moonshot-v1-8k"
    BASE_URL: str = "https://api.moonshot.cn/v1"
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent"
    MILVUS_URI: str = "http://127.0.0.1:19530"
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    TOP_K: int = 3
    CHAT_HISTORY_LIMIT: int = 10    # 每次加载最近 N 轮对话
    UPLOAD_DIR: str = "data/documents"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".docx", ".doc", ".txt", ".md", ".html"]
    CORS_ORIGINS: List[str] = ["*"]
```

### 3.2 数据库

`config/database.py` — SQLAlchemy engine + session：

```python
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 3.3 认证

`src/services/auth.py` — JWT 认证中间件：

- **密码哈希**：bcrypt（passlib）
- **Token 生成**：python-jose，payload `{"sub": user_id, "exp": ...}`
- **Token 验证**：`get_current_user` 依赖注入，解析 JWT → 查 DB → 返回 User 对象

### 3.4 安全加固

- 密码 bcrypt 哈希存储，明文不落库
- JWT 过期时间可配置（默认 24 小时）
- 文件上传：UUID 文件名防路径穿越、流式读取 + 大小限制、文本文件空字节检测
- 按用户 ID 隔离数据（文档、会话、检索）
- 全局异常处理器（middleware.py）：不泄露内部错误

---

## 四、Phase 2：文档管理

### 4.1 数据库模型

`src/types/document.py`：

```python
class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(256), nullable=False)           # 原始文件名
    stored_name = Column(String(256), nullable=False)        # UUID 文件名
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    file_size = Column(BigInteger, nullable=False)
    chunk_count = Column(Integer, default=0)
    status = Column(String(16), default="processing")        # processing / ready / error
    is_public = Column(Boolean, default=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### 4.2 API 路由

前缀 `/api/documents`，所有端点需认证：

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/documents/upload` | 上传文档 → 加载 → 分块 → Embedding → 入 Milvus |
| GET | `/documents` | 文档列表（分页，按用户过滤） |
| GET | `/documents/{id}` | 文档详情 |
| DELETE | `/documents/{id}` | 删除文档（磁盘 + Milvus chunks + DB 记录） |
| POST | `/documents/{id}/reindex` | 重建索引（删除旧 chunks → 重新入库） |

### 4.3 入库流程

`skills/rag/ingestion.py`：

```
文件上传 → UUID 重命名存盘
  → charset_normalizer 探测编码（支持 UTF-8/GB18030/BIG5/BOM）
  → PyPDFLoader / Docx2txtLoader / TextLoader / BSHTMLLoader 加载
  → RecursiveCharacterTextSplitter 分块（chunk_size=500, overlap=50）
  → HuggingFace Embedding 批量向量化（384 维，CPU 推理）
  → Milvus insert（Collection: knowledge_base）
```

### 4.4 Milvus Collection Schema

`skills/rag/collection.py`：

```python
schema = milvus_client.create_schema(auto_id=True, enable_dynamic_field=True)
schema.add_field("id", DataType.INT64, is_primary=True)
schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=384)
schema.add_field("text", DataType.VARCHAR, max_length=65535)
schema.add_field("source", DataType.VARCHAR, max_length=512)       # 显示用文件名
schema.add_field("document_id", DataType.VARCHAR, max_length=36)   # 关联 DB documents.id
schema.add_field("user_id", DataType.INT64)
schema.add_field("is_public", DataType.BOOL)

index: HNSW on embedding, metric=IP, M=8, efConstruction=64
```

### 4.5 当前检索

`skills/rag/retrieval.py`：

```python
def search_documents(query, top_k=None, *, user_id, public_only=False):
    query_vector = embeddings.embed_query(query)
    filter_expr = "is_public == true or user_id == {user_id}"
    results = milvus_client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector], limit=top_k, filter=filter_expr,
        output_fields=["text", "source", "document_id"],
    )
    # 返回 [{text, source, document_id, score}]
```

纯 Dense 向量检索，无 BM25、无 Rerank、无引用溯源。

### 4.6 去重机制

SHA-256 按用户去重：上传时计算 `file_hash`，查 DB 是否已有相同 hash 且非 error 状态的记录。`compute_file_hash` 存在但入库时才校验。

---

## 五、Phase 5：会话持久化

### 5.1 数据库模型

`src/types/session.py`：

```python
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**设计要点：**
- `session_id` API 层暴露 UUID（防枚举），内部 JOIN 用 Integer
- 外键 CASCADE 删除（删 session 自动删 messages）

### 5.2 Pydantic 模型

`src/types/chat.py`：

```python
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None  # None = 新会话，后端自动分配 UUID

class ChatResponse(BaseModel):
    reply: str
    session_id: str

class SessionResponse(BaseModel):
    session_uuid: UUID  # 注意：是 UUID 类型，非 str（Pydantic v2 兼容）
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

class SessionListResponse(BaseModel):
    total: int
    sessions: List[SessionResponse]

class MessageResponse(BaseModel):
    role: str
    content: str
    created_at: datetime

class MessageListResponse(BaseModel):
    total: int
    messages: List[MessageResponse]
```

### 5.3 对话服务

`src/services/chat.py` 核心逻辑：

**同步对话 `chat()`：**
```
1. get_or_create_session（UUID → ChatSession，验证 user_id）
2. _load_messages（最近 CHAT_HISTORY_LIMIT*2 条，正序）
3. 组装 [SystemMessage] + 历史 + [HumanMessage]
4. agent.invoke({"messages": messages}, recursion_limit=10)
5. 保存 user + assistant 消息
6. 返回 (reply, session_uuid_str)
```

**流式对话 `chat_stream()`：**
```
- 独立 SessionLocal 生命周期（避免 FastAPI Depends close 冲突）
- with SessionLocal() as db：获取 session、加载历史、保存用户消息
- agent.astream(stream_mode="messages")：逐 token yield
- 过滤 langgraph_node == "agent"（只取最终回复）
- finally 块：保存 assistant 回复（断连也能存已生成的部分）
- 最后 yield [SESSION:uuid] + [DONE]
```

**System Prompt：**
```python
SYSTEM_PROMPT = (
    "你是 Mini Agent，一个专业的 AI 助手。"
    "你可以检索知识库回答用户问题，也可以获取当前时间。"
    "请根据对话历史和用户的问题，给出准确、有帮助的回答。"
)
```

### 5.4 会话 API

前缀 `/api/sessions`，所有端点需认证，按 user_id 过滤：

| 方法 | 路径 | 功能 | 参数 |
|------|------|------|------|
| GET | `/sessions` | 会话列表 | `page`, `page_size`，按 updated_at 倒序 |
| GET | `/sessions/{uuid}/messages` | 消息列表 | `page`, `page_size`，按 created_at 正序 |
| DELETE | `/sessions/{uuid}` | 删除会话 | CASCADE 删 messages，返回 204 |
| PATCH | `/sessions/{uuid}` | 修改标题 | `title` query parameter |

所有端点验证 session 存在且 `user_id` 匹配。

---

## 六、Phase 6：前端升级

### 6.1 目录结构

```
frontend/
├── index.html                  # Vite 入口
├── package.json
├── vite.config.js              # proxy: '/api' → http://127.0.0.1:8000
├── dist/                       # npm run build 产物
│   ├── index.html
│   └── assets/
├── src/
│   ├── main.js                 # createApp + router + pinia
│   ├── App.vue                 # router-view 壳子
│   ├── router/index.js         # /login, /chat, beforeEach 守卫
│   ├── stores/
│   │   ├── auth.js             # token, user, login/register/logout
│   │   └── chat.js             # messages, sessionId, sessionList, SSE 解析
│   ├── api/
│   │   ├── client.js           # axios 实例 + JWT 拦截器 + 401 处理
│   │   ├── auth.js             # register, login, getMe
│   │   ├── chat.js             # chatStream（fetch，非 axios，因 SSE 需要 reader）
│   │   └── sessions.js         # listSessions, getMessages, delete, rename
│   ├── views/
│   │   ├── LoginView.vue       # 登录/注册表单
│   │   └── ChatView.vue        # 主布局 + 首屏恢复逻辑
│   └── components/
│       ├── Sidebar.vue         # 侧边栏 + 新建会话
│       ├── SessionList.vue     # 会话列表 + 点击切换 + 高亮
│       ├── MessageList.vue     # 消息气泡 + Markdown + thinking 动画 + 自动滚动
│       └── InputBar.vue        # textarea + 发送/停止
```

### 6.2 架构分层

| 层 | 职责 | 边界 |
|----|------|------|
| View | 布局和页面级协调 | 不写业务逻辑，不直接调 API |
| Store | 状态和业务动作 | 不管 UI 细节（thinking 动画归组件） |
| API | 请求封装 | 不管状态，每个文件对应一个后端路由域 |
| Component | 展示和交互 | 不解析 SSE 协议，只消费纯文本 |

### 6.3 SSE 协议处理

**后端协议（chat.py）：**
```
data: {普通文本}\n\n          # 逐 token 输出
data: [SESSION:{uuid}]\n\n    # 会话 ID 标记（仅首次消息）
data: [DONE]\n\n              # 结束标记
```

**前端解析（stores/chat.js）：**
- chat store 统一解析 SSE 协议
- `[SESSION:uuid]` → 更新 sessionId + localStorage
- `[DONE]` → 停止读取
- 其余内容拼接为 reply，更新 messages
- 组件不参与协议解析

### 6.4 路由守卫

`router/index.js` 的 `beforeEach`：
- 未登录访问 `/chat` → 重定向 `/login`
- 已登录访问 `/login` → 重定向 `/chat`
- 用 `router.replace('/login')` 跳转（不污染 history）

### 6.5 401 拦截器

`api/client.js` 的响应拦截器：
```javascript
if (err.response?.status === 401 && localStorage.getItem('access_token')) {
  // token 过期被踢：清理 + 跳转
  localStorage.removeItem('access_token')
  localStorage.removeItem('session_id')
  window.location.href = '/login'
}
// 手动退出已由 auth.logout() 清了 token，这里检测到没 token 就跳过
```

### 6.6 首屏恢复策略

ChatView 的 `onMounted`：
```
1. loadSessionList() → 拿会话列表
2. 检查 localStorage 的 session_id
   ├─ 存在且在列表中 → loadSession(该 uuid)
   └─ 不存在或不在列表中
       ├─ 列表非空 → loadSession(最新一条)
       └─ 列表为空 → 显示欢迎消息
```

### 6.7 静态资源构建

生产模式下 FastAPI 托管 `frontend/dist/`：

```python
DIST_DIR = "frontend/dist"
if os.path.isdir(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(os.path.join(DIST_DIR, "index.html"))
```

开发模式下 `dist/` 不存在，跳过静态托管，由 Vite dev server 负责前端。

**路由优先级：**
```
/api/*       → API 路由（最先匹配）
/assets/*    → 静态资源（JS/CSS）
/{任意路径}  → SPA fallback（返回 index.html）
```

---

## 七、API 路由总览

所有接口统一 `/api` 前缀：

| 前缀 | 方法 | 路径 | 功能 | 认证 |
|------|------|------|------|------|
| `/api/auth` | POST | `/auth/register` | 注册 | 否 |
| `/api/auth` | POST | `/auth/login` | 登录 | 否 |
| `/api/auth` | GET | `/auth/me` | 获取当前用户 | 是 |
| `/api/chat` | POST | `/chat` | 同步对话 | 是 |
| `/api/chat` | POST | `/chat/stream` | 流式对话（SSE） | 是 |
| `/api/documents` | POST | `/documents/upload` | 上传文档 | 是 |
| `/api/documents` | GET | `/documents` | 文档列表 | 是 |
| `/api/documents` | GET | `/documents/{id}` | 文档详情 | 是 |
| `/api/documents` | DELETE | `/documents/{id}` | 删除文档 | 是 |
| `/api/documents` | POST | `/documents/{id}/reindex` | 重建索引 | 是 |
| `/api/sessions` | GET | `/sessions` | 会话列表 | 是 |
| `/api/sessions` | GET | `/sessions/{uuid}/messages` | 消息列表 | 是 |
| `/api/sessions` | DELETE | `/sessions/{uuid}` | 删除会话 | 是 |
| `/api/sessions` | PATCH | `/sessions/{uuid}` | 修改标题 | 是 |

---

## 八、数据库表结构

### users

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| username | VARCHAR(64) | 唯一，索引 |
| email | VARCHAR(256) | 唯一，索引 |
| hashed_password | VARCHAR(256) | bcrypt 哈希 |
| role | VARCHAR(16) | 默认 "user" |
| created_at | TIMESTAMPTZ | 自动生成 |

### documents

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| filename | VARCHAR(256) | 原始文件名 |
| stored_name | VARCHAR(256) | UUID 文件名 |
| file_hash | VARCHAR(64) | SHA-256，索引 |
| file_size | BIGINT | 字节数 |
| chunk_count | INT | 入库 chunk 数 |
| status | VARCHAR(16) | processing/ready/error |
| is_public | BOOL | 是否公开检索 |
| user_id | INT FK → users | 所属用户 |
| created_at | TIMESTAMPTZ | 自动生成 |

### chat_sessions

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| session_uuid | UUID | 唯一，索引，API 层暴露 |
| user_id | INT FK → users | 所属用户，索引 |
| title | VARCHAR(200) | 首条消息截取 |
| created_at | TIMESTAMPTZ | 自动生成 |
| updated_at | TIMESTAMPTZ | 自动更新 |

### chat_messages

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| session_id | INT FK → chat_sessions | CASCADE 删除，索引 |
| role | VARCHAR(16) | user / assistant |
| content | TEXT | 消息内容 |
| created_at | TIMESTAMPTZ | 自动生成 |

### Milvus knowledge_base

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT64 PK | auto_id |
| embedding | FLOAT_VECTOR(384) | HNSW 索引，IP 度量 |
| text | VARCHAR(65535) | chunk 文本 |
| source | VARCHAR(512) | 显示用文件名 |
| document_id | VARCHAR(36) | 关联 DB documents.id |
| user_id | INT64 | 所属用户 |
| is_public | BOOL | 是否公开 |

---

## 九、Agent 对话流程

```
用户消息
  → src/services/chat.py
      ├─ get_or_create_session（DB）
      ├─ _load_messages（最近 N 轮，截断）
      └─ 组装 [SystemMessage] + 历史 + [HumanMessage]
  → src/agents/rag_agent.py：agent.invoke / astream
  → LangGraph ReAct 循环：
      ├─ LLM 判断是否需要调用工具
      │   ├─ get_current_time → 返回当前时间
      │   └─ search_knowledge_base → Dense 向量检索 → 返回文本片段
      └─ LLM 综合工具结果生成最终回复
  → 保存 assistant 消息到 DB
  → SSE 流式返回给前端
```

### 流式输出协议

```
data: {token}\n\n              # 逐 token 输出
data: [SESSION:{uuid}]\n\n    # 会话 ID（首次消息）
data: [DONE]\n\n              # 结束标记
```

---

## 十、未来规划

### Phase 3：RAG 检索质量提升

**目标：** 混合检索 + Rerank + 引用溯源，大幅提升回答质量。

**升级后检索流程：**
```
用户问题
  → Dense 向量检索（Milvus, top_k*2）
  → Sparse BM25 检索（Python 侧 rank_bm25, top_k*2）
  → RRF 融合排序（Reciprocal Rank Fusion, k=60）
  → Cross-encoder Rerank（可选，~80MB 模型）
  → 返回 top_k 结果 + 引用元数据
```

**计划改动：**

| 文件 | 操作 | 内容 |
|------|------|------|
| `skills/rag/collection.py` | 修改 | 加载 Rerank 模型，Milvus schema 扩展 chunk_index 字段 |
| `skills/rag/ingestion.py` | 修改 | 入库时自动赋值 chunk_index |
| `skills/rag/retrieval.py` | 重构 | 混合检索引擎：Dense + BM25 + RRF + Rerank |
| `src/agents/tools.py` | 修改 | search_knowledge_base 返回结构化引用元数据 |
| `src/services/chat.py` | 修改 | SSE 协议扩展：[CITATION:{...}] 事件 |
| `src/types/chat.py` | 扩展 | ChatRequest 增加 enable_rerank 可选参数 |
| `frontend/src/stores/chat.js` | 修改 | 解析 [CITATION:] 标记 |
| `frontend/src/components/CitationCard.vue` | 新建 | 引用展示组件 |

**关键技术决策：**
- BM25 用 Python 侧 `rank_bm25` 实现（不升级 Milvus 版本），数据量小可全量加载
- Rerank 用 `cross-encoder/ms-marco-MiniLM-L-6-v2`，启动时加载，enable_rerank 参数控制开关
- 引用数据不经过 LLM（避免遗漏），工具执行后直接透传给 SSE
- Collection 需要重建（加 chunk_index 字段），写迁移脚本导出/导入

**SSE 协议扩展：**
```
data: {token}\n\n
data: [SESSION:{uuid}]\n\n
data: [CITATION:{"source":"xxx.pdf","chunk_index":2,"score":0.85,"text":"摘要..."}]\n\n
data: [DONE]\n\n
```

### Phase 4：Multi-Agent 多智能体协作

**目标：** Supervisor 路由 + 专业子 Agent 协作。

**架构：**
```
用户消息 → Supervisor Agent（路由决策）
  ├─ General Chat Agent（通用对话，不需要工具）
  ├─ RAG Agent（知识库检索，现有 agent）
  └─ Analysis Agent（文档深度分析）
```

**计划新增：**
- `src/agents/supervisor.py` — Supervisor 路由逻辑
- `src/agents/general_chat.py` — 通用对话 Agent
- `src/agents/analysis_agent.py` — 文档分析 Agent
- 前端 AgentBadge.vue — 显示当前回答来自哪个 Agent

### Phase 7：基础设施

**目标：** 生产部署就绪。

**计划：**
- Docker 多阶段构建（后端 + 前端 build）
- Nginx 反向代理 + HTTPS
- 健康检查端点
- 日志收集（结构化日志）
- 监控（可选 Prometheus + Grafana）

---

## 十一、已知限制

| 限制 | 影响 | 后续处理 |
|------|------|---------|
| 纯 Dense 检索，无 BM25 | 关键词精确匹配差 | Phase 3 混合检索 |
| 无 Rerank | 检索结果排序不够精准 | Phase 3 Cross-encoder |
| 引用只到文件名级别 | 无法定位具体段落 | Phase 3 加 chunk_index |
| System Prompt 无引用指令 | LLM 不主动标注来源 | Phase 3 改 prompt |
| CORS 全开 | 生产环境不安全 | Phase 7 收紧 |
| Milvus 2.4.0 | 无原生 BM25 支持 | Phase 3 用 Python 侧 BM25 |

---

## 十二、环境变量

`.env` 文件（不提交版本控制）：

```env
ARK_API_KEY=sk-xxx              # Moonshot API Key
MODEL=moonshot-v1-8k
BASE_URL=https://api.moonshot.cn/v1
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent
MILVUS_URI=http://127.0.0.1:19530
JWT_SECRET_KEY=change-me-in-production
```

**Python 环境：** `E:\1\python\envs\supermew\python.exe`（Python 3.12）
**安装依赖：** `E:\1\python\envs\supermew\python.exe -m pip install <package>`
