import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Debug
    DEBUG: bool = False

    # LLM
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "openai/kimi-k2.5"  # litellm 格式: provider/model-name
    LLM_BASE_URL: str = "https://api.moonshot.cn/v1"  # 兼容 API 需要，原生 API 留空

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent"

    # Milvus
    MILVUS_URI: str = "http://127.0.0.1:19530"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # Embedding
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384

    # RAG
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    TOP_K: int = 3
    CHAT_HISTORY_LIMIT: int = 10  # 每次加载最近 N 轮对话（1 轮 = user + assistant）
    MAX_HISTORY_TOKENS: int = 4000  # 历史消息最大 token 数（双重截断：条数 + token，取保守值）
    ANALYSIS_CHAR_BUDGET: int = 8000  # 单篇文档分析时的字符预算（约 4K token，Moonshot 8K 模型安全范围）

    # Hybrid Retrieval (Phase 3)
    DENSE_TOP_K: int = 20
    BM25_TOP_K: int = 20
    FINAL_TOP_K: int = 8
    RRF_K: int = 60
    RRF_ALPHA: float = 0.5
    RETRIEVAL_STRATEGY: str = "symmetric_rrf"  # "symmetric_rrf" | "bm25_primary"
    DENSE_BONUS_WEIGHT: float = 0.3  # bm25_primary 模式下 Dense 加分权重

    # Upload
    UPLOAD_DIR: str = "data/documents"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".docx", ".doc", ".txt", ".md", ".html"]

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/agent.json.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    # Memory
    MEMORY_SUMMARY_ENABLED: bool = True       # 启用对话摘要压缩（关闭则回退到旧截断逻辑）
    MEMORY_SUMMARY_KEEP_RECENT: int = 8       # 保留最近 N 条消息原文，更早的压缩成摘要
    MEMORY_SUMMARY_MAX_TOKENS: int = 500      # 摘要最大 token 数

    # CORS
    CORS_ORIGINS: List[str] = ["*"]

    # LangSmith（可选，.env 中设置 API Key 后自动启用）
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "mini-agent"


settings = Settings()

# 导出 LangSmith 环境变量（LangChain 直接读 os.environ，不读 pydantic-settings）
if settings.LANGCHAIN_TRACING_V2:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
if settings.LANGCHAIN_API_KEY:
    os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
if settings.LANGCHAIN_PROJECT:
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
