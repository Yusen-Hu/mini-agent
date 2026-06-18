# Phase 6：前端升级 — 实施计划

## 策略

**核心目标：** 从单文件 `frontend/index.html` 升级为 Vite + Vue 3 多组件项目，搭好能承接后续 Phase 3/4 功能的前端骨架。

**交付标准：** 能登录、能聊天（流式）、能切换会话、能刷新恢复历史、API 调用统一封装。

**不做：** 暗色模式、引用卡片（留给 Phase 3）、Agent 徽章（留给 Phase 4）、拖拽上传、响应式适配、静态资源构建接入（收尾步骤）。

---

## 架构原则

| 层 | 职责 | 边界 |
|----|------|------|
| **View**（页面） | 布局和页面级协调 | 不写业务逻辑，不直接调 API |
| **Store**（Pinia） | 状态和业务动作 | 不管 UI 细节（input 内容、滚动位置归组件）。`store.loading` 表示"消息发送中"（业务态），组件据此决定是否显示 thinking 动画、禁用输入框；动画效果本身归组件管 |
| **API**（接口函数层） | 请求封装 | 不管状态，每个文件对应一个后端路由域 |
| **Component** | 展示和交互 | 不管状态持久化，不解析 SSE 协议 |

一句话：View 管布局、Store 管状态和业务、API 管请求、Component 管展示和交互。

---

## 最终目录结构

```
frontend/
├── index.html                  # Vite 入口
├── package.json
├── vite.config.js              # proxy → 127.0.0.1:8000
├── src/
│   ├── main.js                 # createApp + router + pinia
│   ├── App.vue                 # router-view 壳子，无业务逻辑
│   ├── router/
│   │   └── index.js            # /login, /chat, beforeEach 路由守卫
│   ├── stores/
│   │   ├── auth.js             # token, user, login/logout/register
│   │   └── chat.js             # messages, sessionId, sessionList, 操作方法
│   ├── api/
│   │   ├── client.js           # axios 实例 + JWT 拦截器 + 401 自动 logout
│   │   ├── auth.js             # register, login, me
│   │   ├── chat.js             # chatStream
│   │   └── sessions.js         # listSessions, getMessages, deleteSession, renameSession
│   ├── views/
│   │   ├── LoginView.vue       # 登录/注册
│   │   └── ChatView.vue        # 主布局：Sidebar + MessageList + InputBar
│   └── components/
│       ├── Sidebar.vue         # 侧边栏容器 + 新建会话按钮
│       ├── SessionList.vue     # 会话列表项（点击切换，当前高亮）
│       ├── MessageList.vue     # 消息气泡列表 + Markdown 渲染
│       └── InputBar.vue        # 输入框 + 发送/停止按钮
└── dist/                       # npm run build 产物（Phase 6 收尾再处理）
```

---

## SSE 流式协议处理边界

**规则：** chat store 统一解析流式协议，组件只消费纯文本。

后端 SSE 协议：
```
data: {普通文本}\n\n          # 逐 token 输出
data: [SESSION:{uuid}]\n\n    # 会话 ID 标记（仅首次消息）
data: [DONE]\n\n              # 结束标记
```

**chat store 的 `sendMessage` 负责：**
1. 检测 `[SESSION:{uuid}]` → 更新 `sessionId` 并持久化到 localStorage
2. 检测 `[DONE]` → 停止读取
3. 其余内容拼接为 reply，更新 `messages`

**组件不参与协议解析。** MessageList 只接收 messages 数组渲染，InputBar 只调用 store 的 `sendMessage`。

---

## 会话首次加载策略

登录后执行以下流程：

```
1. 调 GET /sessions，拿到当前用户的所有会话列表 → 存入 store.sessionList
2. 检查 localStorage 中的 session_id
   ├─ 存在且在 sessionList 中 → loadSession(该 uuid) → 显示历史
   └─ 不存在或不在列表中
       ├─ sessionList 非空 → loadSession(最新一条) → 显示历史
       └─ sessionList 为空 → 显示欢迎消息
```

**切换会话时：** 点击 SessionList 中的某条 → 更新 store.sessionId → 调 loadSession → messages 替换为新会话历史 → 滚动到底部。

**新建会话时：** 清空 store.sessionId → messages 重置为欢迎 → 不调后端接口（下次发消息时后端自动创建）。

---

## 步骤详情

### Step 1：Vite 初始化 + 基础设施

做什么：
- 在 `frontend/` 下执行 `npm create vite@latest . -- --template vue`
- 安装依赖：`vue-router@4`、`pinia`、`axios`
- 配置 `vite.config.js`：开发模式 proxy 到 `http://127.0.0.1:8000`
- 创建 `src/router/index.js`：定义 `/login`、`/chat` 两个路由
- 创建 `src/main.js`：createApp + use(router) + use(pinia)
- App.vue 只写 `<router-view />`
- 删除 Vite 默认模板内容（HelloWorld 等）

交付物：`npm run dev` 能跑起来，访问 `/` 显示空白页面，无报错。

---

### Step 2：auth store + 登录注册页

做什么：
- 创建 `src/stores/auth.js`：
  - state：`token`（从 localStorage 读）、`user`
  - actions：`login(username, password)`、`register(username, email, password)`、`logout()`
  - login 成功后存 token 到 localStorage
  - logout 时清 token + 清 localStorage
- 创建 `src/api/client.js`：
  - axios 实例，`baseURL` 从 `import.meta.env.VITE_API_URL` 读（默认 `http://127.0.0.1:8000`）
  - 请求拦截器：自动加 `Authorization: Bearer {token}`
  - 响应拦截器：401 时调 auth store 的 logout()
- 创建 `src/api/auth.js`：
  - `register(data)` → POST /auth/register
  - `login(data)` → POST /auth/login
  - `getMe()` → GET /auth/me
- 创建 `src/router/index.js` 中的路由守卫：
  - `beforeEach`：未登录访问 `/chat` → 重定向 `/login`；已登录访问 `/login` → 重定向 `/chat`
- 创建 `src/views/LoginView.vue`：登录/注册表单，调用 auth store

交付物：能注册、能登录、token 持久化到 localStorage、刷新不丢登录态。

---

### Step 3：chat store + SSE 协议处理

做什么：
- 创建 `src/api/chat.js`：
  - `chatStream(message, sessionId, token)` → POST /chat/stream，返回 ReadableStream
  - 调用方（chat store）负责解析 SSE
- 创建 `src/api/sessions.js`：
  - `listSessions(page, pageSize)` → GET /sessions
  - `getMessages(sessionUuid, page, pageSize)` → GET /sessions/{uuid}/messages
  - `deleteSession(sessionUuid)` → DELETE /sessions/{uuid}
  - `renameSession(sessionUuid, title)` → PATCH /sessions/{uuid}
- 创建 `src/stores/chat.js`：
  - state：`messages: []`、`sessionId: string|null`、`sessionList: []`、`loading: false`
  - actions：
    - `sendMessage(text)` — 调 chatStream，解析 SSE 协议：
      - 检测 `[SESSION:{uuid}]` → 更新 sessionId + localStorage
      - 检测 `[DONE]` → 停止
      - 其余内容拼接追加到 messages 末尾的 AI 回复
    - `loadSession(uuid)` — 调 getMessages，替换 messages
    - `loadSessionList()` — 调 listSessions，存入 sessionList
    - `newSession()` — 清 sessionId，重置 messages 为欢迎
    - `deleteSession(uuid)` — 调 deleteSession 接口，从 sessionList 移除
    - `renameSession(uuid, title)` — 调 renameSession 接口，更新 sessionList 中对应项

交付物：store 逻辑完整，可通过手动调用验证（此时还没有 UI）。

---

### Step 4：ChatView + MessageList + InputBar

做什么：
- 创建 `src/views/ChatView.vue`：
  - 布局：左侧预留 Sidebar 区域（Step 5 实现）+ 右侧消息区 + 底部输入框
  - onMounted：调 auth store 的 getMe() 获取用户信息；调 chat store 的 loadSessionList() + 首屏加载策略（见下方）
- 创建 `src/components/MessageList.vue`：
  - props：`messages` 数组
  - 渲染消息气泡（user 右侧蓝色，AI 左侧灰色）
  - Markdown 渲染（引入 marked.js）
  - thinking 状态（加载中三个点动画）
  - 自动滚动到底部
- 创建 `src/components/InputBar.vue`：
  - props：`loading`
  - emits：`send(text)`、`stop()`
  - textarea + 发送/停止按钮，Enter 发送

**首屏加载逻辑（写在 ChatView 的 onMounted 中）：**

```javascript
// 1. 加载会话列表
await chatStore.loadSessionList()

// 2. 判断恢复策略
const localSessionId = localStorage.getItem('session_id')
if (localSessionId && chatStore.sessionList.some(s => s.session_uuid === localSessionId)) {
  await chatStore.loadSession(localSessionId)
} else if (chatStore.sessionList.length > 0) {
  await chatStore.loadSession(chatStore.sessionList[0].session_uuid)
}
// else: sessionList 为空，显示欢迎消息（store 初始化时默认值）
```

交付物：能发消息、流式回复、刷新后历史恢复。

---

### Step 5：Sidebar + SessionList

做什么：
- 创建 `src/components/Sidebar.vue`：
  - 顶部"新建会话"按钮
  - 渲染 SessionList 组件
  - 宽度固定（如 260px），可后续加折叠
- 创建 `src/components/SessionList.vue`：
  - props：`sessions`（数组）、`currentSessionId`
  - emits：`select(uuid)`
  - 列表项：显示 title + 更新时间
  - 当前会话高亮

**切换会话逻辑（写在 ChatView 中）：**

```javascript
const onSelectSession = async (uuid) => {
  await chatStore.loadSession(uuid)
  // loadSession 内部会更新 sessionId、替换 messages、持久化 localStorage
}
```

**P2（可选增强）：** 删除会话和重命名会话不在 P1 主线内。SessionList 先只做"列表 + 点击切换"，后续再加 hover 删除按钮和双击重命名。`deleteSession` 和 `renameSession` 的 store actions 和 API 函数提前写好（Step 3 已包含），只是 UI 按钮暂不接入。

交付物（P1）：能看到所有会话、点击切换消息区正确替换、新建会话。

---

### Step 6：端到端测试

最小验收清单（P1 — 5 条）：

| # | 场景 | 操作 | 预期 |
|---|------|------|------|
| 1 | 登录后进入 /chat | 打开页面，登录 | 自动跳转 /chat，显示欢迎消息或历史 |
| 2 | 发送消息流式返回 | 输入消息发送 | AI 回复逐 token 显示 |
| 3 | 新建会话旧会话不丢 | 点"新建会话" → 发消息 → 切回旧会话 | 旧会话消息仍在 |
| 4 | 刷新页面恢复历史 | 发几条消息 → F5 | 当前会话历史完整恢复 |
| 5 | 切换会话消息替换 | 点击不同会话 | 消息区清空并加载目标会话历史 |

P2 验收（可选增强，不在主线内）：

| # | 场景 | 操作 | 预期 |
|---|------|------|------|
| 6 | 删除会话 | 点击删除 | 会话从列表消失，消息不再可访问 |
| 7 | 重命名会话 | 双击标题改名 | 列表标题更新 |
| 8 | 用户隔离 | 用户 A 的 session 传给用户 B | 返回 404 |

---

### Step 7：收尾 — 静态资源构建接入

#### 背景

当前开发需要同时跑 `uvicorn` + `npm run dev` 两个进程。Step 7 的目标是：`npm run build` 一次后，只启动 uvicorn 就能访问完整应用（API + 前端）。

**开发模式不变：** `npm run dev`（Vite dev server，自动 proxy）+ `uvicorn` 并行。

**生产模式：** `npm run build` → FastAPI 托管 `frontend/dist/`。

---

#### Step 7.1：确认 Vite build 产物结构

做什么：
- 执行 `cd frontend && npm run build`
- 确认 `frontend/dist/` 生成以下结构：
  ```
  frontend/dist/
  ├── index.html              # SPA 入口
  └── assets/
      ├── index-xxx.js        # 打包后的 JS
      └── index-xxx.css       # 打包后的 CSS
  ```
- `index.html` 中引用的 JS/CSS 路径应为 `/assets/index-xxx.js`（绝对路径）

交付物：build 成功，dist 目录结构正确。

---

#### Step 7.2：修改 `src/api/app.py` — 托管 dist

做什么：
- 删除旧的 `app.mount("/static", StaticFiles(directory="frontend"))`
- 删除旧的 `@app.get("/")` 返回 `frontend/index.html`
- 新增：`app.mount("/assets", StaticFiles(directory="frontend/dist/assets"))` — 托管打包后的静态资源
- 新增：catch-all `@app.get("/{full_path:path}")` 返回 `frontend/dist/index.html` — SPA fallback

**路由优先级（FastAPI 按注册顺序）：**
```
/api/*       → API 路由（最先匹配，不动）
/assets/*    → 静态资源（JS/CSS/图片）
/{任意路径}  → SPA fallback（返回 index.html，Vue Router 接管）
```

**Catch-all 实现：**
```python
import os

DIST_DIR = "frontend/dist"
DIST_INDEX = os.path.join(DIST_DIR, "index.html")

# 静态资源
if os.path.isdir(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(DIST_INDEX)
```

**为什么用 `os.path.isdir` 判断：**
- 开发模式下 `dist/` 不存在，不挂载静态资源、不注册 fallback
- Vite dev server 负责前端，uvicorn 只管 API
- 生产模式下 `dist/` 存在，自动启用静态托管
- 零配置切换，不需要环境变量

**注意事项：**
- `/api/*` 路由在 fallback 之前注册，FastAPI 精确匹配优先于通配符
- `/assets/*` 在 fallback 之前注册（mount 先于 catch-all route）
- catch-all 用 `{full_path:path}` 捕获所有剩余路径，返回 index.html

交付物：build 后只启动 uvicorn，访问 `http://127.0.0.1:8000` 显示登录页。

---

#### Step 7.3：SPA fallback 验证

做什么：
- 在浏览器地址栏直接访问 `/chat` → 应返回 index.html（Vue Router 接管后跳转到 /login 或显示聊天页）
- 在浏览器地址栏直接访问 `/login` → 应正常显示登录页
- 在浏览器地址栏直接访问 `/api/docs` → 应正常显示 FastAPI Swagger 文档
- 在浏览器地址栏直接访问 `/assets/xxx.js` → 应返回 JS 文件（200）

**这是关键测试：** 没有 SPA fallback 时，直接访问 `/chat` 会 404（因为后端没有这个路由）。

交付物：所有路径直接访问均正常，无 404。

---

#### Step 7.4：API 功能回归测试

做什么：
- 登录 → 正常跳转 /chat
- 发送消息 → 流式回复正常
- 会话列表 → 正常加载
- 切换会话 → 消息区正确替换
- 刷新页面 → 历史恢复

**注意：** 这些测试在 `http://127.0.0.1:8000` 下进行（不是 5173）。

交付物：所有 P1 功能在生产模式下正常。

---

#### 验收标准（4 条）

| # | 标准 | 验证方式 |
|---|------|---------|
| 1 | `npm run build` 成功 | 无报错，`frontend/dist/` 生成 index.html + assets/ |
| 2 | 只启动 uvicorn 能访问首页 | 访问 `http://127.0.0.1:8000` 显示登录页 |
| 3 | SPA 路由不 404 | 地址栏直接访问 `/chat`、`/login` 正常 |
| 4 | API 调用正常 | 登录 + 会话列表 + 流式聊天各测一条 |

---

#### 开发 / 生产双模式总结

| 模式 | 启动方式 | 前端来源 | API 来源 |
|------|---------|---------|---------|
| 开发 | `npm run dev` + `uvicorn` | Vite dev server :5173（proxy 到 8000） | uvicorn :8000 |
| 生产 | `npm run build` + `uvicorn` | uvicorn :8000 托管 dist/ | uvicorn :8000 |

---

## 与 UPGRADE_PLAN 的差异

| 原计划 | 本阶段实际 | 原因 |
|--------|-----------|------|
| 13 个文件/组件 | 11 个 | CitationCard、AgentBadge、DocumentUpload/DocumentList 推迟到 Phase 3/4 |
| 暗色模式 | 不做 | 视觉细节，非骨架必须 |
| Pinia 3 个 store | 2 个（auth、chat） | documents store 等文档管理 UI 再建 |
| axios + 拦截器 | axios + 拦截器 + 独立接口函数层 | 多拆了 api/auth.js、api/chat.js、api/sessions.js |
| 静态资源构建在主线内 | 移到 Step 7 收尾 | 避免干扰前端骨架主线 |
| 路由守卫放 App.vue | 放 router/index.js beforeEach | App.vue 只做 router-view 壳子 |
| chat store 管 UI 状态 | chat store 只管业务状态 | input、loading、滚动归组件管 |

---

## 验证结果

### P1 核心功能（已通过）

| # | 优先级 | 测试场景 | 操作 | 预期结果 | 实际结果 |
|---|--------|---------|------|---------|---------|
| 1 | P1 | 登录后进入 /chat | 登录 | 跳转 /chat，显示欢迎或历史 | 通过 |
| 2 | P1 | 流式聊天 | 发消息 | AI 逐 token 回复，Network 确认 /api/chat/stream 返回 200 | 通过 |
| 3 | P1 | 新建会话不丢旧数据 | 新建 → 发消息 → 切回旧会话 | 旧会话消息仍在 | 通过 |
| 4 | P1 | 刷新恢复历史 | 发消息 → F5 | 历史完整恢复 | 通过 |
| 5 | P1 | 切换会话 | 点击不同会话项 | 消息区正确替换 | 通过 |
| 6 | P1 | 退出登录 | 点击退出 | 立刻跳转 /login，浏览器后退不回 /chat | 通过（见下方 Bug 修复记录）|

### P2 增强功能（不在主线内，未测）

| # | 优先级 | 测试场景 | 操作 | 预期结果 | 实际结果 |
|---|--------|---------|------|---------|---------|
| 7 | P2 | 删除会话 | 点击删除 | 会话从列表消失 | 未测（UI 按钮未接入）|
| 8 | P2 | 重命名会话 | 双击标题改名 | 列表标题更新 | 未测（UI 按钮未接入）|

### Bug 修复记录

**退出登录无响应：**
- 现象：点击退出登录不跳转，需刷新或点击其他按钮才跳转
- 根因：`onLogout` 中 `chat.$reset()` 在 Composition API setup store 下抛异常，阻断了后续 `router.replace` 执行
- 修复：
  1. 去掉 `chat.$reset?.()`，改用 `chat.newSession()`
  2. `onLogout` 包 try/finally，保证清理报错也强制跳转
  3. `router.push` 改为 `router.replace`（不污染 history）
  4. 401 拦截器加 `localStorage.getItem('access_token')` 判断，避免手动退出后重复跳转

### Step 7 验收（待测）

| # | 标准 | 验证方式 | 结果 |
|---|------|---------|------|
| 1 | `npm run build` 成功 | 无报错，dist/ 生成正确 | 待测 |
| 2 | 只启动 uvicorn 能访问首页 | 访问 :8000 显示登录页 | 待测 |
| 3 | SPA 路由不 404 | 地址栏直接访问 /chat、/login | 待测 |
| 4 | API 功能正常 | 登录 + 会话列表 + 流式聊天 | 待测 |

---

## 已知限制（本阶段不解决）

| 限制 | 后续处理 |
|------|---------|
| 无引用展示 | Phase 3 加 CitationCard.vue |
| 无 Agent 标识 | Phase 4 加 AgentBadge.vue |
| 无文档管理 UI | 需要时加 DocumentUpload/DocumentList |
| 无暗色模式 | 可在任意阶段加，非阻塞 |
| 侧栏不可折叠 | 小优化，随时可加 |
