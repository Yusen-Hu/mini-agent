# Phase 2 详细实施计划：文档管理 + 企业知识库可见性

与 [Phase 1](PHASE1_PLAN.md)、[升级总览](UPGRADE_PLAN.md) 连贯；Phase 1 已完成（认证、配置、数据库、安全加固）。

---

## 一、当前状态

> 第一节描述 Phase 2 立项时基线；代码已按下方进度表落地，以仓库为准。

### 立项基线（已归档）

| 模块 | 立项时现状 |
|------|------|
| `skills/rag/collection.py` | Milvus URI / 模型 / 维度硬编码，`dotenv` |
| `skills/rag/ingestion.py` | 仅 PDF/DOCX，chunk 硬编码，无 `document_id` |
| `skills/rag/retrieval.py` | 全局检索，无用户、无可见性 filter |
| `src/agents/tools.py` | `search_knowledge_base` 无当前用户上下文 |
| `src/api/routers/chat_router.py` | 内联 `/documents/upload`，无元数据、去重、删除 |
| `config/settings.py` | 已定义 `MILVUS_URI`、`CHUNK_*`、`UPLOAD_DIR`、`ALLOWED_EXTENSIONS` 等，RAG 未全量使用 |

### 当前进度

| Step | 内容 | 状态 |
|------|------|------|
| Step 0 | Milvus drop（开发环境） | 已完成 |
| Step 1 | `collection.py` — settings + document_id + user_id + is_public | 已完成 |
| Step 2 | `ingestion.py` — 多格式 LOADER_MAP + settings + delete/hash + 可见性字段 | 已完成 |
| Step 3 | `retrieval.py` — search_documents 增加 user_id keyword-only filter | 已完成 |
| Step 4 | `src/types/document.py` — Document ORM + is_public + Schema | 已完成 |
| Step 5 | `document_router.py` — CRUD + 按用户去重 + 权限 | 已完成 |
| Step 6 | `tools.py` + `chat.py` — contextvars 注入 user_id 到检索 | 已完成 |
| Step 7 | chat_router 清理、app.py 注册、Document import | 已完成 |
| Step 8 | 端到端测试（含跨用户隔离 + 按用户去重） | 已完成 |

---

## 二、需解决的问题

> 以下为 Phase 2 目标清单（已基本完成，见第一节进度表）。

1. RAG 模块改用 `settings`，去掉硬编码。
2. 支持白名单内 **TXT / MD / HTML（及 .htm）**，与 `ALLOWED_EXTENSIONS` 一致。
3. **PostgreSQL** 记录文档元数据；**SHA-256 按用户去重**（语义见下文「去重范围」）。
4. Milvus chunk 带 **`document_id`**，支持按文档删除；**企业场景**下增加 **`user_id` + `is_public`**，检索 `is_public OR user_id == 当前用户`。
5. 上传从 `chat_router` 迁出至 **`document_router`**，补齐列表/详情/删除/重索引。
6. Schema 变更后 **drop 重建** Milvus collection（开发期可丢数据）。
7. **`app.py` 须 import `Document` 模型** 再 `create_all`，否则 `documents` 表不会创建。
8. **前端**：上传接口响应形态变化时需同步 `frontend/index.html`（含 409 等）。
9. **`search_documents` / Agent 工具** 能拿到 **当前用户 id**，以应用检索 filter；`uid == 0` 时仅查 `is_public == true`。

---

## 三、企业知识库可见性（public + user_id）

### 3.1 业务语义

- **制度/全员文档**：`is_public = true`，任意登录用户检索可见。  
- **个人笔记**：`is_public = false`，仅 **所有者** `user_id` 与当前用户一致时可见。  
- **检索条件（Milvus filter）**：  
  `is_public == true  OR  user_id == <current_user_id>`  
  （具体运算符以 pymilvus 版本文档为准，常见为 `||` 与布尔/整型比较。）

### 3.2 PostgreSQL（`documents` 表）

在基础字段（`filename`, `stored_name`, `file_hash`, `file_size`, `chunk_count`, `status`, `user_id`, `created_at`）之外增加：

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_public` | `Boolean`，默认 `false` | `true` 表示全员可读（制度库） |

**权限规则（API 层）**：

- 普通用户上传：默认 `is_public=false`；仅 **`role == admin`**（或单独权限）可将 `is_public` 置为 `true`，避免任意用户把隐私文件标成全员可见。  
- 修改可见性：若 `private → public` 或反向，需 **更新 Milvus 中该 `document_id` 下全部 chunk** 的 `is_public`（或 **delete_document_chunks + 重索引**）。

### 3.3 Milvus schema（chunk 级冗余）

每条向量除 `embedding`, `text`, `source` 外至少包含：

| 字段 | 类型建议 | 说明 |
|------|----------|------|
| `document_id` | VARCHAR | 与 PG `documents.id` 字符串形式一致 |
| `user_id` | INT64 | 文档所有者，与 PG 一致 |
| `is_public` | BOOL | 与 PG 同步，便于 filter |

入库时由路由根据 DB 记录写入；**`source` 建议存原始展示文件名**（非磁盘 UUID 名），便于引用展示。

### 3.4 检索与 Agent

- `retrieval.search_documents(query, top_k, *, user_id: int)` 增加 **`user_id`**（当前登录用户），内部 `search(..., filter=...)`。  
- `tools.search_knowledge_base` 需能拿到 **当前用户**：例如在 `rag_agent` / `chat` 层 **按请求构造 tool**（闭包传入 `user_id`），或 LangGraph 的 `config`/`state` 注入；**禁止**在无用户上下文时默认全局检索（企业场景）。

### 3.5 后续扩展（本阶段可不实现，文档预留）

- 多租户：`tenant_id` + filter 中增加租户条件。  
- 部门可见：`team_id` / ACL 表，filter 变复杂，可 Phase 3+ 再做。

---

## 四、去重范围（产品语义，实施前定稿）

| 策略 | 行为 |
|------|------|
| **推荐：按用户去重** | `UNIQUE(user_id, file_hash)` 或查询时 `WHERE user_id=? AND file_hash=?`；不同用户可各存一份相同内容。 |
| **全局去重** | 全库 `file_hash` 唯一，省存储；用户 B 上传与用户 A 相同文件 → 409，多租户场景常不合适。 |

计划在 API 与测试用例中写清所选策略。

---

## 五、依赖与格式注意

- **HTML**：`BSHTMLLoader` 需 **`beautifulsoup4`**（及常见组合 **lxml**），写入 `requirements.txt`。  
- **`.htm`**：若 LOADER 支持，**`ALLOWED_EXTENSIONS` 须包含 `.htm`**。  
- **`.doc`**：老格式，`Docx2txtLoader` 可能失败；建议文档中标注「仅保证 docx」或单独选型。

---

## 六、逐步实施计划（与可见性合并版）

### Step 0：Milvus 数据（开发环境）

在首次运行**新 schema** 的 ingest 之前执行 drop（可与 Step 1 代码就绪后立刻执行）：

```bash
E:/1/python/envs/supermew/python.exe -c "
from pymilvus import MilvusClient
from config.settings import settings
MilvusClient(uri=settings.MILVUS_URI).drop_collection('knowledge_base')
print('knowledge_base 已删除，将在 init_collection 时按新 schema 创建')
"
```

---

### Step 1：`skills/rag/collection.py`

- 使用 `settings.MILVUS_URI`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`；移除 `dotenv`。  
- Schema 增加：`document_id` (VARCHAR)，`user_id` (INT64)，`is_public` (BOOL)。  
- 保留 `text`, `source`, `embedding`；索引仍以向量为主；可选为 `document_id` / `user_id` 建标量索引以优化 delete/filter（视数据量）。

---

### Step 2：`skills/rag/ingestion.py`

- `LOADER_MAP`：pdf / docx / doc（注明风险）/ txt / md / html / htm；`RecursiveCharacterTextSplitter` 使用 `settings.CHUNK_*`。  
- `ingest_document(file_path, *, document_id: str, user_id: int, is_public: bool, source_display_name: str)`：  
  - 写入 Milvus 每条带 `document_id`, `user_id`, `is_public`；`source` 使用 `source_display_name`。  
- `delete_document_chunks(document_id: str)`：`filter` 按 `document_id`。  
- `compute_file_hash(path)`：SHA-256 分块读取。

---

### Step 3：`skills/rag/retrieval.py`

- `search_documents(query, top_k=None, *, user_id: int)`，`top_k` 默认 `settings.TOP_K`。  
- `milvus_client.search(..., filter=<public or mine 表达式>, output_fields=[...,"document_id"])`。  
- 与现有 IP/HNSW 度量保持一致。

---

### Step 4：`src/types/document.py`

- ORM：`Document` 含 `is_public`（默认 `False`）；其余字段同原设计（`filename`, `stored_name`, `file_hash`, `file_size`, `chunk_count`, `status`, `user_id`, `created_at`）。  
- Pydantic：`DocumentResponse` / `DocumentListResponse`；上传请求可含可选 `is_public`（仅 admin 生效）。  
- `Base` 继续从 `src.types.user` 导入。

---

### Step 5：`src/api/routers/document_router.py`

- 路由前缀 `/documents`；全部需 `get_current_user`。  
- **POST `/upload`**：扩展名校验、UUID 磁盘名、流式大小限制 → 按策略去重 → 写 DB（`processing`）→ `ingest_document(..., document_id=str(doc.id), user_id=doc.user_id, is_public=doc.is_public, source_display_name=doc.filename)` → 更新 `chunk_count` / `status`。  
- **GET 列表/详情/DELETE/reindex**：仅允许操作 **`user_id == current_user.id`** 的文档；**删除**时 `delete_document_chunks` + 删文件 + 删 DB。  
- **reindex**：先删 Milvus 旧 chunk，再 ingest；可见性变更若走「全量重索引」可与此统一。

---

### Step 6：`src/agents/tools.py` + `src/services/chat.py`（或 agent 装配层）

- 使 `search_knowledge_base` 在调用 `search_documents` 时传入 **当前请求的 `user_id`**（由 chat 入口注入 tool 或包装函数）。  
- 无用户上下文时（若保留匿名调试）：可拒绝检索或仅 `is_public`（按产品定，企业默认应要求登录）。

---

### Step 7：清理与注册

- **`src/api/routers/chat_router.py`**：移除 `/documents/upload` 及相关 import。  
- **`src/api/app.py`**：  
  - `from src.types.document import Document  # noqa: F401`（确保 `create_all` 建表）。  
  - `app.include_router(document_router)`。  
- **`frontend/index.html`**：上传 URL、响应 JSON（`DocumentResponse`）、409/413 提示；Bearer 头保持一致。

---

### Step 8：测试与端到端

- PG + Milvus 启动；drop 后重启应用；注册登录 → 普通用户上传 → 列表/删除/重复上传（409）→ 另一用户上传同内容（若按用户去重应 **不** 409）→ **用户 A 不应检索到用户 B 的 private 文档**；admin 上传 `is_public=true` 后 **用户 B 应能检索到该文档**。

---

## 七、执行顺序总结

```
Step 0   Milvus drop（开发环境，在首次新 schema ingest 前）
Step 1   collection.py（settings + document_id + user_id + is_public）
Step 2   ingestion.py（多格式、ingest/delete/hash、传入可见性字段）
Step 3   retrieval.py（按用户 + public 的 filter）
Step 4   types/document.py（ORM + is_public + Schema）
Step 5   document_router.py（CRUD + 权限 + admin 标 public）
Step 6   tools + chat/agent（注入 user_id 到检索）
Step 7   chat_router 清理、app.py import Document + 注册路由、前端
Step 8   端到端测试（含跨用户检索用例）
```

---

## 八、与 Phase 3 的边界

- **混合检索、Rerank、引用 SSE** 留在 Phase 3（见 `UPGRADE_PLAN.md`）。  
- Phase 2 完成：**元数据、多格式、去重、按文档删除、企业可见性 + 检索隔离**。

---

## 九、已知风险与文档债

- `create_all` 仅开发友好；生产用 Alembic 迁移。
- Milvus 与 PG **强一致**需事务外补偿（失败标记 `status=error` 等）。
- 全局异常与 `DEBUG` 下是否暴露 `detail` 与 Phase 1 策略保持一致。

---

## 附录 A：pymilvus 2.4 filter 语法（已验证）

Milvus filter 表达式用于 `milvus_client.search(..., filter=...)` 和 `delete(..., filter=...)`。

| 语法点 | 正确写法 | 错误写法 |
|--------|----------|----------|
| 布尔值 | `is_public == true` | `is_public == True`（Python 写法，Milvus 不认） |
| 逻辑或 | `a or b` | `a \|\| b`（某些版本不支持） |
| 逻辑与 | `a and b` | `a && b` |
| 字符串 | `document_id == "123"` | `document_id == '123'`（单引号可能报错） |
| 整数 | `user_id == 42` | 直接写，无需引号 |

**已验证示例（pymilvus 2.4.0 + Milvus v2.4.0）：**

```python
# 检索：公开文档 或 当前用户的文档
filter_expr = f"is_public == true or user_id == {user_id}"

# 按文档 ID 删除
filter_expr = f'document_id == "{document_id}"'

# 组合：某用户的公开文档
filter_expr = f"user_id == {user_id} and is_public == true"
```

> **注意：** 代码中 `search_documents` 的 `user_id` 参数为 keyword-only（`*, user_id: int`），无默认值。禁止用 `0` 表示匿名；未登录时不调用检索，或单独写 filter 仅查 `is_public == true`。

---

## 附录 B：管理员列表接口（Phase 2 不做，预留）

当前 Phase 2 的 `GET /documents` 仅返回 `user_id == current_user.id` 的文档。企业场景下管理员需要审核所有 `is_public=true` 的文档。

**Phase 4+ 预留接口：**

| 方法 | 路径 | 权限 | 功能 |
|------|------|------|------|
| GET | `/admin/documents?is_public=true` | admin 角色 | 列出所有公开文档，用于审核 |

Phase 2 不实现此接口，避免提前引入角色权限逻辑（`role` 检查、admin 路由前缀等）。
