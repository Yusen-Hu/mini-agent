# 单元测试报告

**日期**：2026-06-14
**环境**：Python 3.12.13 / pytest 9.0.3 / Windows 11
**耗时**：20.36s（热 import；冷 import 首次 ~70s，主因 chat.py 连带加载 LangChain/SQLAlchemy/LiteLLM）

---

## 结果

```
14 passed in 20.36s
```

| # | 文件 | case | 结果 |
|---|------|------|------|
| 1 | test_degrade_citations.py | test_has_marker_and_citations | PASSED |
| 2 | test_degrade_citations.py | test_no_marker_with_citations | PASSED |
| 3 | test_degrade_citations.py | test_has_marker_no_citations | PASSED |
| 4 | test_postprocess_routing.py | test_clear_all_docs | PASSED |
| 5 | test_postprocess_routing.py | test_clear_no_referential | PASSED |
| 6 | test_postprocess_routing.py | test_keep_referential | PASSED |
| 7 | test_postprocess_routing.py | test_override_general_chat | PASSED |
| 8 | test_postprocess_routing.py | test_override_llm_fallback | PASSED |
| 9 | test_postprocess_routing.py | test_no_override_no_docs | PASSED |
| 10 | test_postprocess_routing.py | test_no_override_chitchat | PASSED |
| 11 | test_postprocess_routing.py | test_no_override_time | PASSED |
| 12 | test_supervisor_regex.py | test_regex_list_docs | PASSED |
| 13 | test_supervisor_regex.py | test_regex_chitchat | PASSED |
| 14 | test_supervisor_regex.py | test_regex_time | PASSED |

---

## 覆盖范围

### should_clear_citations（降级 citations 清空）

| case | 输入 | 期望 | 说明 |
|------|------|------|------|
| 有 marker + 有 citations | reply 含"未在知识库中找到相关内容" | True | 降级回答应清空引用 |
| 无 marker + 有 citations | reply 正常回答 | False | 正常回答保留引用 |
| 有 marker + citations=None | reply 含降级标记，无 citations | False | 无引用可清 |

### _postprocess_routing（路由后处理）

| case | 输入 | 期望 | 说明 |
|------|------|------|------|
| 所有文档 | message="帮我总结所有文档"，doc_ids 非空 | doc_ids=[] | 规则 1：全量关键词清空 |
| 无指代词 | message="ResNet 残差连接"，doc_ids=['173'] | doc_ids=[] | 规则 2：无指代切断污染 |
| 有指代词 | message="它的 mAP"，doc_ids=['45'] | doc_ids=['45'] | 规则 2：有指代保留 |
| override llm | agent=general_chat, method=llm, 有文档 | agent=rag_agent | 规则 3：LLM 漏判兜底 |
| override llm_fallback | agent=general_chat, method=llm_fallback, 有文档 | agent=rag_agent | 规则 3：fallback 也兜底 |
| 不 override（无文档） | agent=general_chat, method=llm, doc_list_text="无文档" | agent=general_chat | 规则 3：无文档不兜底 |
| 不 override chitchat | agent=general_chat, method=regex_chitchat | 不变 | 白名单保护 |
| 不 override time | agent=general_chat, method=regex_time | 不变 | 白名单保护 |

### route() regex 快速路径

| case | 输入 | 期望 | 说明 |
|------|------|------|------|
| list_docs | "我有哪些文档" | rag_agent, method=regex_list_docs | 文档列表硬规则 |
| chitchat | "你好" | general_chat, method=regex_chitchat | 闲聊硬规则（需有文档） |
| time | "现在几点" | general_chat, method=regex_time | 时间硬规则 |

---

## 未覆盖（后续可补充）

| 项目 | 说明 |
|------|------|
| method=default_fallback | route() 的 LLM 不可解析分支，需 mock LLM |
| _CLEAR_ALL_PAT 变体 | 只测了"所有文档"，未测"全部"/"每一篇"/"这些文档" |
| regex_list_docs 无文档边界 | chitchat 测了"需有文档"，list_docs 未测无文档场景 |

---

## 注意

测试依赖 `src.services.chat.should_clear_citations` 纯函数。若 chat.py 中该函数被还原为内联条件，测试会在 collection 阶段报 ImportError。落地时需先重新抽取该函数。

---

## 运行方式

```bash
E:\1\python\envs\supermew\python.exe -m pytest tests/unit/ -v
```

不依赖 LLM / Milvus / uvicorn / Docker。
