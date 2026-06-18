from src.services.chat import should_clear_citations, _DEGRADE_MARKER


def test_has_marker_and_citations():
    reply = f"以下是通用知识。{_DEGRADE_MARKER}，请参考其他资料。"
    citations = [{"source": "test.pdf", "snippet": "..."}]
    assert should_clear_citations(reply, citations) is True


def test_no_marker_with_citations():
    reply = "ResNet 的残差连接通过跳跃连接实现 [1]。"
    citations = [{"source": "test.pdf", "snippet": "..."}]
    assert should_clear_citations(reply, citations) is False


def test_has_marker_no_citations():
    reply = f"以下是通用知识。{_DEGRADE_MARKER}，请参考其他资料。"
    assert should_clear_citations(reply, None) is False
