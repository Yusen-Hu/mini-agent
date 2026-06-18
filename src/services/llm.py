from langchain_litellm import ChatLiteLLM
from config.settings import settings

_model = settings.LLM_MODEL.lower()
extra_kwargs = {}
if any(k in _model for k in ("kimi", "moonshot")):
    extra_kwargs["model_kwargs"] = {"extra_body": {"thinking": {"type": "disabled"}}}

llm = ChatLiteLLM(
    model=settings.LLM_MODEL,
    api_key=settings.LLM_API_KEY,
    api_base=settings.LLM_BASE_URL,
    temperature=None,
    **extra_kwargs,
)
