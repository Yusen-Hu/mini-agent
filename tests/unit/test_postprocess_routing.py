from src.services.chat import _postprocess_routing


DOC_LIST = "1. foo.pdf [ID: 1]\n2. bar.pdf [ID: 2]"


def test_clear_all_docs():
    routing = {"agent": "rag_agent", "document_ids": ["1", "2"], "method": "llm"}
    result = _postprocess_routing("帮我总结所有文档", routing, DOC_LIST)
    assert result["document_ids"] == []


def test_clear_no_referential():
    routing = {"agent": "rag_agent", "document_ids": ["173"], "method": "llm"}
    result = _postprocess_routing("ResNet 残差连接怎么设计", routing, DOC_LIST)
    assert result["document_ids"] == []


def test_keep_referential():
    routing = {"agent": "rag_agent", "document_ids": ["45"], "method": "llm"}
    result = _postprocess_routing("它的 mAP 是多少", routing, DOC_LIST)
    assert result["document_ids"] == ["45"]


def test_override_general_chat():
    routing = {"agent": "general_chat", "document_ids": [], "method": "llm"}
    result = _postprocess_routing("介绍一下深度学习", routing, DOC_LIST)
    assert result["agent"] == "rag_agent"


def test_override_llm_fallback():
    routing = {"agent": "general_chat", "document_ids": [], "method": "llm_fallback"}
    result = _postprocess_routing("介绍一下深度学习", routing, DOC_LIST)
    assert result["agent"] == "rag_agent"


def test_no_override_no_docs():
    routing = {"agent": "general_chat", "document_ids": [], "method": "llm"}
    result = _postprocess_routing("介绍一下深度学习", routing, "无文档")
    assert result["agent"] == "general_chat"


def test_no_override_chitchat():
    routing = {"agent": "general_chat", "document_ids": [], "method": "regex_chitchat"}
    result = _postprocess_routing("你好", routing, DOC_LIST)
    assert result["agent"] == "general_chat"


def test_no_override_time():
    routing = {"agent": "general_chat", "document_ids": [], "method": "regex_time"}
    result = _postprocess_routing("现在几点了", routing, DOC_LIST)
    assert result["agent"] == "general_chat"
