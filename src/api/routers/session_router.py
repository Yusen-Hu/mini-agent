from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.types.user import User
from src.types.session import ChatSession, ChatMessage
from src.types.chat import (
    SessionResponse,
    SessionListResponse,
    MessageResponse,
    MessageListResponse,
)
from src.services.auth import get_current_user
from config.database import get_db

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(ChatSession).filter(ChatSession.user_id == current_user.id)
    total = query.count()
    sessions = (
        query.order_by(ChatSession.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return SessionListResponse(total=total, sessions=sessions)


@router.get("/{session_uuid}/messages", response_model=MessageListResponse)
def list_messages(
    session_uuid: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_session_by_uuid(db, session_uuid, current_user.id)
    query = db.query(ChatMessage).filter(ChatMessage.session_id == session.id)
    total = query.count()
    messages = (
        query.order_by(ChatMessage.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return MessageListResponse(total=total, messages=messages)


@router.delete("/{session_uuid}", status_code=204)
def delete_session(
    session_uuid: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_session_by_uuid(db, session_uuid, current_user.id)
    # CASCADE 会自动删除 chat_messages
    db.delete(session)
    db.commit()


@router.patch("/{session_uuid}", response_model=SessionResponse)
def rename_session(
    session_uuid: UUID,
    title: str = Query(..., min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_session_by_uuid(db, session_uuid, current_user.id)
    session.title = title
    db.commit()
    db.refresh(session)
    return session


def _get_session_by_uuid(db: Session, target_uuid: UUID, user_id: int) -> ChatSession:
    """通过 UUID 获取会话，验证用户所有权。"""
    session = db.query(ChatSession).filter(
        ChatSession.session_uuid == target_uuid,
        ChatSession.user_id == user_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session
