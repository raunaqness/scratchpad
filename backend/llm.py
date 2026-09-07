"""Single OpenRouter chat-model factory.

Every LLM call in the backend goes through :func:`get_chat_model`. There is no
provider fallback and no direct OpenAI/Anthropic path — swapping models is an
env-var change (``OPENROUTER_MODEL``), not a code change.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from backend.config import settings


def _require_credentials() -> None:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to run Signal")
    if not settings.openrouter_model:
        raise RuntimeError("OPENROUTER_MODEL is required to run Signal")


@lru_cache(maxsize=8)
def _cached_model(
    *,
    streaming: bool,
    temperature: float,
    tags: tuple[str, ...],
) -> ChatOpenAI:
    _require_credentials()
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=temperature,
        max_tokens=settings.openrouter_max_tokens,
        timeout=settings.openrouter_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
        streaming=streaming,
        tags=list(tags),
    )


def get_chat_model(
    *,
    streaming: bool = False,
    temperature: float | None = None,
    tags: list[str] | None = None,
) -> ChatOpenAI:
    """Return a configured chat model.

    ``tags`` land on every run and are how the AG-UI layer tells "stream these
    tokens to the user" (``signal:reply``) apart from internal calls.
    """

    return _cached_model(
        streaming=streaming,
        temperature=(
            settings.openrouter_temperature if temperature is None else temperature
        ),
        tags=tuple(tags or ()),
    )
