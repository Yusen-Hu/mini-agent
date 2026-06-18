from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.types.chat import ChatRequest, ChatResponse
from src.types.user import User
from src.services.chat import chat, chat_stream
from src.services.auth import get_current_user
from config.database import get_db
from src.api.middleware import limiter

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("10/minute")
def chat_endpoint(
    request: Request,
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reply, session_uuid, agent_name = chat(
        req.message, req.session_id, user_id=current_user.id, db=db, agent_hint=req.agent_hint,
    )
    return ChatResponse(reply=reply, session_id=session_uuid, agent=agent_name)


@router.post("/chat/stream")
@limiter.limit("10/minute")
async def chat_stream_endpoint(
    request: Request,
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    # 注意：不注入 db，流式函数内部自己管理 SessionLocal 生命周期
    return StreamingResponse(
        chat_stream(req.message, req.session_id, user_id=current_user.id, agent_hint=req.agent_hint),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
