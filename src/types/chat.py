from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None  # None = 新会话，后端自动分配 UUID
    agent_hint: Optional[str] = None  # 调试用：general_chat / rag_agent


class ChatResponse(BaseModel):
    reply: str
    session_id: str  # UUID 字符串
    agent: str = "general_chat"  # 本次回答使用的 agent


class SessionResponse(BaseModel):
    session_uuid: UUID
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    total: int
    sessions: List[SessionResponse]


class MessageResponse(BaseModel):
    role: str
    content: str
    agent_name: Optional[str] = None
    extra_data: Optional[Any] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageListResponse(BaseModel):
    total: int
    messages: List[MessageResponse]
