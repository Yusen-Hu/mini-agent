# Phase 3B：评估集 + Rerank — 详细实施计划

## 背景

当前 Hybrid Retrieval（Dense + BM25 + RRF）已上线运行，但没有量化数据证明效果好坏。Phase 3B 先建立评估基准，用数据驱动后续优化决策（是否加 Rerank、是否调参数）。

## 核心指标

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| **Hit Rate@5** | top-5 结果里是否包含正确文档 | 命中数 / 总题数 |
| **MRR@5** | 正确文档排第几（倒数排名均值） | sum(1/rank_i) / 总题数 |

评估粒度：文档级（问"这个答案在哪篇文档"），不评 chunk 级。

---

## 优先级顺序

1. BM25 加 jieba 分词 — 让 BM25 真正起效
2. 评估脚本 — 边界题逻辑、document_id 辅助函数、user_id 参数化、--compare 对比
3. 评估集生成 — 方案 B 自动生成 30 题 → 人工筛选 20 题 + 补 4 道边界题
4. 跑基线 → 根据数字决定后续

---

## Step 0：BM25 加 jieba 分词

**为什么先做：** BM25 当前用 `text.lower().split()` 空格分词，中文文档基本无效，Hybrid Retrieval 里 BM25 这条路几乎没有贡献。先修再跑基线，否则数据没有参考价值。

**改什么：** `skills/rag/bm25_index.py` 的分词步骤，两处：

```python
# 之前
query_tokens = query.lower().split()

# 之后
import jieba
query_tokens = list(jieba.cut(query.lower()))
```

`build_index()` 里对每个 chunk 的分词也同步改。加一个 import，改两行。

**验证：** 重启 uvicorn，发中文问题搜索，确认 BM25 能返回结果。

---

## Step 1：评估脚本 — `scripts/eval_retrieval.py`

### 1.1 评估集（嵌入脚本内）

`EVAL_SET` 列表，20 道正常题 + 4 道边界题，共 24 题。

正常题格式：
```python
{
    "question": "PoF 模型在电子装备故障分析中的作用是什么？",
    "expected_doc_ids": ["<uuid>"],
    "category": "factual",
}
```

边界题格式：
```python
{
    "question": "世界杯足球赛的规则是什么？",
    "expected_doc_ids": [],  # 空列表，知识库里没有
    "category": "negative",
}
```

**4 种 category 分布：**

| category | 数量 | 说明 |
|----------|------|------|
| factual | 8 题 | 单文档事实查询 |
| cross_doc | 4 题 | 跨文档对比 |
| vague | 4 题 | 模糊/概括性查询 |
| negative | 4 题 | 知识库无对应内容的边界题 |

### 1.2 边界题验证逻辑

边界题不用阈值自动判定。分两轮跑：

**第一轮：正常题评估**
- 只跑 20 道正常题
- 输出 Hit Rate@5、MRR@5
- 每题打印 top-1 score

**第二轮：边界题评估（需要人工定阈值）**
- 你看第一轮正常题的 top-1 score 分布，确定正常题 score 的下限
- 跑 4 道边界题，打印 top-1 score
- 两个区间之间的 gap 就是阈值依据
- 脚本加 `--negative-threshold <float>` 参数，传入观察到的阈值
- 边界题 top-1 score < 阈值 → 正确（系统判断"找不到"）
- 输出 boundary false positive rate

最终报告合并两轮数据。

### 1.3 命令行参数

```bash
# 列出文档映射（方便填 expected_doc_ids）
python scripts/eval_retrieval.py --user-id 9 --list-docs

# 跑正常题基线
python scripts/eval_retrieval.py --user-id 9

# 跑边界题（需先传入阈值）
python scripts/eval_retrieval.py --user-id 9 --only-negative --negative-threshold 0.003

# 对比上次结果
python scripts/eval_retrieval.py --user-id 9 --compare
```

用 `argparse` 处理：`--user-id`（必填）、`--list-docs`（开关）、`--only-negative`（只跑边界题）、`--negative-threshold`（浮点数）、`--compare`（与上次结果对比）。

### 1.4 --list-docs 输出格式

```
=== 文档列表 (user_id=9) ===
  1. 3-基于PoF模型和FTA的电子装备故障模式分析_张宇.pdf  →  uuid-xxxx-xxxx
  2. 8-航空电子设备PHM关键技术研究综述_李根.pdf          →  uuid-yyyy-yyyy
  ...
```

### 1.5 --compare 输出格式

读取 `scripts/eval_results/` 下最新一次结果文件，与本次对比：

```
=== 评估对比 ===
            上次        本次        变化
Hit Rate:   0.75        0.85        +0.10
MRR:        0.63        0.72        +0.09

分组变化:
  factual:      HR 0.88 → 0.90 (+0.02)
  cross_doc:    HR 0.75 → 0.80 (+0.05)
  ...
```

### 1.6 结果保存

每次运行自动保存到 `scripts/eval_results/`，文件名含时间戳和标签：

```
scripts/eval_results/2026-05-19_14-30_baseline.json
scripts/eval_results/2026-05-19_15-00_with_jieba.json
scripts/eval_results/2026-05-19_16-00_with_rerank.json
```

JSON 格式：每题的 query、expected、actual_top5（含 document_id、source、score）、hit、rank，以及汇总指标。

---

## Step 2：评估集生成

### 方案：B 自动生成 → 人工筛选

写辅助脚本 `scripts/gen_eval_questions.py`：
1. 从 Milvus 随机抽取 30 个 chunk
2. 对每个 chunk 调 LLM 生成一个问题
3. 输出格式（每题一组）：

```
=== 问题 #1 ===
来源文档: 3-基于PoF模型和FTA的电子装备故障模式分析_张宇.pdf
document_id: uuid-xxxx-xxxx
chunk 摘要: 本文研究电子装备故障模式分析方法...
生成问题: PoF 模型在电子装备故障分析中的作用是什么？
```

用户人工筛选 20 道，确认 document_id 正确，复制到 `eval_retrieval.py` 的 `EVAL_SET` 里。再手动补 4 道边界题（问知识库里没有的内容，如体育、娱乐等）。

---

## Step 3：跑基线

```bash
# Step 0 已加 jieba，此时 BM25 已正常工作
python scripts/eval_retrieval.py --user-id 9
```

拿到 Hit Rate@5、MRR@5，以及每题 top-1 score。根据 score 分布定边界题阈值。

---

## Step 4：根据数字决策

| Hit Rate@5 | 下一步 |
|-----------|--------|
| > 0.85 | 质量够用，Phase 3B 结束 |
| 0.70 - 0.85 | 分析失败用例，优先调 RRF 参数（alpha、k），看是否改善 |
| < 0.70 | 加 Cross-Encoder Rerank |

### 如果需要 Rerank

**方案：** 在 RRF 融合后、取 top-5 前，插入 Cross-Encoder 重排：

```
Dense top-20 + BM25 top-20 → RRF 融合 top-20 → Cross-Encoder 重排 → 取 top-5
```

**候选模型：** `BAAI/bge-reranker-v2-m3`（568M，多语言，中文能力强）

**插入位置：** `skills/rag/retrieval.py` 的 `search_documents()` 函数

**延迟预估：** CPU 上对 20 个候选重排约 200-500ms

**验证：** 跑 `--compare` 对比 Rerank 前后指标变化

### 如果不需要 Rerank

检查其他低成本优化：
- RRF 参数调优（alpha、k 值）
- FINAL_TOP_K 从 5 调到更大值

---

## 文件变更总览

| 文件 | 操作 | 时机 |
|------|------|------|
| `skills/rag/bm25_index.py` | 修改 — jieba 分词 | Step 0 |
| `scripts/eval_retrieval.py` | 新建 — 评估脚本 | Step 1 |
| `scripts/gen_eval_questions.py` | 新建 — 题目自动生成 | Step 2 |
| `scripts/eval_results/` | 新建目录 | Step 1 |
| `skills/rag/retrieval.py` | 修改 — 加 Rerank | 仅 Step 4 确认需要时 |
