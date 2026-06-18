# Phase 3：RAG 检索质量提升 — 实施计划

## 策略

**核心目标：** 混合检索 + 引用溯源，大幅提升 RAG 回答质量与可追溯性。

**交付分两阶段：**
- **Phase 3A（主线）：** Hybrid Retrieval（Dense + BM25 + RRF）+ 引用透传 + 前端引用展示，完整闭环交付。
- **Phase 3B（增强）：** Cross-encoder Rerank，在 3A 稳定后独立交付，单独验收收益。

**核心原则：** Phase 3 最关键的不是"把技术名词都接上"，而是先把"可重建、可过滤、可追溯、并发安全"的检索链路做稳。稳了以后，再加 Rerank，收益才能干净地量化。

**不做：** 查询改写（rewrite_query，留给后续迭代）、语义分块（保持 RecursiveCharacterTextSplitter）、Milvus 版本升级（用 Python 侧 BM25 替代）。

---

## 架构总览

### 升级后检索流程

```
用户问题
  → Dense 向量检索（Milvus HNSW, dense_top_k=20）
  → Sparse BM25 检索（Python rank_bm25, bm25_top_k=20）
  → RRF 融合排序（Reciprocal Rank Fusion, k=60, alpha 权重）
  → 去重（document_id + chunk_index 唯一键；chunk_index 缺失时退化为 document_id + text hash）
  → 取 final_top_k=5
  → 生成 relevance_label（按排序位次）
  → [Phase 3B] Cross-encoder Rerank（可选开关）
  → 返回结果 + 引用元数据
```

### 模块关系

```
skills/rag/
├── collection.py        # Milvus 连接 + Embedding（不变）
├── ingestion.py         # 文档入库（修改：加 chunk_index + BM25 触发）
├── retrieval.py         # 检索引擎（重构：Dense + BM25 + RRF）
├── bm25_index.py        # [新增] BM25 索引模块
└── reranker.py          # [Phase 3B 新增] Cross-encoder Rerank

src/agents/tools.py      # 结构化返回 + 引用元数据透传
src/services/chat.py     # SSE 协议扩展：citation 事件
src/types/chat.py        # ChatRequest 扩展

frontend/src/
├── stores/chat.js       # 解析 citation 事件（含 JSON 容错）
├── components/
│   └── CitationCard.vue # [新增] 引用展示组件
```

---

## 执行步骤

### Step 1：SSE 协议统一 + Citation 数据结构定稿

**目标：** 所有模块的开发前提——接口契约先行。

#### 1.1 SSE 事件格式统一

当前协议用字符串特殊标记（`[SESSION:uuid]`、`[DONE]`），随着事件类型增加会变成字符串分支地狱。统一改为 JSON 格式：

| 事件类型 | JSON 格式 | 说明 |
|---------|----------|------|
| token | `{"type":"token","content":"你好"}` | 流式文字，逐 token |
| session | `{"type":"session","session_id":"..."}` | 会话 ID |
| citation | `{"type":"citation","schema_version":1,"items":[...]}` | 引用列表，回复结束后一次性发送 |
| done | `{"type":"done"}` | 流结束 |

**关键决策：** Citation 事件发送时机为"先流 token → 最后一次性发 citation 列表 → 再发 done"。不要 token/citation 交错发送。

**改动文件：**
- `src/services/chat.py`：yield 格式从 `data: {text}\n\n` 改为 `data: {"type":"token","content":"{text}"}\n\n`，同理 session 和 done
- `frontend/src/stores/chat.js`：SSE 解析从字符串分支改为 `JSON.parse(data)` + switch on type

**JSON 解析容错：** 前端 chat store 对 `JSON.parse` 失败要兜底——`try/catch` 包裹，失败则 `console.warn` 记录 + 跳过该事件，继续读下一条。单条坏事件不能打断整个流式会话。

**向后兼容：** 这是破坏性变更，后端和前端必须同时上线。建议单独一个 commit，方便回滚。

#### 1.2 Citation 数据结构定稿

```json
{
  "type": "citation",
  "schema_version": 1,
  "items": [
    {
      "document_id": "42",           // 必须：文档唯一 ID
      "filename": "产品手册.pdf",      // 必须：供前端展示的文件名
      "chunk_index": 3,              // 推荐：新文档必有，老文档在降级方案 B 下可为空
      "rrf_score": 0.032,            // 必须：RRF 原始分数（非 0-1 归一化）
      "relevance_label": "高度相关",  // 必须：后端按排序位次生成的语义标签
      "snippet": "前后各100字的摘要",  // 必须：截取片段，不发完整 chunk
      "retrieval_method": "hybrid",  // 建议："hybrid" / "dense" / "bm25"
      "page_number": 12              // 可选：PDF 页码
    }
  ]
}
```

**score 语义说明：** RRF 原始分数是 `1/(k+rank)`，k=60 时第一名约 0.016，不是直觉上的 0-1。不使用归一化（归一化策略不稳定，不同查询间不可比）。前端展示标签由后端按排序位次生成：
- 排名 1-2 → "高度相关"
- 排名 3-4 → "相关"
- 排名 5+ → "参考"

**chunk_index 说明：** 新文档从 ingestion 自动赋值，必有。老文档如走降级方案 B（顺序不可恢复时），chunk_index 可为空。此时引用仍包含 document_id + filename + rrf_score + relevance_label + snippet，用户可追溯到文件级别。

**snippet 设计：** 取匹配片段前后各 100 字，不发送完整 chunk 文本（太大、有隐私风险）。

**schema_version 说明：** 固定为 1。以后扩展字段（page_number、highlight_range 等）时递增版本号，前端可根据版本决定解析策略，避免硬解析出错。

**交付物：** 协议文档 + Citation 结构体定义，前后端确认一致。

---

### Step 2：BM25 索引模块（skills/rag/bm25_index.py）

**目标：** 独立封装 BM25 索引，解决生命周期管理和并发安全。

#### 2.1 模块接口

| 方法 | 触发时机 | 说明 |
|------|---------|------|
| `build_index()` | 服务启动 | 全量从 Milvus 拉取 chunk 文本，重建索引 |
| `refresh_index()` | 文档上传完成后 | 全量重建（适用条件：chunk 总数 < 10,000） |
| `remove_document(doc_id)` | 文档删除时 | 从索引中移除指定文档的所有 chunk |
| `search(query, allowed_doc_ids)` | 检索时 | 支持权限过滤 |
| `reindex()` | 文档重建时 | 原子替换旧索引对象 |
| `status()` | 调试/监控 | 返回索引状态信息 |

**依赖：** `rank_bm25` 库（纯 Python，`pip install rank-bm25`）。

#### 2.2 索引生命周期

| 场景 | 处理策略 |
|------|---------|
| 服务启动 | 调用 `build_index()`，全量从 Milvus 重建 |
| 文档上传 | ingestion.py 完成后显式调用 `refresh_index()` |
| 文档删除 | 删除接口调用 `remove_document(document_id)` |
| 文档重建（reindex） | 先构建新索引，再原子替换 |
| 服务重启 | 自动 `build_index()`，无需人工干预 |

**refresh_index() 适用边界：** 当前采用全量重建，适用条件为 chunk 总数 < 10,000。超过此阈值需升级为增量索引或迁移到 Milvus 2.5+ 原生 sparse 方案。在达到阈值前，此方案是最简可靠的实现。

#### 2.3 并发安全

**风险场景：** 用户 A 触发文档上传（重建索引），用户 B 同时检索。BM25Okapi 对象在重建期间被替换，B 可能拿到半初始化状态。

**解决方案：读写锁 + 原子替换。**
- 使用 `threading.Lock` 保护索引替换
- 重建时先在本地变量构建新的 BM25Okapi 对象
- 构建完成后，加锁，原子替换 `self._index` 引用，立即释放
- 读操作（search）使用当前 `_index` 引用（Python 引用赋值是原子的）
- 重建期间的检索请求正常使用旧索引，不中断

#### 2.4 BM25 失败降级

**必须实现。** 如果 `build_index()` 或 `refresh_index()` 失败（OOM、Milvus 超时等），检索应降级为纯 Dense，不能整个检索链路挂掉。

```python
class BM25Index:
    def __init__(self):
        self._index = None          # BM25Okapi 对象，None 表示未就绪
        self._doc_chunks = {}       # {doc_id: [chunk_texts]}
        self._chunk_metadata = {}   # {doc_id: [{chunk_index, user_id, is_public}]}
        self._lock = threading.Lock()
        self._last_built_at = None  # 最近一次构建时间
        self._last_error = None     # 最近一次错误

    def search(self, query, allowed_doc_ids, top_k=20):
        if self._index is None:
            return []  # BM25 未就绪，返回空，retrieval.py 降级为纯 Dense
        # ... 正常检索逻辑

    def status(self):
        """返回索引状态，用于调试和监控。"""
        return {
            "ready": self._index is not None,
            "chunk_count": sum(len(v) for v in self._doc_chunks.values()),
            "document_count": len(self._doc_chunks),
            "last_built_at": self._last_built_at,
            "error": self._last_error,
        }
```

#### 2.5 状态暴露（status()）

`status()` 方法返回 `{ready, chunk_count, document_count, last_built_at, error}`，启动日志中打印一次，文档增删后打印一次。排查问题时直接看日志，不需要额外 HTTP endpoint。后续 Phase 7 接到 `/health` 端点。

**交付物：** `skills/rag/bm25_index.py`，单元测试验证基本搜索、并发安全、失败降级、状态暴露。

---

### Step 3：Ingestion 变更 + 迁移策略

**目标：** 入库流程扩展，支持 chunk_index 和 BM25 触发。

**改动文件：** `skills/rag/ingestion.py`

#### 3.1 变更清单

1. **chunk_index 字段写入：** 每个 chunk 写入 Milvus 时赋值递增序号（从 0 开始）
2. **document_id 确认：** 确保每个 chunk 携带所属文档的唯一 ID（已有，确认即可）
3. **BM25 触发点：** 所有 chunk 写入 Milvus 完成后，显式调用 `bm25_index.refresh_index()`

**修改 `ingest_document()`：**
```python
def ingest_document(file_path, document_id, user_id, is_public, source_display_name):
    chunks = load_and_split(file_path)
    texts = [c.page_content for c in chunks]
    vectors = embeddings.embed_documents(texts)

    data = [
        {
            "embedding": vectors[i],
            "text": texts[i],
            "source": source_display_name,
            "document_id": str(document_id),
            "user_id": int(user_id),
            "is_public": bool(is_public),
            "chunk_index": i,  # [新增] chunk 序号
        }
        for i in range(len(texts)]
    ]
    milvus_client.insert(COLLECTION_NAME, data)

    # [新增] 触发 BM25 索引更新
    from skills.rag.bm25_index import bm25_index
    bm25_index.refresh_index()

    return len(data)
```

#### 3.2 现有数据迁移策略

**核心问题：** Milvus 现有数据没有 chunk_index 字段。导出时如果没有稳定排序依据，赋值的 chunk_index 不等于原始分块顺序，会导致引用定位不可靠。

**决策规则：**
- 如果现有数据能可靠恢复 chunk 顺序（即入库时有原始顺序信息，或 document_id 分组内有稳定排序字段）→ 执行迁移脚本
- **如果无法可靠恢复 → 不迁移，chunk_index 暂降为增强项**

**降级方案 B（推荐，当顺序不可靠时）：**
- 现有数据不回填 chunk_index
- 第一版引用做到"文件名 + snippet + rrf_score + relevance_label"，不包含 chunk_index，仍然可用
- 新文档从 ingestion 自动赋值 chunk_index
- 老文档在用户重新上传时自然补齐

**迁移脚本（如顺序可恢复时）：**
```python
# scripts/migrate_chunk_index.py
# 1. drop 旧 collection
# 2. 创建新 collection（含 chunk_index 字段）
# 3. 从 Milvus 导出所有 chunk（按 document_id 分组）
# 4. 每组内按某种稳定规则排序，赋值 chunk_index
# 5. 重新写入
```

**交付物：** ingestion.py 修改 + 迁移脚本（或确认走降级方案 B 的记录）。

---

### Step 4：Collection Schema 扩展

**目标：** Milvus Collection 加 chunk_index 字段。

**改动文件：** `skills/rag/collection.py`

**变更：**
```python
def init_collection():
    if milvus_client.has_collection(COLLECTION_NAME):
        return

    schema = milvus_client.create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("source", DataType.VARCHAR, max_length=512)
    schema.add_field("document_id", DataType.VARCHAR, max_length=36)
    schema.add_field("chunk_index", DataType.INT64)  # [新增]
    schema.add_field("user_id", DataType.INT64)
    schema.add_field("is_public", DataType.BOOL)
    # ... index 不变
```

**注意：** Collection 已存在时 `has_collection()` 返回 True，不会重建。需要在迁移脚本中处理（drop → recreate → import）。

**交付物：** collection.py 修改 + 迁移脚本。

---

### Step 5：Hybrid Retrieval 核心重构（skills/rag/retrieval.py）

**目标：** 实现 Dense + BM25 + RRF 混合检索。

#### 5.1 三层 top_k 参数

| 参数 | 默认值 | 说明 | 对外暴露 |
|------|--------|------|---------|
| `dense_top_k` | 20 | Dense 向量检索候选数 | 内部配置 |
| `bm25_top_k` | 20 | BM25 候选数 | 内部配置 |
| `final_top_k` | 5 | RRF 融合后返回数 | 给 tools.py 暴露 |

**设计原则：** 只暴露 `final_top_k` 给上层，`dense_top_k` 和 `bm25_top_k` 作为内部配置（config/settings.py），不向上蔓延。

#### 5.2 RRF 融合公式

```
score = alpha * (1 / (k + rank_dense)) + (1 - alpha) * (1 / (k + rank_bm25))
```

- `k`：平滑系数，默认 60（学术文献推荐值）
- `alpha`：Dense 权重，默认 0.5（等权）。可配置，方便 A/B 测试

**配置项（新增到 config/settings.py）：**
```python
DENSE_TOP_K: int = 20
BM25_TOP_K: int = 20
FINAL_TOP_K: int = 5
RRF_K: int = 60
RRF_ALPHA: float = 0.5
```

#### 5.3 去重逻辑

**chunk 唯一键：** `document_id` + `chunk_index`

Dense 和 BM25 可能返回同一个 chunk，RRF 融合时以唯一键去重：
- 两路都有 → 合并分数（RRF 公式）
- 只有一路有 → 取该路分数

**chunk_index 缺失时的 fallback：** 当老文档没有 chunk_index（走降级方案 B 时），退化为 `document_id` + `text hash`（对 chunk 文本取前 200 字的 hash）作为临时唯一键。保证去重逻辑在任何情况下都能工作。

#### 5.4 权限过滤（分离策略）

**Dense 侧：** 直接用 Milvus filter 表达式（`user_id == X or is_public == true`），不需要先查 DB 得到 ID 列表。Milvus 原生支持字段过滤。

**BM25 侧：** 构建索引时将 `document_id` → `{user_id, is_public}` 的元数据映射存入 `self._chunk_metadata`。检索时就地过滤，不传显式 ID 列表。

**不强行统一数据结构。** 两边的过滤机制完全不同，各自用各自最高效的方式。

#### 5.5 relevance_label 生成

RRF 融合排序后，按位次生成标签：

```python
def _assign_relevance_label(rank: int) -> str:
    if rank <= 2:
        return "高度相关"
    elif rank <= 4:
        return "相关"
    else:
        return "参考"
```

标签在后端生成，前端直接展示，不做二次判断。

#### 5.6 重构后的 `search_documents()`

```python
def search_documents(query, top_k=None, *, user_id, public_only=False):
    if top_k is None:
        top_k = settings.FINAL_TOP_K

    # 1. Dense 检索（Milvus filter 表达式过滤权限）
    dense_results = _dense_search(query, user_id, public_only, settings.DENSE_TOP_K)

    # 2. BM25 检索（本地元数据过滤，失败降级为空列表）
    bm25_results = _bm25_search(query, user_id, public_only, settings.BM25_TOP_K)

    # 3. RRF 融合 + 去重
    fused = _rrf_fusion(dense_results, bm25_results, settings.RRF_K, settings.RRF_ALPHA)

    # 4. 取 top_k + 生成 relevance_label + snippet
    results = []
    for rank, r in enumerate(fused[:top_k], 1):
        r["relevance_label"] = _assign_relevance_label(rank)
        r["snippet"] = _extract_snippet(r["text"], match_pos=r.get("match_pos"))
        results.append(r)

    return results
```

**BM25 降级：** 如果 `bm25_index.search()` 返回空（索引未就绪），整个检索退化为纯 Dense，不报错、不中断。

**交付物：** retrieval.py 重构，混合检索 + RRF + 去重 + 降级 + relevance_label。

---

### Step 6：引用元数据透传（tools.py + chat.py）

**目标：** 引用数据不经过 LLM，工具执行后直接透传给前端。

#### 6.1 tools.py 结构化返回

当前 `search_knowledge_base` 返回纯文本拼接。改为：

```python
# ContextVar 存储引用数据（跨 tool → chat 层传递）
current_citations: ContextVar[list] = ContextVar("current_citations", default=[])

@tool
def search_knowledge_base(query: str) -> str:
    """当用户询问知识库中的内容、上传的文档相关问题时调用此工具。"""
    uid = current_user_id.get()
    results = search_documents(query, user_id=uid)

    if not results:
        return "知识库中没有找到相关内容。"

    # 给 LLM 的文本（保持现有格式）
    output = ""
    for i, r in enumerate(results):
        output += f"【片段{i+1}】来源：{r['source']}\n{r['text']}\n\n"

    # [新增] 存储引用数据给 chat.py 用
    citations = [
        {
            "document_id": r["document_id"],
            "filename": r["source"],
            "chunk_index": r.get("chunk_index"),  # 可能为 None（降级方案 B）
            "rrf_score": r["score"],
            "relevance_label": r["relevance_label"],
            "snippet": r["snippet"],
            "retrieval_method": r.get("retrieval_method", "hybrid"),
        }
        for r in results
    ]
    current_citations.set(citations)

    return output
```

**核心原则：** 引用数据不走 LLM（LLM 可能遗漏或改写），由检索链路直接产生，通过 ContextVar 透传。

#### 6.2 chat.py SSE 输出

在流式回复结束后，从 ContextVar 取出 citations，发送 citation 事件：

```python
async def chat_stream(message, session_id, user_id):
    # ... 现有逻辑（获取 session、加载历史、保存用户消息）

    current_citations.set([])  # 清空引用
    full_reply = ""
    try:
        async for chunk in agent.astream(...):
            # ... 逐 token yield
    finally:
        if full_reply:
            # 保存 assistant 消息
            ...

    # [新增] 发送引用
    citations = current_citations.get()
    if citations:
        citation_event = {
            "type": "citation",
            "schema_version": 1,
            "items": citations,
        }
        yield f"data: {json.dumps(citation_event, ensure_ascii=False)}\n\n"

    yield 'data: {"type":"done"}\n\n'
```

**注意：** 如果本次对话没有触发知识库检索（纯对话模式），不发送 citation 事件，直接发 done。前端需要兼容"没有 citation"的情况。

**交付物：** tools.py 改造 + chat.py SSE 扩展。

---

### Step 7：前端 CitationCard

**目标：** 在消息气泡下方展示引用列表。

#### 7.1 交互决策（已定）

| 交互点 | 方案 |
|--------|------|
| 排序方式 | 按分数排序 |
| 点击行为 | 无响应（后期加预览） |
| 标签展示 | 直接使用后端生成的 relevance_label |
| 默认状态 | 折叠，用户主动展开 |

#### 7.2 chat store 改动

`frontend/src/stores/chat.js` 的 `sendMessage()` 需要解析新协议：

```javascript
// SSE 解析改为 JSON（含容错）
for (const line of lines) {
  if (!line.startsWith('data: ')) continue
  let data
  try {
    data = JSON.parse(line.slice(6))
  } catch {
    console.warn('SSE JSON parse error:', line)
    continue  // 跳过坏事件，不打断整个流
  }
  switch (data.type) {
    case 'token':
      reply += data.content
      messages.value[botIdx] = { role: 'ai', content: reply, thinking: false }
      break
    case 'session':
      sessionId.value = data.session_id
      localStorage.setItem('session_id', data.session_id)
      break
    case 'citation':
      if (data.schema_version === 1) {
        messages.value[botIdx].citations = data.items
      }
      break
    case 'done':
      return  // 结束循环
  }
}
```

**schema_version 兼容：** 前端检查 `schema_version === 1` 再解析，未来版本扩展字段时前端不会因未知字段崩溃。

#### 7.3 CitationCard.vue

新建 `frontend/src/components/CitationCard.vue`：

- props：`citations`（数组）
- 显示：文件名 + relevance_label（后端已生成，前端直接展示）
- 默认折叠，点击展开
- 无引用时不显示
- chunk_index 为空时只显示文件名 + relevance_label，不显示"第 N 段"

**Phase 3 必做：** 消息气泡下方显示引用列表；文件名 + relevance_label；可折叠；没有 citation 时不显示。

**后期迭代：** 点击引用打开文件预览；按文档来源聚合；相邻 chunk 合并；移动端适配。

**交付物：** chat.js 协议解析改造（含容错 + schema_version 兼容）+ CitationCard.vue + ChatView 集成。

---

### Step 8：检索质量评估

**目标：** 量化 Hybrid 检索相比纯 Dense 的收益。

#### 8.1 测试集准备

- 准备 10-20 个测试问题，覆盖知识库中有明确答案的场景
- 优先标注每个问题的"理想来源"为 `document_id` + `chunk_index`
- 若历史文档无 chunk_index（降级方案 B），则标注 `document_id` + `snippet/text match`
- 用 Python 脚本 + CSV 记录结果

#### 8.2 评估指标

| 指标 | 说明 |
|------|------|
| Recall@5 | 前 5 个结果中包含正确来源的比例（核心指标） |
| MRR | 正确结果排在第几位的倒数（可选） |
| P95 延迟 | 检索链路整体延迟（3B 上线后重点关注） |

#### 8.3 对比验证

| 阶段 | Recall@5 | P95 延迟 |
|------|----------|----------|
| 纯 Dense（当前） | baseline | baseline |
| 3A Hybrid | 对比提升 | 对比延迟 |
| 3B Rerank | 对比提升 | 对比延迟 |

**交付物：** 评估脚本 + 测试集 + 三阶段对比结果。

---

### Step 9：Phase 3B — Rerank 增强

**目标：** 在 3A 稳定后独立交付 Cross-encoder Rerank。

#### 9.1 技术方案

- **模型：** `cross-encoder/ms-marco-MiniLM-L-6-v2`（约 80MB，本地推理）
- **输入：** `(query, chunk_text)` 对，对 RRF 后的 top_k 结果重排序
- **加载时机：** 与 embedding 模型一起，服务启动时加载，全局缓存
- **开关：** `enable_rerank` 配置参数，false 时系统正常走 3A 链路
- **模块位置：** `skills/rag/reranker.py`，独立封装

#### 9.2 失败降级

Rerank 模型加载失败或推理异常时，无损降级回 3A Hybrid 链路，不报错。

#### 9.3 验收标准

- `enable_rerank=false` 时系统完整可用，结果与 3A 一致
- `enable_rerank=true` 时，Recall@5 相比 3A 有可量化提升
- Rerank 延迟 P95 < 500ms

**改动文件：**
- `skills/rag/reranker.py`（新建）
- `skills/rag/retrieval.py`（加 Rerank 步骤）
- `src/types/chat.py`（ChatRequest 加 `enable_rerank`）
- `config/settings.py`（加 `ENABLE_RERANK` 配置）

**交付物：** reranker.py + retrieval.py 集成 + 评估对比。

---

## 文件变更总览

### Phase 3A

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/settings.py` | 修改 | 加 DENSE_TOP_K、BM25_TOP_K、FINAL_TOP_K、RRF_K、RRF_ALPHA |
| `skills/rag/bm25_index.py` | 新建 | BM25 索引模块（生命周期 + 并发安全 + 失败降级 + status()） |
| `skills/rag/collection.py` | 修改 | Schema 加 chunk_index 字段 |
| `skills/rag/ingestion.py` | 修改 | chunk_index 赋值 + BM25 触发 |
| `skills/rag/retrieval.py` | 重构 | Dense + BM25 + RRF 混合检索 + relevance_label |
| `src/agents/tools.py` | 修改 | 结构化返回 + ContextVar 存储引用 |
| `src/services/chat.py` | 修改 | SSE 协议改为 JSON + citation 事件（含 schema_version） |
| `frontend/src/stores/chat.js` | 修改 | JSON 协议解析（含容错） + citation 存储 + schema_version 兼容 |
| `frontend/src/components/CitationCard.vue` | 新建 | 引用展示组件 |
| `scripts/migrate_chunk_index.py` | 新建 | 迁移脚本（仅在顺序可恢复时执行） |

### Phase 3B

| 文件 | 操作 | 说明 |
|------|------|------|
| `skills/rag/reranker.py` | 新建 | Cross-encoder Rerank 模块 |
| `skills/rag/retrieval.py` | 修改 | 集成 Rerank 步骤 |
| `src/types/chat.py` | 修改 | ChatRequest 加 enable_rerank |
| `config/settings.py` | 修改 | 加 ENABLE_RERANK 配置 |

---

## 综合风险矩阵

| 风险 | 等级 | 应对方案 |
|------|------|---------|
| BM25 并发写竞态 | 高 | 读写锁 + 原子替换索引对象 |
| BM25 索引一致性 | 高 | 明确触发点，Ingestion/删除/reindex 都调用对应方法 |
| BM25 初始化失败 | 高 | 失败降级为纯 Dense，不阻塞服务启动 |
| chunk_index 迁移顺序不可靠 | 高 | 无法可靠恢复则走降级方案 B，不硬迁移 |
| Collection 重建（chunk_index） | 中 | 迁移脚本，chunk_index 可降为增强项 |
| Rerank 首次加载慢 | 中 | 服务启动预热，全局缓存 |
| SSE 格式破坏性变更 | 中 | 后端前端同时上线，单独 commit 方便回滚 |
| Dense/BM25 返回同一 chunk | 中 | document_id + chunk_index 唯一键去重（缺 chunk_index 时用 text hash） |
| allowed_doc_ids 列表过大 | 低 | Dense 用 Milvus filter，BM25 用本地元数据，不传显式列表 |

---

## 完整验收标准

### 功能

- [ ] Hybrid 检索结果相比纯 Dense 可解释来源，检索路径可追溯
- [ ] 每条知识库回答都附带结构化引用列表（含 document_id、rrf_score、relevance_label、snippet）
- [ ] chunk_index 缺失时引用仍能正常展示（文件名 + relevance_label + snippet）
- [ ] 没触发知识库检索时不显示引用区域
- [ ] citation 事件包含 schema_version: 1

### 安全

- [ ] 用户 A 无法看到用户 B 私有文档的引用（Dense 用 Milvus filter，BM25 用本地元数据过滤）

### 一致性

- [ ] 删除/重建文档后，BM25 不再引用已失效 chunk
- [ ] 服务重启后 BM25 索引自动恢复，无需人工干预
- [ ] BM25 status() 反映正确状态（ready/chunk_count/last_built_at/error）

### 质量

- [ ] 10-20 个测试问题，Recall@5 相比纯 Dense 有可量化提升

### 容灾

- [ ] BM25 初始化失败时降级为纯 Dense，服务正常启动
- [ ] BM25 重建期间检索请求不中断
- [ ] 单条 SSE 坏事件不打断整个流式会话
- [ ] enable_rerank=false 时系统正常可用（3B）

---

## 执行优先级

| # | 执行项 | 核心原因 | 阶段 |
|---|--------|---------|------|
| 1 | SSE 协议统一 + Citation 结构定稿 | 接口契约先定死，前后端可并行 | 3A 前置 |
| 2 | BM25 模块 + 并发安全 + 失败降级 + status() | 工程隐患最大的点，先解决 | 3A |
| 3 | Collection Schema 扩展（chunk_index） | Ingestion 和 Retrieval 的前提 | 3A |
| 4 | Ingestion 变更 + 迁移/降级决策 | 写入侧适配新 Schema | 3A |
| 5 | Hybrid Retrieval 核心重构 | 核心功能实现 | 3A |
| 6 | 引用元数据透传（tools + chat） | 完成后端闭环 | 3A |
| 7 | 前端 CitationCard（含 SSE 容错） | 完成前端闭环 | 3A |
| 8 | 检索质量评估 | 量化验收收益 | 3A 验收 |
| 9 | Rerank 增强 | 3A 稳定后独立交付 | 3B |

---

## 与 UPGRADE_PLAN 的差异

| 原计划 | 本阶段实际 | 原因 |
|--------|-----------|------|
| Milvus BM25 内置函数 | Python 侧 rank_bm25 | Milvus 2.4.0 无原生 BM25，不升级版本 |
| Rerank 与 Hybrid 一起交付 | 分 3A/3B 两阶段 | 先闭环基础链路，再叠加增强，便于量化收益 |
| 无并发安全设计 | BM25 读写锁 + 原子替换 | 多用户并发检索时索引重建会出问题 |
| 无降级策略 | BM25 失败降级为纯 Dense | 保证服务可用性 |
| 无评估方案 | Recall@5 量化评估 | 无法验收收益 |
| 无 SSE 协议统一 | 全面改为 JSON 格式 | 字符串分支维护成本随事件类型增加而爆炸 |
| Citation 无 snippet | 截取前后各 100 字 | 安全考虑 + 减少传输量 |
| score 字段 | 改为 rrf_score + relevance_label | RRF 原始分数非 0-1 语义，按位次定标签更直观 |
| 无状态暴露 | BM25 status() 方法 | 排查问题必需 |
| 无 schema 版本 | citation 含 schema_version | 向后兼容，未来扩字段更稳 |
| chunk_index 必须 | 降为推荐，兼容降级方案 B | 现有数据顺序可能不可恢复 |
| 去重唯一键固定 | chunk_index 缺失时用 text hash | 保证降级方案下去重逻辑仍有效 |
