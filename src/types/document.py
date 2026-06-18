from sqlalchemy import Column, Integer, String, BigInteger, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from pydantic import BaseModel
from datetime import datetime
from typing import List

from src.types.user import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(256), nullable=False)           # 原始文件名
    stored_name = Column(String(256), nullable=False)        # UUID 文件名
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    file_size = Column(BigInteger, nullable=False)
    chunk_count = Column(Integer, default=0)
    status = Column(String(16), default="processing")        # processing / ready / error
    is_public = Column(Boolean, default=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_size: int
    chunk_count: int
    status: str
    is_public: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    total: int
    documents: List[DocumentResponse]
