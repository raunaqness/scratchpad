"""Langfuse tracing wiring.

One Langfuse trace per turn. The LangChain callback handler is attached to the
LangGraph run config, so every nested node and LLM call becomes a span
automatically. Traces are grouped into a **session** by ``thread_id`` and
attributed to a **user** by the Google ``sub`` the Next.js proxy injects.

Everything here is best-effort: a missing dependency, bad keys, or a Langfuse
outage must never raise into a turn. Callers get ``None`` and carry on.
"""

from __future__ import annotations

import atexit
import logging
from functools import lru_cache
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)

_MASK = "[redacted]"


def _mask(data: Any) -> Any:  # pragma: no cover - passthrough shape varies
    """Replace captured input/output text when content tracing is disabled."""

    if isinstance(data, str):
        return _MASK
    if isinstance(data, dict):
        return {k: _mask(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return type(data)(_mask(v) for v in data)
    return data


@lru_cache(maxsize=1)
def _client() -> Any | None:
    if not settings.langfuse_ready:
        return None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            mask=None if settings.langfuse_trace_content else _mask,
        )
        atexit.register(_safe_flush)
        logger.info("Langfuse tracing enabled (host=%s)", settings.langfuse_host)
        return client
    except Exception:  # noqa: BLE001 - tracing is never load-bearing
        logger.exception("Langfuse client init failed; tracing disabled")
        return None


@lru_cache(maxsize=1)
def get_langfuse_handler() -> Any | None:
    """A cached LangChain ``CallbackHandler`` bound to the Langfuse singleton."""

    if _client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:  # noqa: BLE001
        logger.exception("Langfuse callback handler unavailable; tracing disabled")
        return None


def trace_metadata(
    *, conversation_id: str, user_id: str, prompt_version: str
) -> dict[str, Any]:
    """Metadata keys the Langfuse LangChain handler reads off the run config."""

    return {
        "langfuse_session_id": conversation_id,
        "langfuse_user_id": user_id,
        "langfuse_tags": ["scratchpad", prompt_version],
    }


def _safe_flush() -> None:
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse flush failed", exc_info=True)


def flush() -> None:
    """Force-send buffered events. Call at the end of short-lived (CLI) runs."""

    _safe_flush()
