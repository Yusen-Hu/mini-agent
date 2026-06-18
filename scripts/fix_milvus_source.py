"""修复 Milvus source 字段乱码。

用法: python scripts/fix_milvus_source.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.database import SessionLocal
from src.types.document import Document
from skills.rag.collection import milvus_client, COLLECTION_NAME


def main():
    # 1. 从 DB 查 document_id → filename（已修复的中文名）
    with SessionLocal() as db:
        docs = db.query(Document.id, Document.filename).all()
    id_to_name = {str(d.id): d.filename for d in docs}
    print(f"DB 文档数: {len(id_to_name)}")

    # 2. 从 Milvus 查所有 chunk 的 document_id 和 source
    rows = milvus_client.query(
        collection_name=COLLECTION_NAME,
        filter="document_id != ''",
        output_fields=["document_id", "source", "text", "chunk_index", "user_id", "is_public", "embedding"],
        limit=16384,
    )
    print(f"Milvus chunk 数: {len(rows)}")

    # 3. 按 document_id 分组，找出需要修复的
    fixed = 0
    skipped = 0
    by_doc: dict[str, list] = {}
    for row in rows:
        doc_id = row["document_id"]
        by_doc.setdefault(doc_id, []).append(row)

    for doc_id, chunks in by_doc.items():
        db_name = id_to_name.get(doc_id)
        if not db_name:
            print(f"  跳过 document_id={doc_id}（DB 中无对应记录）")
            skipped += len(chunks)
            continue

        # 检查是否需要修复（取第一个 chunk 的 source）
        current_source = chunks[0].get("source", "")
        if current_source == db_name:
            continue  # 已经正确，跳过

        # 需要修复：delete + re-insert
        milvus_client.delete(COLLECTION_NAME, filter=f'document_id == "{doc_id}"')

        new_data = []
        for chunk in chunks:
            new_data.append({
                "document_id": doc_id,
                "text": chunk["text"],
                "source": db_name,
                "chunk_index": chunk.get("chunk_index", 0),
                "user_id": chunk["user_id"],
                "is_public": chunk["is_public"],
                "embedding": chunk["embedding"],
            })
        milvus_client.insert(COLLECTION_NAME, new_data)
        fixed += 1
        print(f"  修复 document_id={doc_id}: {current_source[:30]}... → {db_name}")

    print(f"\n完成：修复 {fixed} 篇文档，跳过 {skipped} 个 chunk")


if __name__ == "__main__":
    main()
