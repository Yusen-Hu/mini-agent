"""从 Milvus 随机抽取 chunk，用 LLM 自动生成评估问题初稿。

用法:
  python scripts/gen_eval_questions.py --user-id 9 --count 30
  python scripts/gen_eval_questions.py --user-id 9 --count 30 --min-len 150 --exclude-docs 18

输出到终端 + 保存到 scripts/eval_sets/ 目录（版本化落盘）。
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 文本质量过滤 ────────────────────────────────────────────


def is_reference_chunk(text: str) -> bool:
    """判断是否为参考文献/引用列表段落。"""
    lines = text.strip().split('\n')
    if len(lines) < 3:
        return False

    # 参考文献特征行计数
    ref_patterns = [
        r'^\s*\[\d+\]',           # [1] [2] ...
        r'^\s*\d+\.\s+[A-Z]',     # 1. Author...
        r'ISSN',                   # 期刊 ISSN
        r'DOI:',                   # DOI 引用
        r'\. (期刊|季刊|月刊|双月|周刊)',  # 出版频率
    ]
    ref_line_count = 0
    for line in lines:
        if any(re.search(p, line) for p in ref_patterns):
            ref_line_count += 1

    ratio = ref_line_count / len(lines)
    if ratio > 0.4:
        print(f"    [过滤] 参考文献行比例 {ratio:.0%} > 40%", file=sys.stderr)
        return True
    return False


def is_directory_chunk(text: str) -> bool:
    """判断是否为目录/期刊列表/元数据段落。"""
    lines = text.strip().split('\n')
    if len(lines) < 5:
        return False

    # 目录/列表特征：大量行包含 ISSN、ISSN:、季刊/月刊等元数据
    meta_patterns = [
        r'ISSN[:\s]',
        r'(月刊|季刊|双月|周刊|半月刊)',
        r'(ISSN|E-ISSN)',
        r'^\s*\d+\s*$',  # 纯数字行
    ]
    meta_count = 0
    for line in lines:
        if any(re.search(p, line, re.IGNORECASE) for p in meta_patterns):
            meta_count += 1

    ratio = meta_count / len(lines)
    if ratio > 0.3:
        print(f"    [过滤] 元数据行比例 {ratio:.0%} > 30%", file=sys.stderr)
        return True
    return False


def filter_chunks(chunks: list[dict], min_len: int = 100, exclude_docs: set | None = None) -> list[dict]:
    """过滤 chunk，返回通过所有过滤条件的 chunk。"""
    exclude_docs = exclude_docs or set()
    passed = []
    for chunk in chunks:
        doc_id = chunk.get("document_id", "")
        text = chunk.get("text", "")

        if doc_id in exclude_docs:
            print(f"    [过滤] 排除文档 {doc_id}", file=sys.stderr)
            continue
        if len(text) < min_len:
            print(f"    [过滤] 长度 {len(text)} < {min_len}", file=sys.stderr)
            continue
        if is_reference_chunk(text):
            continue
        if is_directory_chunk(text):
            continue

        passed.append(chunk)

    return passed


# ── LLM 调用（带重试） ──────────────────────────────────────

def call_llm_with_retry(messages, max_retries: int = 3):
    """调用 LLM，遇到 429 指数退避重试（带 jitter）。"""
    from src.services.llm import llm

    for attempt in range(max_retries):
        try:
            response = llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                delay = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                print(f"    [429] 第 {attempt+1} 次重试，等待 {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)
            else:
                raise
    return None


# ── 可回答性校验 ────────────────────────────────────────────

CHECK_PROMPT = (
    "判断以下问题是否能从给定的文档片段中直接、明确地回答。\n\n"
    "要求：\n"
    "- 如果文档片段包含回答该问题所需的核心信息，输出 yes\n"
    "- 如果问题过于泛泛（如\"这篇文献研究什么\"），输出 no\n"
    "- 如果答案需要跨多个片段推理，输出 no\n"
    "- 只输出 yes 或 no，不要输出其他内容"
)


def check_answerable(chunk_text: str, question: str) -> bool:
    """快速校验问题是否可从 chunk 中回答。"""
    from langchain_core.messages import SystemMessage, HumanMessage
    result = call_llm_with_retry([
        SystemMessage(content=CHECK_PROMPT),
        HumanMessage(content=f"文档片段：\n{chunk_text[:500]}\n\n问题：{question}"),
    ])
    return result is not None and result.lower().startswith("yes")


# ── 主流程 ──────────────────────────────────────────────────

GENERATE_PROMPT = (
    "你是一个评估集生成器。根据以下文档片段，生成一个用户可能会问的问题。\n\n"
    "要求：\n"
    "- 问题应该能通过检索这篇文档得到回答\n"
    "- 问题要自然，像真实用户会问的（不要太学术化）\n"
    "- 不要问参考文献、ISSN、出版频率等元数据信息\n"
    "- 只输出问题本身，不要输出其他内容\n"
    "- 问题用中文"
)


def generate_questions(user_id: int, count: int = 30, min_len: int = 100,
                       exclude_docs: set | None = None, check: bool = True):
    """从 Milvus 随机抽取 chunk，过滤后调 LLM 生成问题。"""
    from skills.rag.collection import milvus_client, COLLECTION_NAME
    from langchain_core.messages import SystemMessage, HumanMessage

    # 拉取用户的所有 chunk
    all_chunks = milvus_client.query(
        collection_name=COLLECTION_NAME,
        filter=f"user_id == {user_id}",
        output_fields=["text", "source", "document_id", "chunk_index"],
        limit=16384,
    )

    if not all_chunks:
        print(f"用户 {user_id} 没有任何 chunk 数据。")
        return []

    print(f"\n共 {len(all_chunks)} 个 chunk，开始过滤...\n", file=sys.stderr)

    # 过滤
    valid_chunks = filter_chunks(all_chunks, min_len=min_len, exclude_docs=exclude_docs)
    print(f"过滤后剩余 {len(valid_chunks)} 个 chunk\n", file=sys.stderr)

    if not valid_chunks:
        print("过滤后无可用 chunk。")
        return []

    # 抽样（多抽一些，因为可回答性校验会淘汰一批）
    oversample = int(count * 2.5)
    sample_size = min(oversample, len(valid_chunks))
    sampled = random.sample(valid_chunks, sample_size)

    print(f"从 {len(valid_chunks)} 个有效 chunk 中抽取 {sample_size} 个，开始生成...\n")

    results = []
    for i, chunk in enumerate(sampled, 1):
        if len(results) >= count:
            break

        text = chunk["text"][:500]
        source = chunk.get("source", "unknown")
        doc_id = chunk.get("document_id", "")
        chunk_idx = chunk.get("chunk_index", 0)

        # 生成问题
        question = call_llm_with_retry([
            SystemMessage(content=GENERATE_PROMPT),
            HumanMessage(content=f"文档片段：\n{text}"),
        ])

        if not question:
            print(f"  [{i:2d}] ✗ 生成失败", file=sys.stderr)
            continue

        # 可回答性校验
        answerable = True
        if check:
            answerable = check_answerable(text, question)
            time.sleep(0.5)  # 校验间隔

        status = "✓" if answerable else "✗"
        check_str = "" if not check else ("pass" if answerable else "fail")
        print(f"  [{i:2d}] {status} {check_str}  {question[:50]}", file=sys.stderr)

        if not answerable:
            continue

        entry = {
            "question": question,
            "source_doc": source,
            "document_id": doc_id,
            "chunk_index": chunk_idx,
            "chunk_text_preview": text[:150],
        }
        results.append(entry)

        # 节流
        time.sleep(1)

    print(f"\n生成完成：{len(results)}/{count} 题通过校验\n", file=sys.stderr)
    return results


def save_eval_set(results: list[dict], output_dir: str):
    """版本化落盘保存评估集。"""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filepath = out_path / f"eval_set_{timestamp}.json"

    output = {
        "generated_at": datetime.now().isoformat(),
        "total": len(results),
        "questions": results,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"评估集已保存: {filepath}")
    return filepath


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自动生成 RAG 评估问题")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--count", type=int, default=30, help="目标问题数量")
    parser.add_argument("--min-len", type=int, default=100, help="chunk 最小字符数")
    parser.add_argument("--exclude-docs", type=str, default="", help="排除的文档 ID，逗号分隔")
    parser.add_argument("--output-dir", type=str, default="scripts/eval_sets", help="输出目录")
    parser.add_argument("--no-check", action="store_true", help="跳过可回答性校验")
    args = parser.parse_args()

    exclude = set(args.exclude_docs.split(",")) if args.exclude_docs.strip() else set()

    results = generate_questions(
        user_id=args.user_id,
        count=args.count,
        min_len=args.min_len,
        exclude_docs=exclude,
        check=not args.no_check,
    )

    if results:
        save_eval_set(results, args.output_dir)
        # 同时打印到 stdout 方便查看
        for i, r in enumerate(results, 1):
            print(f"\n=== 问题 #{i} ===")
            print(f"来源文档: {r['source_doc']}")
            print(f"document_id: {r['document_id']}")
            print(f"chunk_index: {r['chunk_index']}")
            print(f"chunk 摘要: {r['chunk_text_preview']}...")
            print(f"生成问题: {r['question']}")
