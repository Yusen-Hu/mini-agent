import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config.settings import settings

logger = logging.getLogger(__name__)


# ── 限流 ─────────────────────────────────────────────────────

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


limiter = Limiter(key_func=get_user_key, default_limits=["60/minute"])


async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后重试"},
    )


# ── 异常处理 ─────────────────────────────────────────────────

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
