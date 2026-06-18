import os
import uuid
from fastapi import APIRouter, Depends, File, Request, UploadFile, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.settings import settings
from config.database import get_db
from src.types.user import User
from src.types.document import Document, DocumentResponse, DocumentListResponse
from src.services.auth import get_current_user
from skills.rag.ingestion import ingest_document, delete_document_chunks, compute_file_hash
from src.api.middleware import limiter

TEXT_EXTENSIONS = {".txt", ".md", ".html", ".htm"}


def _validate_text_content(file_path: str, ext: str) -> str | None:
    """检查文本文件是否真的是文本内容，返回错误信息或 None。"""
    if ext not in TEXT_EXTENSIONS:
        return None

    with open(file_path, "rb") as f:
        raw = f.read(10240)

    # 空字节检测：所有常见二进制格式（docx/zip/pdf/png 等）前 10KB 内必定包含 \x00
    if b"\x00" in raw:
        return "文件内容与扩展名不匹配，疑似二进制文件"

    return None


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=201)
@limiter.limit("5/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. 校验扩展名
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 2. UUID 文件名，保存到磁盘（流式 + 大小限制）
    safe_name = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    total = 0
    with open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 64):
            total += len(chunk)
            if total > max_bytes:
                f.close()
                os.remove(file_path)
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制",
                )
            f.write(chunk)

    # 3. 内容校验：文本文件必须真的是文本
    validation_error = _validate_text_content(file_path, ext)
    if validation_error:
        os.remove(file_path)
        raise HTTPException(status_code=422, detail=validation_error)

    # 4. SHA-256 去重（按用户，排除已失败的记录）
    file_hash = compute_file_hash(file_path)
    existing = db.query(Document).filter(
        Document.user_id == current_user.id,
        Document.file_hash == file_hash,
        Document.status != "error",
    ).first()
    if existing:
        os.remove(file_path)
        raise HTTPException(status_code=409, detail="文件已存在")

    # 5. 创建 DB 记录
    doc = Document(
        filename=file.filename,
        stored_name=safe_name,
        file_hash=file_hash,
        file_size=total,
        user_id=current_user.id,
        status="processing",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 6. 入库 Milvus
    try:
        chunk_count = ingest_document(
            file_path,
            document_id=str(doc.id),
            user_id=doc.user_id,
            is_public=doc.is_public,
            source_display_name=doc.filename,
        )
        doc.chunk_count = chunk_count
        doc.status = "ready"
        db.commit()

        # 清理同用户同 hash 的旧 error 记录
        db.query(Document).filter(
            Document.user_id == current_user.id,
            Document.file_hash == file_hash,
            Document.status == "error",
            Document.id != doc.id,
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        doc.status = "error"
        db.commit()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {e}")

    return doc


@router.get("", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Document).filter(Document.user_id == current_user.id)
    total = query.count()
    docs = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return DocumentListResponse(total=total, documents=docs)


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 删除 Milvus chunk
    delete_document_chunks(str(doc.id))

    # 删除磁盘文件
    file_path = os.path.join(settings.UPLOAD_DIR, doc.stored_name)
    if os.path.exists(file_path):
        os.remove(file_path)

    # 删除 DB 记录
    db.delete(doc)
    db.commit()


@router.post("/{document_id}/reindex", response_model=DocumentResponse)
def reindex_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = os.path.join(settings.UPLOAD_DIR, doc.stored_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="源文件已丢失")

    # 删除旧 chunk，重新入库
    delete_document_chunks(str(doc.id))
    doc.status = "processing"
    db.commit()

    try:
        chunk_count = ingest_document(
            file_path,
            document_id=str(doc.id),
            user_id=doc.user_id,
            is_public=doc.is_public,
            source_display_name=doc.filename,
        )
        doc.chunk_count = chunk_count
        doc.status = "ready"
        db.commit()
    except Exception as e:
        doc.status = "error"
        db.commit()
        raise HTTPException(status_code=500, detail=f"重新索引失败: {e}")

    return doc


class DocumentUpdate(BaseModel):
    is_public: bool | None = None


@router.patch("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    body: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    if body.is_public is not None and body.is_public != doc.is_public:
        doc.is_public = body.is_public

        # 同步 Milvus：查出所有 chunk → 删除 → 用新 is_public 重新插入
        from skills.rag.collection import milvus_client, COLLECTION_NAME
        doc_id_str = str(doc.id)
        rows = milvus_client.query(
            collection_name=COLLECTION_NAME,
            filter=f'document_id == "{doc_id_str}"',
            output_fields=["text", "source", "chunk_index", "embedding"],
            limit=16384,
        )
        if rows:
            milvus_client.delete(COLLECTION_NAME, filter=f'document_id == "{doc_id_str}"')
            new_data = []
            for row in rows:
                new_data.append({
                    "document_id": doc_id_str,
                    "text": row["text"],
                    "source": row.get("source", ""),
                    "chunk_index": row.get("chunk_index", 0),
                    "user_id": current_user.id,
                    "is_public": body.is_public,
                    "embedding": row["embedding"],
                })
            milvus_client.insert(COLLECTION_NAME, new_data)

        # 同步 BM25 索引
        from skills.rag.bm25_index import bm25_index
        if doc_id_str in bm25_index._chunk_metadata:
            for meta in bm25_index._chunk_metadata[doc_id_str]:
                meta["is_public"] = body.is_public

    db.commit()
    db.refresh(doc)
    return doc
