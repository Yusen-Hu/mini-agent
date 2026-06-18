# Phase 5：会话持久化 — 实施总结

## 目标（来自 UPGRADE_PLAN.md）

聊天记录持久化到 PostgreSQL，支持历史回溯。

---

## 实施状态

| 计划项 | 状态 | 说明 |
|--------|------|------|
| `ChatSession` ORM 模型 | 已完成 | session_uuid(UUID), user_id, title, created_at, updated_at |
| `ChatMessage` ORM 模型 | 已完成 | session_id(FK + CASCADE), role, content, created_at |
| `ChatRequest.session_id` 改为可选 UUID 字符串 | 已完成 | `Optional[str] = None` |
| `SessionResponse` / `MessageResponse` | 已完成 | 含 `from_attributes = True` |
| 移除内存 `_session_store` | 已完成 | 改为 DB 驱动 |
| `_get_or_create_session` | 已完成 | UUID 查找 + 用户所有权验证 |
| `_load_messages` 历史截断 | 已完成 | `CHAT_HISTORY_LIMIT * 2` 条，DESC LIMIT 后 reverse |
| SystemMessage 注入 | 已完成 | `create_react_agent` 无默认 prompt，手动拼装 |
| `chat_stream` 独立 SessionLocal 生命周期 | 已完成 | 避免 FastAPI Depends close 冲突 |
| 流式断连保存（try/finally） | 已完成 | 断连也能存已生成的部分回复 |
| 会话标题自动生成（`_sanitize_title`） | 已完成 | strip 换行 → collapse 空格 → 截取 20 字符 |
| `GET /sessions` 会话列表 | 已完成 | 分页，按 updated_at 倒序 |
| `GET /sessions/{uuid}/messages` 消息列表 | 已完成 | 分页，按 created_at 正序 |
| `DELETE /sessions/{uuid}` 删除会话 | 已完成 | CASCADE 自动删消息 |
| `PATCH /sessions/{uuid}` 改标题 | 已完成 | Query 参数传 title |
| 前端 session_id 传递 | 已完成 | localStorage 持久化 + SSE [SESSION:uuid] 接收 |
| 前端"新建会话"按钮 | 已完成 | 清空 session_id + 重置消息 |
| 前端 `loadSession` 首屏加载 | 已完成 | token + sessionId 存在时自动拉取历史 |
| 错误降级（401/404/500） | 已完成 | 401→logout, 其他→newSession |
| `CHAT_HISTORY_LIMIT` 配置项 | 已完成 | `config/settings.py`，默认 10 |
| 用户隔离（user_id 过滤） | 已完成 | 所有 session 端点验证所有权 |

---

## 与 UPGRADE_PLAN 的差异

### 计划外增加

| 变更 | 原因 |
|------|------|
| `SessionResponse.session_uuid` 类型从 `str` 改为 `uuid.UUID` | Pydantic v2 对 ORM 返回的 `uuid.UUID` 对象不会自动转 `str`，导致 `GET /sessions` 返回 500 |
| `session_router.py` 路由参数从 `str` 改为 `UUID` | FastAPI 原生支持 `UUID` 路径参数自动校验，省去手写 `uuid.UUID()` 解析和 `try/except` |
| 前端 `loadSession` 首屏加载 + 错误降级 | 原计划只写了"前端适配"，实际需要完整的首屏历史恢复逻辑和 401/404/500 兜底 |
| `ChatRequest.session_id` 注释修正 | 原注释"由前端生成 UUID"不准确，后端也支持无 session_id 时自动创建 |

### 计划中未实现

| 项 | 说明 |
|------|------|
| `ChatMessage.agent_name` 字段 | 需要 Phase 4 Multi-Agent 架构配合（每条消息记录由哪个 Agent 处理），当前单 Agent 无此字段，Phase 4 实现时补上 |
| 退出登录时保留 session_id | 当前策略：退出登录时清空 session_id。若要支持重新登录恢复上次会话，需配合会话列表 UI 和登录态恢复策略统一设计 |

---

## 文件变更

| 文件 | 操作 | 内容 |
|------|------|------|
| `config/settings.py` | 修改 | +`CHAT_HISTORY_LIMIT: int = 10` |
| `src/types/session.py` | 新建 | ChatSession + ChatMessage ORM |
| `src/types/chat.py` | 修改 | +SessionResponse(UUID)、SessionListResponse、MessageResponse、MessageListResponse；ChatRequest.session_id 可选 |
| `src/services/chat.py` | 重构 | 移除 `_session_store`，新增 `_sanitize_title`、`_get_or_create_session`、`_create_session`、`_load_messages`、`_save_message`；chat/chat_stream 改为 DB 驱动 |
| `src/api/routers/chat_router.py` | 修改 | +`db: Depends(get_db)` 注入；流式端点不注入 db |
| `src/api/routers/session_router.py` | 新建 | GET/DELETE/PATCH /sessions 端点 |
| `src/api/app.py` | 修改 | +session_router 注册 + ORM import |
| `frontend/index.html` | 修改 | +session_id 传递、loadSession 首屏加载、新建会话按钮、错误降级 |

---

## 验证结果

| # | 测试场景 | 操作 | 预期结果 | 实际结果 |
|---|---------|------|---------|---------|
| 1 | 新建会话 + 流式回复 | 前端发送消息，不传 session_id | 返回正常回复，SSE 包含 `[SESSION:uuid]` 标记 | 通过 |
| 2 | 刷新页面加载历史 | 发送 2 条消息后刷新页面 | 历史消息从 `/sessions/{uuid}/messages` 加载回来 | 通过 |
| 3 | 连续对话上下文 | 同一会话内发送"我刚才说了什么" | AI 能引用之前的对话内容 | 通过 |
| 4 | 新建会话隔离 | 点击"新建会话"后发送消息 | 新会话无旧消息，数据库中存在两个独立 session | 通过 |
| 5 | 重启持久化 | 重启 uvicorn 后刷新页面 | 会话和消息仍在（PostgreSQL 存储） | 通过 |
| 6 | 用户隔离 | 用户 A 的 session_uuid 传给用户 B | 返回 404（`_get_session_by_uuid` 过滤 user_id） | 通过 |
| 7 | 删除会话 | `DELETE /sessions/{uuid}` | 返回 204，会话及消息被 CASCADE 删除 | 通过 |
| 8 | GET /sessions 响应 | 调用会话列表接口 | 返回 200，session_uuid 正确序列化为字符串 | 通过（修复前为 500） |

---

## 已知问题

| 问题 | 说明 |
|------|------|
| 退出登录后无法恢复上次会话 | 退出时清空 session_id，重新登录后创建新会话。需配合 Phase 6 会话列表 UI 解决 |
| 欢迎消息 + 历史消息同屏闪烁 | 需验证：首屏先显示欢迎消息，loadSession 异步替换后是否仍有残留。当前代码逻辑上 loadSession 直接替换 messages.value，理论上不会同时存在，需在不同网络条件下确认 |
