# Phase 1 详细实施计划：基础设施与安全

## 当前状态

已有文件（需改造）：
- `config/settings.py` — 占位
- `config/database.py` — 占位
- `src/api/middleware.py` — 占位
- `src/api/app.py` — 可用，但硬编码 CORS、无认证、无配置管理
- `src/services/llm.py` — 可用，但用 `os.getenv()` + `load_dotenv()` 分散管理配置
- `src/types/chat.py` — 可用，ChatRequest、ChatResponse
- `docker-compose.yml` — 可用，只有 Milvus 三件套
- `.env` — 可用，3 个环境变量

---

## 逐步实施计划

---

### Step 1: 创建 requirements.txt

**做什么：** 在项目根目录创建 `requirements.txt`，列出所有依赖。

**怎么做：**
```
fastapi>=0.115
uvicorn[standard]>=0.34
sqlalchemy>=2.0
alembic>=1.14
psycopg2-binary>=2.9
pydantic-settings>=2.7
email-validator>=2.2
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7
python-multipart>=0.0.18
pymilvus>=2.4
langchain>=0.3
langchain-openai>=0.3
langchain-community>=0.3
langchain-huggingface>=0.1
langchain-text-splitters>=0.3
langgraph>=0.3
pypdf>=5.0
docx2txt>=0.8
python-dotenv>=1.0
```

> `email-validator` 为 `EmailStr` 提供运行时校验支持（Step 6 需要）。

**怎么测试：**
```bash
E:/1/python/envs/supermew/python.exe -m pip install -r requirements.txt
# 应全部安装成功，无报错
E:/1/python/envs/supermew/python.exe -c "import fastapi, sqlalchemy, pydantic_settings, jose, passlib, email_validator; print('All deps OK')"
```

---

### Step 2: 实现 config/settings.py

**做什么：** 用 pydantic-settings 替换分散在各文件中的 `os.getenv()` 调用，集中管理所有配置。使用 Pydantic v2 写法。

**怎么做：**

```python
# config/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Debug
    DEBUG: bool = False

    # LLM
    ARK_API_KEY: str = ""
    MODEL: str = "moonshot-v1-8k"
    BASE_URL: str = "https://api.moonshot.cn/v1"

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent"

    # Milvus
    MILVUS_URI: str = "http://127.0.0.1:19530"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # Embedding
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384

    # RAG
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    TOP_K: int = 3

    # Upload
    UPLOAD_DIR: str = "data/documents"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".docx", ".doc", ".txt", ".md", ".html"]

    # CORS
    CORS_ORIGINS: List[str] = ["*"]


settings = Settings()
```

**怎么测试：**
```bash
E:/1/python/envs/supermew/python.exe -c "
from config.settings import settings
print(f'MODEL: {settings.MODEL}')
print(f'DB: {settings.DATABASE_URL}')
print(f'DEBUG: {settings.DEBUG}')
print(f'ALLOWED_EXT: {settings.ALLOWED_EXTENSIONS}')
print('settings.py OK')
"
# 应输出：MODEL: moonshot-v1-8k, DB: postgresql+psycopg2://..., DEBUG: False, ALLOWED_EXT: ['.pdf', ...]
```

---

### Step 3: 改造 src/services/llm.py 使用 settings

**做什么：** 把 `llm.py` 中的 `os.getenv()` + `load_dotenv()` 替换为从 `config.settings` 导入。

**改前（当前）：**
```python
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

llm = ChatOpenAI(
    model=os.getenv("MODEL"),
    api_key=os.getenv("ARK_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    temperature=0,
)
```

**改后：**
```python
from langchain_openai import ChatOpenAI
from config.settings import settings

llm = ChatOpenAI(
    model=settings.MODEL,
    api_key=settings.ARK_API_KEY,
    base_url=settings.BASE_URL,
    temperature=0,
)
```

**怎么测试：**
```bash
E:/1/python/envs/supermew/python.exe -c "
from src.services.llm import llm
print(f'llm.model_name: {llm.model_name}')
print('llm.py OK')
"
# 应输出：llm.model_name: moonshot-v1-8k
```

---

### Step 4: 实现 config/database.py

**做什么：** 创建 SQLAlchemy engine 和 FastAPI 的 `get_db()` 依赖。

**怎么做：**

```python
# config/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.settings import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**怎么测试：**
```bash
# 此步需要 PostgreSQL 运行，先测试 import 不报错
E:/1/python/envs/supermew/python.exe -c "
from config.database import engine, SessionLocal, get_db
print(f'engine.url: {engine.url}')
print('database.py OK')
"
# 启动 PG 后再测试连接：
docker compose up -d postgres
E:/1/python/envs/supermew/python.exe -c "
from config.database import engine
with engine.connect() as conn:
    print('PostgreSQL connected OK')
"
```

---

### Step 5: 添加 PostgreSQL 到 docker-compose.yml

**做什么：** 在 `docker-compose.yml` 中添加 PostgreSQL 16 服务。

**怎么做：** 在 `services:` 下添加：

```yaml
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

volumes:  # 在现有 volumes: 下添加
  postgres_data:
```

**怎么测试：**
```bash
docker compose up -d postgres
docker compose ps postgres
# STATUS 应显示 "Up"
docker compose exec postgres pg_isready -U postgres
# 应输出: postgres:5432 - accepting connections
```

> 注意：本项目后端通过本机 uvicorn 运行，不走 Docker 容器，所以无需 `depends_on`。开发者需先手动启动 PG：`docker compose up -d postgres`。

---

### Step 6: 创建 src/types/user.py

**做什么：** 创建 User 的 SQLAlchemy ORM 模型和 Pydantic Schema。

**怎么做：**

```python
# src/types/user.py
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from pydantic import BaseModel, EmailStr
from datetime import datetime

# SQLAlchemy Base（后续 models 都继承它）
Base = declarative_base()


# ── ORM 模型 ──
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(256), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(16), default="user")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Pydantic 请求/响应模型 ──
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

> `EmailStr` 需要 `email-validator` 依赖（已在 Step 1 中添加）。
> 后续多表时可将 `Base` 抽到单独模块（如 `src/types/db_base.py`），避免模型文件互相引用混乱。

**怎么测试：**
```bash
E:/1/python/envs/supermew/python.exe -c "
from src.types.user import User, UserCreate, UserLogin, UserResponse, Token
print(f'User table: {User.__tablename__}')
print(f'UserCreate fields: {list(UserCreate.model_fields.keys())}')
print('user.py OK')
"
```

---

### Step 7: 实现 src/services/auth.py

**做什么：** 密码哈希、JWT 签发/验证、FastAPI 认证依赖。`sub` claim 统一使用字符串类型（RFC 7519 规范）。

**怎么做：**

```python
# src/services/auth.py
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from config.settings import settings
from config.database import get_db
from src.types.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        sub: str = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 token")
        user_id = int(sub)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user
```

**怎么测试：**
```bash
E:/1/python/envs/supermew/python.exe -c "
from src.services.auth import hash_password, verify_password, create_access_token
h = hash_password('test123')
print(f'verify: {verify_password(\"test123\", h)}')  # True
print(f'verify bad: {verify_password(\"wrong\", h)}')  # False
token = create_access_token({'sub': '1'})
print(f'token: {token[:30]}...')
print('auth.py OK')
"
```

---

### Step 8: 创建 src/api/routers/auth_router.py

**做什么：** 注册、登录、获取当前用户信息 3 个端点。`create_access_token` 调用时 `sub` 统一传字符串。

**怎么做：**

```python
# src/api/routers/auth_router.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from config.database import get_db
from src.types.user import User, UserCreate, UserLogin, UserResponse, Token
from src.services.auth import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Token, status_code=201)
def register(body: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="邮箱已存在")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token)


@router.post("/login", response_model=Token)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
```

**怎么测试（需 PostgreSQL 运行 + 表已创建）：**
```bash
# 先创建表（Step 9），然后：
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test","email":"test@test.com","password":"123456"}'
# 应返回: {"access_token":"eyJ...","token_type":"bearer"}

curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"123456"}'
# 应返回: {"access_token":"eyJ...","token_type":"bearer"}

curl http://127.0.0.1:8000/auth/me \
  -H "Authorization: Bearer <上面的token>"
# 应返回: {"id":1,"username":"test","email":"test@test.com","role":"user","created_at":"..."}
```

---

### Step 9: 初始化数据库表

**做什么：** 在 app 启动时自动创建 users 表。

> Phase 1 使用 `create_all` 简化处理。生产环境应使用 Alembic 迁移管理 schema 变更；多 worker 部署时注意 `create_all` 的启动竞态问题（可改为在部署脚本中提前执行一次，而非每次启动都调用）。

**怎么做：** 在 `src/api/app.py` 中添加：

```python
from src.types.user import Base
from config.database import engine

Base.metadata.create_all(bind=engine)
```

**怎么测试：**
```bash
# 确保 PG 已启动
docker compose up -d postgres
# 启动服务
uvicorn src.api.app:app --reload
# 看日志无报错
# 然后测试 Step 8 的 curl 命令
```

---

### Step 10: 实现 src/api/middleware.py

**做什么：** 全局异常处理。`HTTPException` 和 `RequestValidationError` 不应被通用 Exception 处理器改成 500，只有"未预期的异常"才返回统一 500。错误详情由 `settings.DEBUG` 控制。

**怎么做：**

```python
# src/api/middleware.py
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError

from config.settings import settings

logger = logging.getLogger(__name__)


# HTTPException：保持原始状态码和 detail，直接返回
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# RequestValidationError：返回 422 + 字段错误详情
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数错误", "errors": exc.errors()},
    )


# 未预期的异常：统一返回 500
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误",
            "error": str(exc) if settings.DEBUG else "Internal Server Error",
        },
    )
```

在 `src/api/app.py` 中注册：
```python
from fastapi.exceptions import HTTPException, RequestValidationError
from src.api.middleware import (
    http_exception_handler,
    validation_exception_handler,
    global_exception_handler,
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
```

**怎么测试：**
```bash
# 1. 访问不存在的路由 → 应返回 404（HTTPException 保持原状态码）
curl http://127.0.0.1:8000/nonexistent
# 应返回: {"detail":"Not Found"}，状态码 404（不是 500）

# 2. 发送格式错误的请求 → 应返回 422
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":123}'
# 应返回: {"detail":"请求参数错误","errors":[...]}

# 3. 触发未预期异常 → 应返回 500（DEBUG 模式下含 error 详情）
# 后续可注册一个 /debug-error 测试端点
```

---

### Step 11: 改造 src/api/app.py

**做什么：** 综合以上改动，重写 `app.py`：使用 settings 管理 CORS、注册 auth_router、挂载异常处理器、创建数据库表。

**改后完整代码：**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.exceptions import HTTPException, RequestValidationError

from config.settings import settings
from config.database import engine
from src.types.user import Base
from src.api.routers.chat_router import router as chat_router
from src.api.routers.auth_router import router as auth_router
from src.api.middleware import (
    http_exception_handler,
    validation_exception_handler,
    global_exception_handler,
)

# 创建数据库表（Phase 1 简化方案，生产环境用 Alembic）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mini Agent", version="0.1.0")

# 异常处理器（按优先级注册，越具体的越先注册）
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# CORS（从 settings 读取）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def root():
    return FileResponse("frontend/index.html")


# 路由注册
app.include_router(auth_router)
app.include_router(chat_router)
```

**怎么测试：**
```bash
# 启动
uvicorn src.api.app:app --reload

# Swagger UI 测试
# 浏览器打开 http://127.0.0.1:8000/docs
# 应看到端点：
#   GET  /
#   POST /auth/register
#   POST /auth/login
#   GET  /auth/me
#   POST /chat
#   POST /chat/stream
#   POST /documents/upload
```

---

### Step 12: 给 /chat 和 /documents/upload 加认证保护

**做什么：** 把 `get_current_user` 依赖注入到需要认证的端点。

**怎么做：** 在 `src/api/routers/chat_router.py` 中：

```python
from fastapi import Depends
from src.services.auth import get_current_user
from src.types.user import User

@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest, current_user: User = Depends(get_current_user)):
    reply = chat(req.message, req.session_id)
    return ChatResponse(reply=reply, session_id=req.session_id)

@router.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest, current_user: User = Depends(get_current_user)):
    ...

@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    ...
```

**怎么测试：**
```bash
# 不带 token 调 /chat，应返回 401
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
# 应返回: {"detail":"Not authenticated"}，状态码 401（不是 500）

# 带 token 调 /chat，应正常返回
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message":"hello"}'
# 应正常返回 AI 回复
```

---

### Step 12.5: 前端适配 Bearer Token

**做什么：** `frontend/index.html` 需要支持登录后保存 token，所有 API 请求带上 `Authorization` header。Phase 1 不做完整 Vue 重构，只做最小改动让认证流程跑通。

**怎么做：** 在 `frontend/index.html` 的 Vue 应用中添加以下逻辑：

```javascript
// 1. 登录/注册后保存 token
async function login(username, password) {
    const res = await fetch('http://127.0.0.1:8000/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    localStorage.setItem('access_token', data.access_token);
}

// 2. 请求时携带 token
function getToken() {
    return localStorage.getItem('access_token');
}

// 3. /chat 和 /chat/stream 请求头加 Authorization
async function sendMessage(message) {
    const token = getToken();
    if (!token) { alert('请先登录'); return; }

    const res = await fetch('http://127.0.0.1:8000/chat/stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ message, session_id: sessionId })
    });
    // ... SSE 解析逻辑不变
}

// 4. 登录状态检测：token 过期时自动提示重新登录
// fetch 返回 401 时 → 清除 token → 跳转登录
```

> localStorage key 名：`access_token`。未登录时调用 API 应提示"请先登录"。SSE 流同样需要带 `Authorization` header（fetch 原生支持自定义 header）。

**怎么测试：**
```bash
# 浏览器测试流程：
# 1. 打开 http://127.0.0.1:8000
# 2. 未登录直接发消息 → 应弹出"请先登录"提示
# 3. 调用 /auth/register 或 /auth/login 获取 token
# 4. 带 token 发消息 → 应正常返回 AI 回复
# 5. 浏览器 F12 → Application → Local Storage → 应有 access_token
```

---

### Step 13: 修复路径穿越漏洞 + 上传大小限制

**做什么：** 在 `/documents/upload` 端点中：用 UUID 文件名替换 `file.filename`、校验扩展名白名单、限制上传大小。

**怎么做：**

```python
import uuid
from config.settings import settings

@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    # 校验扩展名
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # UUID 文件名，防止路径穿越
    safe_name = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)

    # 限制上传大小
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    total = 0
    with open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 64):  # 64KB 分块读取
            total += len(chunk)
            if total > max_bytes:
                f.close()
                os.remove(file_path)  # 删除不完整文件
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制",
                )
            f.write(chunk)

    chunk_count = ingest_document(file_path)
    return {"message": f"上传成功，共入库 {chunk_count} 个片段", "filename": file.filename}
```

> 后续可加强 MIME 类型 / 魔数校验（非 Phase 1 必做项）。

**怎么测试：**
```bash
# 正常上传
curl -X POST http://127.0.0.1:8000/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@data/documents/test.pdf"
# 应成功

# 上传不支持的格式
echo "test" > test.exe
curl -X POST http://127.0.0.1:8000/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@test.exe"
# 应返回 400: {"detail":"不支持的文件类型: .exe"}

# 上传超大文件（>50MB）
dd if=/dev/zero of=big.pdf bs=1M count=51 2>/dev/null
curl -X POST http://127.0.0.1:8000/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@big.pdf"
# 应返回 413: {"detail":"文件超过 50MB 限制"}
```

---

## 执行顺序总结

```
Step 1    requirements.txt                （无依赖，先做）
Step 2    config/settings.py              （无依赖，和 Step 1 并行）
Step 3    src/services/llm.py             （依赖 Step 2）
Step 4    config/database.py              （依赖 Step 2）
Step 5    docker-compose.yml 添加 PG       （依赖 Step 4）
Step 6    src/types/user.py               （依赖 Step 1 email-validator）
Step 7    src/services/auth.py            （依赖 Step 2, 4, 6）
Step 8    src/api/routers/auth_router.py  （依赖 Step 7）
Step 9    数据库表初始化                    （依赖 Step 4, 6）
Step 10   src/api/middleware.py            （依赖 Step 2）
Step 11   src/api/app.py 改造             （依赖 Step 8, 10）
Step 12   /chat 加认证保护                 （依赖 Step 7, 11）
Step 12.5 前端 Bearer Token               （依赖 Step 12）
Step 13   修复路径穿越 + 上传大小限制        （依赖 Step 2, 12）
```

---

## 完整端到端测试流程

```bash
# 1. 启动基础设施
docker compose up -d

# 2. 启动后端
uvicorn src.api.app:app --reload

# 3. 打开 Swagger UI
# http://127.0.0.1:8000/docs

# 4. 注册用户
# POST /auth/register {"username":"admin","email":"admin@test.com","password":"123456"}
# → 201 + access_token

# 5. 登录
# POST /auth/login {"username":"admin","password":"123456"}
# → 200 + access_token

# 6. 获取用户信息（带 token）
# GET /auth/me + Authorization: Bearer <token>
# → 200 + user info

# 7. 不带 token 调 /chat
# POST /chat {"message":"hello"}
# → 401 Not authenticated（不是 500）

# 8. 带 token 调 /chat
# POST /chat + Authorization: Bearer <token> {"message":"你好"}
# → 200 + AI 回复

# 9. 上传 PDF（带 token）
# POST /documents/upload + file
# → 200 + chunk count

# 10. 上传 .exe（应该被拒绝）
# → 400 不支持的文件类型

# 11. 访问不存在的路由
# GET /nonexistent
# → 404（HTTPException 保持原状态码，不会被改成 500）

# 12. 浏览器测试
# 打开 http://127.0.0.1:8000
# → 未登录发消息应提示"请先登录"
# → 登录后发消息应正常流式返回
# → F12 Local Storage 应有 access_token
```
