from pymilvus import MilvusClient
from config.settings import settings

c = MilvusClient(uri=settings.MILVUS_URI)

results = c.query(
    collection_name='knowledge_base',
    filter='document_id != ""',
    output_fields=['text', 'source', 'document_id', 'user_id', 'is_public'],
    limit=50,
)

print(f"共 {len(results)} 条 chunk:")
for r in results:
    print(f"  doc_id={r['document_id']} user={r['user_id']} public={r['is_public']} src={r['source']} text={r['text'][:60]}...")
