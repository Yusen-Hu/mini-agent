"""长文档智能截断。"""
import random as _random

RATIO = {"head": 0.4, "tail": 0.4, "mid": 0.2}
MIN_PER_DOC = 2000  # 多文档均分时单篇最低预算


def smart_truncate(text: str, doc_id: str, budget: int) -> str:
    """
    按预算做加权截取：开头 40% + 结论 40% + 中间抽样 20%。
    固定 seed 保证同一文档每次截取结果一致。纯文本处理，不附加提示语。
    """
    if len(text) <= budget:
        return text

    head_len = int(budget * RATIO["head"])
    tail_len = int(budget * RATIO["tail"])

    # 重叠检测：head + tail 超过文本长度时，各取一半
    if head_len + tail_len >= len(text):
        half = len(text) // 2
        head_len = tail_len = half

    head = text[:head_len]
    tail = text[-tail_len:]

    mid_len = budget - head_len - tail_len
    if mid_len <= 0:
        return head + "\n...\n" + tail

    # 用 doc_id 做 seed，同一文档每次抽样位置一致
    rng = _random.Random(doc_id)
    mid_start = rng.randint(head_len, len(text) - tail_len - mid_len)

    # 对齐到段落边界（找最近的换行符），避免截断中文字符
    newline_pos = text.rfind("\n", max(0, mid_start - 50), min(len(text), mid_start + 50))
    if newline_pos != -1:
        mid_start = newline_pos + 1

    # 确保 mid 不侵入 head 区域
    mid_start = max(mid_start, head_len)

    mid = text[mid_start: mid_start + mid_len]
    return head + "\n...\n" + mid + "\n...\n" + tail
