from src.agents.supervisor import route


DOC_LIST = "1. foo.pdf [ID: 1]"


def test_regex_list_docs():
    result = route("我有哪些文档", doc_list_text=DOC_LIST)
    assert result["agent"] == "rag_agent"
    assert result["method"] == "regex_list_docs"


def test_regex_chitchat():
    result = route("你好", doc_list_text=DOC_LIST)
    assert result["agent"] == "general_chat"
    assert result["method"] == "regex_chitchat"


def test_regex_time():
    result = route("现在几点", doc_list_text=DOC_LIST)
    assert result["agent"] == "general_chat"
    assert result["method"] == "regex_time"
