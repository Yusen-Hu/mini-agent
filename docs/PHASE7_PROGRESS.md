# Phase 7：Docker 部署 — 执行进度记录

## 一、已完成的代码改动

### Step 1：补依赖 + .dockerignore

**文件：`requirements.txt`**

新增 5 个依赖（tiktoken/jieba 在代码中已使用但未声明，slowapi 为本次新增，rank_bm25/sentence-transformers 同理）：

```
tiktoken>=0.7
jieba>=0.42
slowapi>=0.1.9
rank_bm25>=0.2
sentence-transformers>=3.0
```

**文件：`.dockerignore`**（新建）

排除 node_modules、.env、__pycache__、eval 结果、文档目录等，减小构建上下文。

---

### Step 2：/health 端点

**文件：`src/api/app.py`**

新增 `/health` 端点，检查数据库和 Milvus 连通性：
- 全部 ok → 200 + `{"status":"healthy"}`
- 任一异常 → 503 + `{"status":"degraded"}`
- 不需要认证

---

### Step 3：限流中间件

**文件：`src/api/middleware.py`**

用 `slowapi` 实现令牌桶限流。关键设计：

```python
def get_user_key(request: Request) -> str:
    """已登录按 user_id 限流，未登录降级到 IP。"""
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        try:
            from src.services.auth import decode_token
            payload = decode_token(token[7:])
            if payload:
                sub = payload.get("sub")
                if sub:
                    return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)
```

- 登录制系统按 user_id 限流比 IP 精准
- 未登录请求（login/register）降级到 IP，正好防暴力破解
- 默认 60/分钟

**文件：`src/services/auth.py`**

新增 `decode_token()` 函数：轻量 JWT 解码（验签名，不查 DB），供限流 key 函数使用。

**文件：`src/api/app.py`**

注册 limiter 和 RateLimitExceeded handler：

```python
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
```

---

### Step 4：各路由加限流装饰器

| 文件 | 端点 | 限制 |
|------|------|------|
| `chat_router.py` | `/chat` | 10/分钟 |
| `chat_router.py` | `/chat/stream` | 10/分钟 |
| `document_router.py` | `/documents/upload` | 5/分钟 |
| `auth_router.py` | `/auth/login` | 5/分钟 |
| `auth_router.py` | `/auth/register` | 3/分钟 |

每个限流端点函数签名加了 `request: Request` 作为第一个参数（slowapi 要求）。

---

### Step 5：前端 Nginx + Dockerfile

**文件：`frontend/nginx.conf`**（新建）

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # SPA fallback
    }

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_buffering off;                  # SSE 关键
        proxy_cache off;
        proxy_read_timeout 300s;              # 长对话防超时
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

**文件：`frontend/Dockerfile`**（新建，多阶段构建）

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

---

### Step 6：后端 Dockerfile

**文件：`Dockerfile`**（新建）

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV HF_HOME=/app/.cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
COPY . .
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**关键设计点：**
- `ENV HF_HOME=/app/.cache`：强制 HuggingFace 缓存到 `/app/.cache`，不使用默认的 `/root/.cache`，确保 appuser 能访问
- `chown -R appuser:appuser /app`：让 appuser 拥有 `/app` 下所有文件（含 `/app/.cache` 中的模型）
- 初始版本有 `apt-get install build-essential libpq-dev`，后因 Debian 源 502 被删（见下方错误记录）

---

### Step 7：docker-compose.yml 整合

新增两个服务：

```yaml
backend:
  build: .
  ports: ["8000:8000"]
  env_file: [.env]
  environment:
    - DATABASE_URL=postgresql+psycopg2://postgres:postgres@postgres:5432/mini_agent
    - MILVUS_URI=http://standalone:19530
  volumes: [documents_data:/app/data/documents]
  depends_on:
    postgres: {condition: service_healthy}
    standalone: {condition: service_started}
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
    interval: 15s
    timeout: 10s
    retries: 10
    start_period: 180s

frontend:
  build: ./frontend
  ports: ["80:80"]
  depends_on:
    backend: {condition: service_healthy}
```

新增 `documents_data` named volume。

---

### Step 8：.env.example

**文件：`.env.example`**（新建）

环境变量模板，不含真实密钥，可提交版本控制。

---

## 二、本地验证（uvicorn）

启动后端：
```powershell
cd D:\mini_agent; & E:\1\python\envs\supermew\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 验证 1：/health 端点 ✅

```bash
curl http://127.0.0.1:8000/health
# → {"status":"healthy","checks":{"database":"ok","milvus":"ok"}}
```

### 验证 2：限流 ✅

对 `/api/auth/login` 连发 8 次（密码错误，5/分钟限制）：

```
请求 1: HTTP 401
请求 2: HTTP 401
请求 3: HTTP 401
请求 4: HTTP 401
请求 5: HTTP 401
请求 6: HTTP 429  ← 限流生效
请求 7: HTTP 429
请求 8: HTTP 429
```

### 验证 3：正常功能不受影响 ✅

注册 → 登录 → 发消息，正常返回：
```json
{"reply":"你好！很高兴见到你 👋","session_id":"5a076bd5-...","agent":"general_chat"}
```

---

## 三、Docker 构建遇到的错误与解决

### 错误 1：slowapi 导入失败

**现象：**
```
ModuleNotFoundError: No module named 'slowapi'
```

**原因：** `slowapi` 未安装。

**解决：**
```powershell
& E:\1\python\envs\supermew\python.exe -m pip install slowapi
```

---

### 错误 2：RateLimitExceeded 导入路径错误

**现象：**
```
ImportError: cannot import name 'RateLimitExceeded' from 'slowapi'
```

**原因：** slowapi 0.1.9 中 `RateLimitExceeded` 在 `slowapi.errors` 子模块，不在顶层 `slowapi`。

**解决：** `app.py` 改导入路径：
```python
# 改前
from slowapi import RateLimitExceeded
# 改后
from slowapi.errors import RateLimitExceeded
```

---

### 错误 3：slowapi 装饰器缺少 request 参数

**现象：** 未在本地验证时发现，但启动后端时 `@limiter.limit()` 装饰的端点必须接受 `request: Request` 参数。

**原因：** slowapi 从 `request.state` 读取 limiter，要求函数签名里有 `request: Request`。

**解决：** 所有限流端点加 `request: Request` 作为第一个参数：
```python
@router.post("/chat")
@limiter.limit("10/minute")
def chat_endpoint(request: Request, req: ChatRequest, ...):
```

---

### 错误 4：Docker 镜像源不可用

**现象：**
```
ERROR: failed to do request: Head "https://docker.mirrors.ustc.edu.cn/v2/...": EOF
```

**原因：** daemon.json 中配置的三个镜像源（USTC、163、百度）全部不可用。

**解决：** 清空镜像源，改为直连 Docker Hub：
```json
{}
```

---

### 错误 5：Docker 无法直连 Docker Hub

**现象：**
```
ERROR: dial tcp 74.86.12.172:443: connectex: A connection attempt failed
```

**原因：** Docker Desktop 不使用系统代理，直连 Docker Hub 被墙。

**解决：** 在 Docker Desktop → Settings → Resources → Proxies 中配置代理 `http://127.0.0.1:7897`。

---

### 错误 6：GUI 代理配置对构建不生效

**现象：** Docker Desktop GUI 配了代理，`docker compose build` 仍然直连 auth.docker.io 超时。

**原因：** Docker Desktop GUI 的代理设置对 BuildKit 构建阶段不完全生效。

**解决：** 使用环境变量显式传递代理：
```powershell
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
docker compose build --no-cache
```

环境变量方案有效：前端（node + nginx）构建成功。

---

### 错误 7：Debian apt-get 源 502

**现象：**
```
E: Failed to fetch http://deb.debian.org/debian/dists/trixie-updates/InRelease  502 Bad Gateway
```

**原因：** 后端 Dockerfile 中 `RUN apt-get update && apt-get install build-essential libpq-dev` 通过代理访问 Debian 官方源，代理返回 502。

**解决：** 删除整个 `apt-get` 步骤。理由：
- `psycopg2-binary` 在 requirements.txt 中，提供预编译 wheel，不需要 `libpq-dev`
- `tiktoken` 有 Python 3.12 Linux 预编译 wheel，不需要 `build-essential`
- `jieba` 是纯 Python 包，不需要编译

改后 Dockerfile（此处为当时版本，不含 ENV HF_HOME，见下方错误 8 的后续修复）：
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
COPY . .
RUN useradd -m appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

> 注意：此版本模型缓存在默认路径 `/root/.cache`，appuser 无权访问，导致容器启动失败。后续在"错误 8"中修复为 `ENV HF_HOME=/app/.cache` + `chown -R appuser:appuser /app`。最终 Dockerfile 以 Step 6 中的版本为准。

---

### 错误 8：HuggingFace 模型缓存权限问题

**现象：**
```
PermissionError: [Errno 13] Permission denied: '/root/.cache/huggingface/...'
```

后端容器启动时，sentence_transformers 尝试加载模型，但 appuser 无法访问 `/root/.cache`（root 用户构建时下载的缓存）。

**原因：** 构建阶段 `RUN python -c "SentenceTransformer(...)"` 以 root 身份运行，模型下载到 `/root/.cache`。之后 `RUN useradd -m appuser && chown -R appuser:appuser /app` 只 chown 了 `/app`，不包括 `/root/.cache`。运行时 appuser 读不到模型。

**解决：** 在 `RUN SentenceTransformer(...)` 之前加 `ENV HF_HOME=/app/.cache`，强制缓存到 `/app` 下，再由 `chown -R appuser:appuser /app` 统一授权。

修复后 Dockerfile 关键行：
```dockerfile
ENV HF_HOME=/app/.cache                                              # ← 强制缓存路径
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
COPY . .
RUN useradd -m appuser && chown -R appuser:appuser /app              # ← 覆盖 /app/.cache
```

因果链：`ENV HF_HOME=/app/.cache` → 模型下载到 `/app/.cache` → `chown -R appuser /app` → appuser 有权限访问模型。

---

## 四、当前状态

| 项目 | 状态 |
|------|------|
| 代码改动（Step 1-8） | ✅ 全部完成 |
| 本地验证（uvicorn） | ✅ 三项通过 |
| 前端 Docker 构建 | ✅ 成功（镜像 26.2MB） |
| 后端 Docker 构建 | ✅ 成功（镜像 3.95GB） |
| docker compose up | 🔲 待执行（镜像已就绪，服务未启动） |
| C 盘迁移 | ✅ docker_data.vhdx（26GB）迁至 D 盘，Junction 方案 |

### C 盘迁移方案说明

**当前方案：** Junction（目录级软链接）

```powershell
# 原文件已移至 D:\Docker\wsl\disk\docker_data.vhdx
# C 盘原目录删除后创建 Junction
New-Item -ItemType Junction -Path "C:\Users\胡宇森\AppData\Local\Docker\wsl\disk" -Target "D:\Docker\wsl\disk"
```

**⚠️ 风险：** Junction 是软链接，不是真正的迁移。Docker Desktop 升级、重置或修复时可能会：
- 删除 Junction 并创建新目录（数据回退到 C 盘）
- 覆盖 Junction 指向的内容（数据丢失）

**正式方案：** Docker Desktop → Settings → Resources → Advanced → 修改 Disk image location 为 `D:\Docker\wsl\disk\docker_data.vhdx`。上次 GUI 修改未生效，待确认是否需要完全停止 Docker 后再修改。当前 Junction 方案为临时过渡。

## 五、完整启动命令

### Docker 部署（从零）

```powershell
# 1. 设置代理（构建时需要，运行时不需要）
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"

# 2. 如镜像不存在则构建（已构建可跳过）
cd D:\mini_agent
docker compose build --no-cache

# 3. 启动全部服务
docker compose up -d

# 4. 等待健康检查通过（约 2-3 分钟）
docker compose ps

# 5. 访问
# 浏览器打开 http://localhost
```

### 本地开发（uvicorn）

```powershell
cd D:\mini_agent
& E:\1\python\envs\supermew\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
# 访问 http://127.0.0.1:8000
```
