import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError

from config.settings import settings
from config.logging import setup_logging
from config.database import engine
from src.types.user import Base
from src.types.document import Document  # noqa: F401 — 确保 create_all 建 documents 表
from src.types.session import ChatSession, ChatMessage  # noqa: F401 — 确保 create_all 建 chat_sessions/chat_messages 表
from src.api.routers.chat_router import router as chat_router
from src.api.routers.auth_router import router as auth_router
from src.api.routers.document_router import router as document_router
from src.api.routers.session_router import router as session_router
from src.api.routers.admin_router import router as admin_router
from slowapi.errors import RateLimitExceeded
from src.api.middleware import (
    http_exception_handler,
    validation_exception_handler,
    global_exception_handler,
    rate_limit_handler,
    limiter,
)

# 初始化结构化日志
setup_logging(
    level=settings.LOG_LEVEL,
    log_file=settings.LOG_FILE,
    max_bytes=settings.LOG_MAX_BYTES,
    backup_count=settings.LOG_BACKUP_COUNT,
)

# 创建数据库表（Phase 1 简化方案，生产环境用 Alembic）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mini Agent", version="0.1.0")

# 限流
app.state.limiter = limiter

# 异常处理器
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_exception_handler(Exception, global_exception_handler)

# CORS（从 settings 读取）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册（统一 /api 前缀，避免与前端 SPA 路由冲突）
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(document_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


@app.get("/health")
def health():
    """容器编排健康检查：检查数据库和 Milvus 连通性。"""
    from sqlalchemy import text

    checks = {}
    try:
        from config.database import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

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


@app.on_event("startup")
def _build_bm25_index():
    """服务启动时构建 BM25 索引（失败降级，不阻塞启动）。"""
    from config.logging import get_logger
    logger = get_logger("app")
    try:
        from skills.rag.bm25_index import bm25_index
        from skills.rag.collection import milvus_client, COLLECTION_NAME, init_collection
        init_collection()
        bm25_index.build_index(milvus_client, COLLECTION_NAME)
        status = bm25_index.status()
        logger.info("BM25 索引构建完成: %s", status)
    except Exception as e:
        logger.warning("BM25 索引构建失败，降级为纯 Dense: %s", e)

# Admin 页面（独立于 SPA，始终可用）
@app.get("/admin.html")
def admin_page():
    admin_file = os.path.join("frontend", "admin.html")
    if os.path.isfile(admin_file):
        return FileResponse(admin_file)
    raise HTTPException(status_code=404, detail="admin.html not found")

# 生产模式：托管前端 dist（开发模式下 dist/ 不存在则跳过）
DIST_DIR = "frontend/dist"
if os.path.isdir(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(os.path.join(DIST_DIR, "index.html"))
