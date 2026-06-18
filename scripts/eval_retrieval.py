"""RAG 检索质量评估脚本。

用法:
  python scripts/eval_retrieval.py --user-id 16 --list-docs          # 列出文档映射
  python scripts/eval_retrieval.py --user-id 16                      # 跑正常题基线
  python scripts/eval_retrieval.py --user-id 16 --only-negative --negative-threshold 0.003  # 跑边界题
  python scripts/eval_retrieval.py --user-id 16 --compare            # 对比上次结果
  python scripts/eval_retrieval.py --user-id 16 --eval-set scripts/eval_sets/eval_set_2026-06-15.json  # 指定评测集
"""

import argparse
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows GBK 终端兼容
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 把项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402

# ── 评估集加载 ──────────────────────────────────────────────

def load_eval_set(path: str | None = None) -> list[dict]:
    """从 JSON 文件加载评估集。

    如果指定 path，加载该文件；否则自动选 scripts/eval_sets/ 下最新的。
    只加载 eval_type=retrieval 的题目。
    """
    eval_dir = Path(__file__).resolve().parent / "eval_sets"

    if path:
        filepath = Path(path)
    else:
        files = sorted(eval_dir.glob("eval_set_*.json"), reverse=True)
        if not files:
            print("未找到评估集文件。请在 scripts/eval_sets/ 下放置 eval_set_*.json。")
            return []
        filepath = files[0]

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    questions = [q for q in data.get("questions", []) if q.get("eval_type") == "retrieval"]
    print(f"加载评估集: {filepath.name}  ({len(questions)} 道检索题)")
    return questions


# ── 辅助函数 ────────────────────────────────────────────────

def list_documents(user_id: int):
    """列出指定用户的所有文档，打印 filename → document_id 映射。"""
    from config.database import SessionLocal
    from src.types.document import Document

    with SessionLocal() as db:
        docs = (
            db.query(Document)
            .filter(Document.user_id == user_id)
            .order_by(Document.created_at.asc())
            .all()
        )

    if not docs:
        print(f"用户 {user_id} 没有上传任何文档。")
        return

    print(f"\n=== 文档列表 (user_id={user_id}) ===")
    for i, doc in enumerate(docs, 1):
        status = {"ready": "已就绪", "processing": "处理中", "error": "失败"}.get(doc.status, doc.status)
        print(f"  {i:2d}. {doc.filename:<60s} → {doc.id}  ({status})")
    print()


def get_result_files() -> list[Path]:
    """获取评估结果目录下的所有 JSON 文件，按时间排序。"""
    result_dir = Path(__file__).resolve().parent / "eval_results"
    if not result_dir.exists():
        return []
    files = sorted(result_dir.glob("*.json"))
    return files


def compute_metrics(results: list[dict], negative_threshold: float | None = None) -> dict:
    """计算 Hit Rate 和 MRR。

    results: [{"question", "expected_doc_ids", "category", "topK_doc_ids", "top1_score", "hit", "rank"}, ...]
    """
    normal = [r for r in results if r["category"] != "negative"]
    negative = [r for r in results if r["category"] == "negative"]

    # 正常题
    normal_hits = sum(1 for r in normal if r["hit"])
    normal_mrr_sum = sum(1.0 / r["rank"] if r["rank"] > 0 else 0.0 for r in normal)
    n = len(normal) if normal else 1

    # 边界题
    if negative and negative_threshold is not None:
        # 边界题：top-1 score < 阈值 → 正确（系统判断"找不到"）
        for r in negative:
            r["hit"] = r["top1_score"] < negative_threshold
        neg_correct = sum(1 for r in negative if r["hit"])
        neg_fpr = 1.0 - neg_correct / len(negative)  # false positive rate
    else:
        neg_correct = 0
        neg_fpr = None

    metrics = {
        "total_normal": len(normal),
        "total_negative": len(negative),
        "hit_rate": round(normal_hits / n, 4),
        "mrr": round(normal_mrr_sum / n, 4),
        "normal_hits": normal_hits,
        "negative_correct": neg_correct,
        "negative_fpr": round(neg_fpr, 4) if neg_fpr is not None else None,
    }

    # 分组统计
    categories = set(r["category"] for r in normal)
    for cat in sorted(categories):
        cat_results = [r for r in normal if r["category"] == cat]
        cat_n = len(cat_results)
        cat_hits = sum(1 for r in cat_results if r["hit"])
        cat_mrr = sum(1.0 / r["rank"] if r["rank"] > 0 else 0.0 for r in cat_results)
        metrics[f"group_{cat}"] = {
            "count": cat_n,
            "hit_rate": round(cat_hits / cat_n, 4),
            "mrr": round(cat_mrr / cat_n, 4),
        }

    return metrics


def print_metrics(metrics: dict, label: str = "", top_k: int = 8):
    """打印评估指标。"""
    prefix = f" [{label}]" if label else ""
    print(f"\n=== 评估结果{prefix} ===")
    print(f"总题数: {metrics['total_normal']} 正常 + {metrics['total_negative']} 边界")
    print(f"Hit Rate@{top_k}: {metrics['hit_rate']:.2%} ({metrics['normal_hits']}/{metrics['total_normal']})")
    print(f"MRR@{top_k}:      {metrics['mrr']:.4f}")

    if metrics.get("negative_fpr") is not None:
        print(f"边界题 False Positive Rate: {metrics['negative_fpr']:.2%} ({metrics['total_negative'] - metrics['negative_correct']}/{metrics['total_negative']} 正确)")

    # 分组
    group_keys = [k for k in metrics if k.startswith("group_")]
    if group_keys:
        print(f"\n分组:")
        for k in group_keys:
            cat = k.replace("group_", "")
            g = metrics[k]
            print(f"  {cat:<12s}  HR={g['hit_rate']:.2%}  MRR={g['mrr']:.4f}  ({g['count']}题)")


def print_comparison(old_metrics: dict, new_metrics: dict, top_k: int = 8):
    """打印两次评估的对比。"""
    print(f"\n=== 评估对比 (HR@{top_k}) ===")
    print(f"{'':20s} {'上次':>10s} {'本次':>10s} {'变化':>10s}")
    for key, label in [("hit_rate", "Hit Rate"), ("mrr", "MRR")]:
        old_v = old_metrics.get(key, 0)
        new_v = new_metrics.get(key, 0)
        delta = new_v - old_v
        sign = "+" if delta >= 0 else ""
        if key == "hit_rate":
            print(f"{label:<20s} {old_v:>9.2%} {new_v:>9.2%} {sign}{delta:.2%}")
        else:
            print(f"{label:<20s} {old_v:>10.4f} {new_v:>10.4f} {sign}{delta:.4f}")

    # 分组对比
    group_keys = [k for k in new_metrics if k.startswith("group_")]
    if group_keys:
        print(f"\n分组变化:")
        for k in group_keys:
            cat = k.replace("group_", "")
            old_g = old_metrics.get(k, {})
            new_g = new_metrics[k]
            old_hr = old_g.get("hit_rate", 0)
            new_hr = new_g["hit_rate"]
            delta = new_hr - old_hr
            sign = "+" if delta >= 0 else ""
            print(f"  {cat:<12s}  HR {old_hr:.2%} → {new_hr:.2%} ({sign}{delta:.2%})")


# ── 主流程 ──────────────────────────────────────────────────

def run_evaluation(user_id: int, only_negative: bool, negative_threshold: float | None,
                   eval_set_path: str | None = None):
    """跑评估。"""
    from skills.rag.retrieval import search_documents
    from skills.rag.bm25_index import bm25_index
    from skills.rag.collection import milvus_client, COLLECTION_NAME

    # 确保 BM25 索引已构建（独立脚本运行时没有 uvicorn 启动流程）
    if not bm25_index.status().get("ready"):
        print("构建 BM25 索引...")
        bm25_index.build_index(milvus_client, COLLECTION_NAME)
        print(f"BM25 索引就绪: {bm25_index.status()['chunk_count']} chunks")

    eval_set = load_eval_set(eval_set_path)
    if not eval_set:
        return

    # 过滤题目
    if only_negative:
        questions = [q for q in eval_set if q["category"] == "negative"]
    else:
        questions = [q for q in eval_set if q["category"] != "negative"]

    if not questions:
        print("没有可评估的题目。")
        return

    print(f"\n开始评估：{len(questions)} 题 (user_id={user_id})\n")

    all_results = []
    for i, q in enumerate(questions, 1):
        query = q["question"]
        expected = set(q["expected_doc_ids"])
        category = q["category"]

        # 检索
        try:
            hits = search_documents(query, top_k=settings.FINAL_TOP_K, user_id=user_id)
        except Exception as e:
            print(f"  [{i:2d}] 检索失败: {query[:40]}... → {e}")
            continue

        topK_doc_ids = [r["document_id"] for r in hits]
        top1_score = hits[0]["score"] if hits else 0.0

        # 判断命中
        hit = False
        rank = 0
        if category == "negative":
            # 边界题：暂不自动判定，等阈值
            hit = False
            rank = 0
        else:
            # 正常题：expected 中任一在 top-K 中出现
            for j, doc_id in enumerate(topK_doc_ids):
                if doc_id in expected:
                    hit = True
                    rank = j + 1
                    break

        result = {
            "question": query,
            "expected_doc_ids": list(expected),
            "category": category,
            "topK_doc_ids": topK_doc_ids,
            "top1_score": round(top1_score, 6),
            "hit": hit,
            "rank": rank,
        }
        all_results.append(result)

        # 实时打印
        status = "✓" if hit else "✗"
        if category == "negative":
            status = "?"
        rank_str = f"rank={rank}" if rank > 0 else "miss"
        print(f"  [{i:2d}] {status} {rank_str}  score={top1_score:.6f}  {query[:60]}")

    if not all_results:
        print("没有结果。")
        return

    # 计算指标
    metrics = compute_metrics(all_results, negative_threshold)
    print_metrics(metrics, top_k=settings.FINAL_TOP_K)

    # 打印失败用例
    if not only_negative:
        misses = [r for r in all_results if not r["hit"] and r["category"] != "negative"]
        if misses:
            print(f"\n失败用例 ({len(misses)}):")
            for r in misses:
                print(f"  Q: {r['question'][:60]}")
                print(f"    期望: {r['expected_doc_ids']}")
                print(f"    实际 top-3: {r['topK_doc_ids'][:3]}")

    # 保存结果
    result_dir = Path(__file__).resolve().parent / "eval_results"
    result_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    tag = "negative" if only_negative else "baseline"
    filepath = result_dir / f"{timestamp}_{tag}.json"
    output = {
        "timestamp": datetime.now().isoformat(),
        "user_id": user_id,
        "settings": {
            "DENSE_TOP_K": settings.DENSE_TOP_K,
            "BM25_TOP_K": settings.BM25_TOP_K,
            "FINAL_TOP_K": settings.FINAL_TOP_K,
            "RRF_K": settings.RRF_K,
            "RRF_ALPHA": settings.RRF_ALPHA,
            "negative_threshold": negative_threshold,
        },
        "results": all_results,
        "metrics": metrics,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {filepath}")

    return metrics


def main():
    parser = argparse.ArgumentParser(description="RAG 检索质量评估")
    parser.add_argument("--user-id", type=int, required=True, help="评估用的用户 ID")
    parser.add_argument("--list-docs", action="store_true", help="列出文档映射")
    parser.add_argument("--only-negative", action="store_true", help="只跑边界题")
    parser.add_argument("--negative-threshold", type=float, default=None, help="边界题判定阈值")
    parser.add_argument("--compare", action="store_true", help="与上次结果对比")
    parser.add_argument("--eval-set", type=str, default=None, help="指定评估集 JSON 路径")
    args = parser.parse_args()

    if args.list_docs:
        list_documents(args.user_id)
        return

    # 跑评估
    new_metrics = run_evaluation(args.user_id, args.only_negative, args.negative_threshold, args.eval_set)

    # 对比
    if args.compare and new_metrics:
        files = get_result_files()
        # 找最近一次不同标签的结果
        if len(files) >= 2:
            with open(files[-2], encoding="utf-8") as f:
                old_data = json.load(f)
            print_comparison(old_data["metrics"], new_metrics, settings.FINAL_TOP_K)
        else:
            print("\n没有历史结果可供对比。")


if __name__ == "__main__":
    main()
