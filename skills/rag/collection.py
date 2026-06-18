from pymilvus import MilvusClient, DataType
from langchain_huggingface import HuggingFaceEmbeddings

from config.settings import settings

# ── 1. 初始化 Milvus 客户端 ────────────────────────────────────
milvus_client = MilvusClient(uri=settings.MILVUS_URI)

COLLECTION_NAME = "knowledge_base"
EMBEDDING_DIM = settings.EMBEDDING_DIM

# ── 2. 本地 Embedding 模型 ─────────────────────────────────────
embeddings = HuggingFaceEmbeddings(
    model_name=settings.EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
)

# ── 3. 创建 Collection ─────────────────────────────────────────
def init_collection():
    if milvus_client.has_collection(COLLECTION_NAME):
        return

    schema = milvus_client.create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("source", DataType.VARCHAR, max_length=512)
    schema.add_field("document_id", DataType.VARCHAR, max_length=36)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("user_id", DataType.INT64)
    schema.add_field("is_public", DataType.BOOL)

    index_params = milvus_client.prepare_index_params()
    index_params.add_index("embedding", metric_type="IP", index_type="HNSW", params={"M": 8, "efConstruction": 64})

    milvus_client.create_collection(COLLECTION_NAME, schema=schema, index_params=index_params)
    print(f"Collection {COLLECTION_NAME} 创建成功")
