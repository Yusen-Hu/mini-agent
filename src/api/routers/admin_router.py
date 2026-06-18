import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from config.database import get_db
from src.services.auth import require_admin
from src.types.user import User
from src.types.document import Document
from src.types.session import ChatSession, ChatMessage

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def admin_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        doc_count = db.query(sa_func.count(Document.id)).filter(Document.user_id == u.id).scalar()
        session_count = db.query(sa_func.count(ChatSession.id)).filter(ChatSession.user_id == u.id).scalar()
        result.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "document_count": doc_count,
            "session_count": session_count,
            "created_at": u.created_at,
        })
    return result


@router.get("/sessions")
def admin_sessions(
    user_id: int = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = db.query(ChatSession)
    if user_id is not None:
        query = query.filter(ChatSession.user_id == user_id)
    sessions = query.order_by(ChatSession.updated_at.desc()).limit(limit).all()
    return [
        {
            "session_uuid": str(s.session_uuid),
            "user_id": s.user_id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in sessions
    ]


@router.get("/sessions/{session_uuid}/messages")
def admin_session_messages(
    session_uuid: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        target_uuid = uuid.UUID(session_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 session_id 格式")

    session = db.query(ChatSession).filter(ChatSession.session_uuid == target_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    offset = (page - 1) * page_size
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    total = db.query(sa_func.count(ChatMessage.id)).filter(ChatMessage.session_id == session.id).scalar()
    return {
        "total": total,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "agent_name": m.agent_name,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.get("/documents")
def admin_documents(
    user_id: int = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = db.query(Document, User.username).join(User, Document.user_id == User.id)
    if user_id is not None:
        query = query.filter(Document.user_id == user_id)
    results = query.order_by(Document.created_at.desc()).all()
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "file_size": doc.file_size,
            "chunk_count": doc.chunk_count,
            "uploaded_by": username,
            "created_at": doc.created_at,
        }
        for doc, username in results
    ]


@router.delete("/documents/{doc_id}", status_code=204)
def admin_delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 清理 Milvus chunks
    try:
        from pymilvus import MilvusClient
        from config.settings import settings
        client = MilvusClient(uri=settings.MILVUS_URI)
        client.delete(
            collection_name="knowledge_base",
            filter=f"document_id == {doc_id}",
        )
    except Exception:
        pass

    # 删除文件
    import os
    file_path = os.path.join("data/documents", doc.stored_name)
    if os.path.exists(file_path):
        os.remove(file_path)

    # 删除 DB 记录
    db.delete(doc)
    db.commit()


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    # 路由分布：按 agent_name 统计 assistant 消息数
    route_dist = (
        db.query(ChatMessage.agent_name, sa_func.count(ChatMessage.id))
        .filter(ChatMessage.role == "assistant", ChatMessage.agent_name.isnot(None))
        .group_by(ChatMessage.agent_name)
        .all()
    )

    total_messages = db.query(sa_func.count(ChatMessage.id)).filter(ChatMessage.role == "user").scalar()
    total_users = db.query(sa_func.count(User.id)).scalar()
    total_docs = db.query(sa_func.count(Document.id)).scalar()
    total_sessions = db.query(sa_func.count(ChatSession.id)).scalar()

    return {
        "total_messages": total_messages,
        "total_users": total_users,
        "total_documents": total_docs,
        "total_sessions": total_sessions,
        "route_distribution": {name: count for name, count in route_dist},
    }
