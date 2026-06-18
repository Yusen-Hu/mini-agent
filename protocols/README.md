# Agent 通信协议

## 设计原则
- 消息格式标准化（Pydantic models）
- 路由规则配置化（YAML）
- Agent 契约文档化（contract.md）

## 目录说明
- `message_schema.py` — Agent 消息标准格式
- `agent_contracts/` — 各 Agent 输入/输出契约
- `routing_rules.yml` — Supervisor 路由规则
- `handoff_templates/` — Agent 交接 payload 模板
