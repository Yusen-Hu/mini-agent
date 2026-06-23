# Security Policy

## 已知安全限制

本项目为学习/portfolio 用途，以下问题已知但暂未修复：

- CORS 全开（`allow_origins=["*"]`），生产环境需收紧
- 无数据库迁移工具（Alembic），schema 变更需手动 SQL
- 首个注册用户自动获得 admin 权限
- 文件上传未做病毒扫描

## 重要提醒

- **禁止**将默认 docker-compose 凭证暴露公网
- 生产环境**必须**修改 `JWT_SECRET_KEY` 和数据库密码
- 启动时若检测到 `JWT_SECRET_KEY` 使用默认值，服务会输出警告日志

## 报告漏洞

请通过 [GitHub Security Advisories](../../security/advisories/new) 或 Issues 私密报告安全问题。
