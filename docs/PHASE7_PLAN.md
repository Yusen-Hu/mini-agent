# Phase 7：Docker 部署 — 详细实施计划

## 背景

Phase 1~6 全部完成：认证、文档管理、RAG 混合检索、Multi-Agent、会话持久化、前端升级。当前开发模式是手动 `uvicorn` + Vite dev server，无法一键部署。Phase 7 实现容器化部署，前后端分离，生产级加固。

## 现状分析

| 组件 | 当前状态 | 问题 |
|------|----------|------|
| 后端 | `uvicorn src.api.app:app` 手动启动 | 无 Dockerfile |
| 前端 | Vite dev server (开发) / FastAPI 托管 dist/ (生产) | 耦合在后端进程里 |
| 基础设施 | docker-compose 已有 postgres/etcd/minio/milvus | 缺后端和前端服务 |
| 健康检查 | 无 `/health` 端点 | 容器编排无法判断服务是否就绪 |
| 限流 | 无 | 任何人可无限调用 API |
| SSE | 后端直接输出 | 经 Nginx 时会被缓冲导致流式卡死 |
| 依赖 | requirements.txt 缺 tiktoken/jieba | Docker 构建会缺包 |
| 镜像安全 | 无 | 无 .dockerignore、以 root 运行 |

## 架构设计

```
                    ┌─────────────────┐
                    │   Nginx :80     │
                    │  (前端静态资源)   │
                    └────────┬────────┘
                             │ /api/*  → proxy_pass backend:8000
                             │ /       → index.html (SPA)
                    ┌────────▼────────┐
                    │  Backend :8000  │
                    │  (FastAPI)      │
                    └───┬───┬───┬─────┘
                        │   │   │
              ┌─────────┘   │   └─────────┐
              ▼             ▼             ▼
          PostgreSQL     Milvus       Embedding
           :5432        :19530       (本地推理)
```

---

## 分步计划

### Step 1：补依赖 + .dockerignore

**做什么：**

1. `requirements.txt` 末尾添加：
```
tiktoken>=0.7
jieba>=0.42
slowapi>=0.1.9
```
- tiktoken：`chat.py` 的 `_load_messages` token 截断
- jieba：`bm25_index.py` 中文分词
- slowapi：请求限流

2. 新建 `.dockerignore`：
```
.git
.env
__pycache__
*.pyc
*.pyo
backend_old/
node_modules/
frontend/node_modules/
frontend/dist/
scripts/eval_results/
scripts/eval_sets/
data/documents/
tests/
*.md
!requirements.txt
```
- 排除开发产物、敏感文件、大体积目录
- `data/documents/` 排除是因为用 named volume 挂载

---

### Step 2：后端 Dockerfile

**做什么：** 基于 `python:3.12-slim`，单阶段构建（HuggingFace 模型需要下载，不适合多阶段）。

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 预下载 Embedding 模型（避免首次请求超时）
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

# 非 root 用户
RUN useradd -m appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**关键点：**
- 预下载模型避免冷启动（模型约 471MB，首次下载会超时 healthcheck）
- 单 worker：BM25 索引是内存单例，多 worker 会重复构建
- 非 root 运行

---

### Step 3：`/health` 端点

**做什么：** 在 `src/api/app.py` 添加健康检查端点，检查数据库和 Milvus 连通性。

```python
from sqlalchemy import text

@app.get("/health")
def health():
    checks = {}
    # 数据库
    try:
        from config.database import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    # Milvus
    try:
        from skills.rag.collection import init_collection
        init_collection()
        checks["milvus"] = "ok"
    except Exception as e:
        checks["milvus"] = f"error: {e}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "degraded", "checks": checks},
    )
```

- 全部 ok → 200，任一异常 → 503
- 不需要认证
- 供 docker-compose healthcheck 和运维监控使用

---

### Step 4：请求限流

**做什么：** 用 `slowapi`（基于内存的令牌桶，无外部依赖）。

**限流 key 设计：按用户 ID，未登录降级到 IP。**

系统是登录制，所有 API 调用带 JWT token。基于用户 ID 限流比 IP 更精准：同一用户换网络不会绕过限流，同一 IP 下不同用户不会互相影响。

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

def get_user_key(request: Request) -> str:
    """已登录按 user_id 限流，未登录降级到 IP。"""
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        try:
            from src.api.auth import decode_token
            payload = decode_token(token[7:])
            return f"user:{payload.get('sub', 'unknown')}"
        except Exception:
            pass
    return get_remote_address(request)

limiter = Limiter(key_func=get_user_key, default_limits=["60/minute"])

async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后重试"},
    )
```

**各端点限流策略：**

| 端点 | 限制 | 原因 |
|------|------|------|
| `/api/chat/stream` | 10/分钟 | LLM 调用成本高，长时间连接 |
| `/api/chat` | 10/分钟 | 同上 |
| `/api/documents/upload` | 5/分钟 | 上传 + 入库耗资源 |
| `/api/auth/login` | 5/分钟（按 IP） | 防暴力破解，未登录降级到 IP 正好合适 |
| `/api/auth/register` | 3/分钟（按 IP） | 防批量注册 |
| 其他端点 | 60/分钟（默认） | 读操作为主 |

**`decode_token` 只解码不验证**（验证由 FastAPI Depends 完成），开销极小。

---

### Step 5：前端 Nginx 配置 + Dockerfile

**做什么：**

1. 新建 `frontend/nginx.conf`：

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # SPA 路由 fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 关键配置
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # 静态资源长缓存
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

**SSE 核心配置说明：**
- `proxy_buffering off` — Nginx 不等后端响应完毕就转发给客户端（流式对话关键）
- `proxy_read_timeout 300s` — 防止长对话被 Nginx 超时断开（默认 60s 太短）
- `proxy_http_version 1.1` + `Connection ""` — 启用 keep-alive

2. 新建 `frontend/Dockerfile`（多阶段构建）：

```dockerfile
# 构建阶段
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# 运行阶段
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

---

### Step 6：docker-compose.yml 整合

**做什么：** 在现有 compose 文件中添加 backend + frontend 服务和 documents 卷。

```yaml
services:
  # ── 原有服务（不变）──
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: mini_agent
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  etcd:
    image: quay.io/coreos/etcd:v3.5.18
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - etcd_data:/etcd
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

  minio:
    image: minio/minio:RELEASE.2023-03-13T19-46-17Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    volumes:
      - minio_data:/minio_data
    command: minio server /minio_data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  standalone:
    image: milvusdb/milvus:v2.4.0
    command: ["milvus", "run", "standalone"]
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes:
      - milvus_data:/var/lib/milvus
    ports:
      - "19530:19530"
      - "9091:9091"
    depends_on:
      - etcd
      - minio

  # ── 新增服务 ──
  backend:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      # 容器内用服务名覆盖 localhost
      - DATABASE_URL=postgresql+psycopg2://postgres:postgres@postgres:5432/mini_agent
      - MILVUS_URI=http://standalone:19530
    volumes:
      - documents_data:/app/data/documents
    depends_on:
      postgres:
        condition: service_healthy
      standalone:
        condition: service_started
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 15s
      timeout: 10s
      retries: 8
      start_period: 120s

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      backend:
        condition: service_healthy

volumes:
  postgres_data:
  etcd_data:
  minio_data:
  milvus_data:
  documents_data:  # 新增：文档持久化
```

**关键设计：**
- `depends_on` + `condition: service_healthy` 确保启动顺序
- `start_period: 120s` 给模型加载和 BM25 索引构建留充足时间
- `retries: 8` 配合 120s 宽限
- `documents_data` 卷挂载：防止容器重建丢失已上传文档
- `.env` 注入 API Key 等敏感变量，`environment` 覆盖内部地址（容器间通信用服务名）

---

### Step 7：环境变量适配

**做什么：**

1. 新建 `.env.example`（不含真实密钥，提交到版本控制）：

```env
ARK_API_KEY=sk-xxx
MODEL=moonshot-v1-8k
BASE_URL=https://api.moonshot.cn/v1
JWT_SECRET_KEY=change-me-in-production

# 以下在 docker-compose 中自动覆盖，本地开发改 localhost
# DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent
# MILVUS_URI=http://127.0.0.1:19530
```

2. 不需要改 `config/settings.py`：pydantic-settings 已从环境变量读取，docker-compose `environment` 直接覆盖。

---

### Step 8：验证测试

**测试 1：一键启动**
```bash
docker compose up -d --build
# 等待 healthcheck 通过（约 120s）
docker compose ps   # 所有服务 healthy
# 访问 http://localhost → 前端加载
# 访问 http://localhost/health → {"status": "healthy", "checks": {...}}
```

**测试 2：SSE 流式对话**
1. 打开前端，发消息
2. 确认 token 逐字出现（非等全部生成完才显示）
3. `curl -N -X POST http://localhost/api/chat/stream -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"message":"你好"}'`

**测试 3：限流**
1. 登录拿到 token
2. 快速连发 15 次 `/api/chat/stream`
3. 第 11 次应返回 429 + "请求过于频繁"
4. 未登录状态调 `/api/chat/stream`（不带 token）→ 也应被限流（按 IP）

**测试 4：容器重启数据不丢**
1. 上传文档，创建会话
2. `docker compose restart backend`
3. 文档和会话仍在

**测试 5：健康检查编排**
1. `docker compose stop postgres`
2. backend healthcheck 应变 unhealthy
3. `docker compose start postgres`
4. backend 自动恢复

---

## 文件变更总览

| 文件 | 操作 | 说明 |
|------|------|------|
| `.dockerignore` | 新建 | 排除 node_modules、.env、__pycache__ 等 |
| `Dockerfile` | 新建 | 后端镜像（python:3.12-slim + 预下载模型） |
| `frontend/Dockerfile` | 新建 | 前端镜像（多阶段：node 构建 + nginx 运行） |
| `frontend/nginx.conf` | 新建 | SPA 路由 + API 反代 + SSE 配置 |
| `docker-compose.yml` | 修改 | 加 backend + frontend 服务 + documents 卷 |
| `requirements.txt` | 修改 | 加 tiktoken、jieba、slowapi |
| `src/api/app.py` | 修改 | 加 `/health` 端点 + limiter 注册 |
| `src/api/middleware.py` | 修改 | 加限流 handler + user key 函数 + limiter 实例 |
| `src/api/routers/chat_router.py` | 修改 | 关键端点加 `@limiter.limit` |
| `src/api/routers/document_router.py` | 修改 | 上传端点加限流 |
| `src/api/routers/auth_router.py` | 修改 | 登录/注册端点加限流（按 IP） |
| `.env.example` | 新建 | 环境变量模板（提交版本控制） |

## 与前面阶段的衔接

| 前置阶段 | 衔接点 |
|----------|--------|
| Phase 1 (DB) | PostgreSQL 服务已在 compose 中，backend 通过 `DATABASE_URL` 连接 |
| Phase 2 (文档) | `data/documents/` 挂载为 named volume，容器重建不丢 |
| Phase 3A (RAG) | Milvus 服务已在 compose 中，`MILVUS_URI` 改为 `http://standalone:19530` |
| Phase 4 (Agent) | BM25 索引启动时自动构建，healthcheck 包含 Milvus 检查 |
| Phase 5 (会话) | PostgreSQL 持久化，容器重启会话不丢 |
| Phase 6 (前端) | Vite build 产物由 Nginx 托管，`/api` 反代到后端 |
