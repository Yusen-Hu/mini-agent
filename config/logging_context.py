"""请求级 contextvars — 由 chat 入口设置，各模块读取。"""
import contextvars

current_session_id: contextvars.ContextVar = contextvars.ContextVar("session_id", default=None)
current_user_id: contextvars.ContextVar = contextvars.ContextVar("user_id", default=0)
current_run_id: contextvars.ContextVar = contextvars.ContextVar("run_id", default=None)
