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
    """A cached LangChain ``CallbackHandler`` bound to the Langfuse singleton.

    Used where no per-turn trace id is needed (e.g. ``aget_thread_state``'s
    read-only config). Turn execution uses ``build_turn_handler`` instead —
    see there for why.
    """

    if _client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:  # noqa: BLE001
        logger.exception("Langfuse callback handler unavailable; tracing disabled")
        return None


def turn_trace_id(conversation_id: str, turn_index: int) -> str | None:
    """The deterministic Langfuse trace id for one turn of one thread.

    Seeded from ``(conversation_id, turn_index)`` — same inputs always
    produce the same id, so nothing needs to be stored to look it up again
    later (e.g. from ``record_feedback``, run well after the turn finished).
    Returns ``None`` when Langfuse isn't configured.
    """

    client = _client()
    if client is None:
        return None
    try:
        return client.create_trace_id(seed=f"{conversation_id}:{turn_index}")
    except Exception:  # noqa: BLE001
        logger.exception("Langfuse trace id derivation failed")
        return None


def build_turn_handler(
    conversation_id: str, turn_index: int
) -> tuple[Any | None, str | None]:
    """A fresh ``CallbackHandler`` pinned to this turn's deterministic trace
    id, plus that id. A fresh instance is required (not the cached singleton
    from ``get_langfuse_handler``) because Langfuse's v3 SDK only lets you
    pin a trace id at handler-construction time, via ``trace_context``.
    Returns ``(None, None)`` if Langfuse isn't configured or setup fails —
    tracing is never load-bearing for a turn.
    """

    client = _client()
    if client is None:
        return None, None
    trace_id = turn_trace_id(conversation_id, turn_index)
    if trace_id is None:
        return None, None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(trace_context={"trace_id": trace_id}), trace_id
    except Exception:  # noqa: BLE001
        logger.exception("Langfuse per-turn handler unavailable; tracing disabled")
        return None, None


def record_feedback(*, conversation_id: str, turn_index: int, comment: str) -> bool:
    """Attach free-text user feedback to one turn's Langfuse trace — every
    node, prompt and LLM call inside that turn is a span under it, so this
    is "feedback on exactly this state of the system," browsable/filterable
    in Langfuse next to the trace itself. Returns whether it was recorded;
    the caller treats a ``False`` as a soft failure, never breaking the UI.
    """

    client = _client()
    if client is None:
        return False
    trace_id = turn_trace_id(conversation_id, turn_index)
    if trace_id is None:
        return False
    try:
        client.create_score(
            name="user_feedback",
            value=comment,
            data_type="TEXT",
            trace_id=trace_id,
            comment=comment,
        )
        _safe_flush()
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "Langfuse feedback score failed for %s turn %s", conversation_id, turn_index
        )
        return False


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
