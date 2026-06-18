FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary 和 tiktoken 都有预编译 wheel，不需要系统编译工具
COPY requirements.txt .
# 先装 CPU 版 torch（避免默认拉 ~8GB CUDA 全家桶）
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# 预下载 Embedding 模型（避免首次请求超时）
ENV HF_HOME=/app/.cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

# 非 root 用户（需要访问 HF 缓存 + 上传目录）
RUN useradd -m appuser \
    && mkdir -p /app/data/documents \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
