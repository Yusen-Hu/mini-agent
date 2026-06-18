# Mini Agent — AGENTS.md

基于 LangGraph 的 AI Agent 应用，支持 RAG 知识库检索、文档分析、多 Agent 调度和流式对话。
本文件为 AI 助手提供项目上下文，协助开发、调试和功能扩展。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| Agent 引擎 | LangGraph `create_react_agent`（ReAct）+ 确定性流程（analysis_agent） |
| LLM 接口 | LiteLLM（统一 100+ Provider，当前 `openai/kimi-k2.5`） |
| 向量库 | Milvus（本地 Docker）+ etcd + MinIO |
| 检索策略 | Hybrid Retrieval（Dense + BM25 + BM25-primary RRF / 对称 RRF 可切换） |
| Embedding | HuggingFace `paraphrase-multilingual-MiniLM-L12-v2`，384 维，本地推理 |
| 数据库 | PostgreSQL + SQLAlchemy ORM |
| 认证 | JWT（passlib bcrypt） |
| 前端 | Vite + Vue 3 多组件 + Pinia + Vue Router + marked.js + KaTeX |
| Admin 页面 | 独立 `admin.html`（Vue 3 CDN，单文件） |
| 日志 | Python logging + JSON Formatter + contextvars |
| 基础设施 | Docker Compose（Milvus + PostgreSQL + etcd + MinIO + 后端 + 前端 Nginx） |

---

## 启动方式

```bash
# 1. 启动基础设施 + 后端 + 前端（Docker）
docker compose up -d

# 2. 本地开发后端（先停 Docker 后端容器释放 8000 端口）
docker stop mini_agent-backend-1
E:\1\python\envs\supermew\python.exe -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 3. 访问
# 聊天界面：http://127.0.0.1:8000
# Admin 页面：http://127.0.0.1:8000/admin.html
# API 文档：http://127.0.0.1:8000/docs
```

---

## 环境变量（.env）

```env
LLM_API_KEY=sk-xxx
LLM_MODEL=openai/kimi-k2.5
LLM_BASE_URL=https://api.moonshot.cn/v1
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/mini_agent
MILVUS_URI=http://127.0.0.1:19530
JWT_SECRET_KEY=change-me-in-production
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ANALYSIS_CHAR_BUDGET=8000
RETRIEVAL_STRATEGY=bm25_primary  # "symmetric_rrf" | "bm25_primary"
```

---

## 核心逻辑说明

### Multi-Agent 对话流程

```
用户消息
  → Supervisor 路由（正则快速路径 + LLM 语义路由 + doc_ids 提取）
  → 分发到子 Agent：
      ├── general_chat：通用对话（create_react_agent + get_current_time）
      ├── rag_agent：知识库检索（create_react_agent + 5 工具）
      └── analysis_agent：文档分析（确定性流程，代码控制）
  → 保存消息到 PostgreSQL
  → 流式 SSE 返回前端
```

### RAG 检索流程（Hybrid Retrieval）

```
文档上传
  → PyMuPDF 加载 + 分块（chunk_size=500, overlap=50）
  → HuggingFace Embedding 向量化（384 维）→ Milvus

用户提问
  → Dense 检索：embed_query → Milvus search（top_k=20）
  → BM25 检索：jieba 分词 → BM25 索引（top_k=20）
  → 融合策略（可切换）：
      ├── bm25_primary：BM25 候选池 + Dense rerank 加分（HR@8=88%）
      └── symmetric_rrf：Dense + BM25 对称 RRF 融合
  → 取 top_k=8
```

### 评测框架

```
scripts/eval_sets/eval_set_2026-06-15.json  — 55 题（25 retrieval + 15 negative + 15 routing）
scripts/eval_retrieval.py                   — 跑 HR@8 / MRR@8 基线
scripts/eval_results/                       — 结果 JSON
```

---

## 注意事项

- `.env` 包含 API Key，**不要提交到版本控制**
- Python 环境统一使用 `E:\1\python\envs\supermew\python.exe`（Python 3.12），**禁止使用 conda 命令**
- Docker 后端容器绑了 8000 端口，本地跑 uvicorn 需先 `docker stop mini_agent-backend-1`
- 改了 `.vue` 文件必须 `npm run build`，uvicorn serve 的是 `frontend/dist/`
- `EMBEDDING_DIM` 改动后必须删除并重建 Milvus Collection
