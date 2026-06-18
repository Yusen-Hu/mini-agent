"""结构化 JSON 日志配置。"""
import logging
import json
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


class JSONFormatter(logging.Formatter):
    """每条日志输出为一行 JSON，自动携带 session_id / user_id / run_id。"""

    def format(self, record: logging.LogRecord) -> str:
        from config.logging_context import current_session_id, current_user_id, current_run_id

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": current_session_id.get(),
            "user_id": current_user_id.get(),
            "run_id": current_run_id.get(),
        }
        if record.exc_info and record.exc_info != (None, None, None):
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """获取按模块命名的 logger。"""
    return logging.getLogger(name)


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/agent.json.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """初始化全局日志：stdout + 文件轮转。"""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler（reload 场景）
    if root.handlers:
        return

    formatter = JSONFormatter()

    # stdout
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    # 文件轮转
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 静默 uvicorn access log（与 JSON 格式混在一起不统一）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # 静默 httpx INFO 日志（每次 LLM 调用都出一条，对 debug 无价值）
    logging.getLogger("httpx").setLevel(logging.WARNING)
