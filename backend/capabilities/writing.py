"""Generative primitives for Signal.

Each function is one focused LLM call. Streaming functions ``yield`` plain string
deltas; the graph nodes decide what to do with them (push artifact snapshots,
forward reply tokens). Capabilities know nothing about LangGraph streaming.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm import get_chat_model
from backend.prompts import (
    BRAINSTORM_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CRITIQUE_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
    REVISE_SYSTEM_PROMPT,
    writer_system_prompt,
)
from backend.signal_models import Critique, GroundingNotes


def _text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    return content if isinstance(content, str) else ""


def _brief_json(brief: dict[str, Any]) -> str:
    return json.dumps(brief, ensure_ascii=False, indent=2)


# --- brainstorm -----------------------------------------------------------------

def brainstorm(brief: dict[str, Any]) -> dict[str, Any]:
    """Return {"angles": [...], "outline": [...], "open_questions": [...]}."""

    model = get_chat_model(tags=["signal:brainstorm"])
    response = model.invoke(
        [
            SystemMessage(content=BRAINSTORM_SYSTEM_PROMPT),
            HumanMessage(content=_brief_json(brief)),
        ]
    )
    raw = _text(response).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to treating each non-empty line as an angle.
        data = {"angles": [ln.strip("-* ") for ln in raw.splitlines() if ln.strip()]}
    return {
        "angles": [a for a in data.get("angles", []) if isinstance(a, str)],
        "outline": [o for o in data.get("outline", []) if isinstance(o, str)],
        "open_questions": [q for q in data.get("open_questions", []) if isinstance(q, str)],
    }


# --- write / revise (streaming) ----------------------------------------------

def write_content(content_format: str, brief: dict[str, Any]) -> Iterator[str]:
    """Stream a fresh piece token-by-token."""

    model = get_chat_model(streaming=True, tags=["signal:write"])
    messages = [
        SystemMessage(content=writer_system_prompt(content_format)),
        HumanMessage(content=_brief_json(brief)),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece


def revise_content(
    content_format: str,
    brief: dict[str, Any],
    current_body: str,
    instruction: str,
) -> Iterator[str]:
    """Stream a revised piece token-by-token."""

    model = get_chat_model(streaming=True, tags=["signal:write"])
    payload = {
        "format": content_format,
        "instruction": instruction,
        "current_draft": current_body,
        "context": brief,
    }
    messages = [
        SystemMessage(content=REVISE_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece


# --- critique -----------------------------------------------------------------

def critique(content_format: str, brief: dict[str, Any], body: str) -> Critique:
    model = get_chat_model(tags=["signal:critique"])
    try:
        structured = model.with_structured_output(Critique)
        payload = {"format": content_format, "context": brief, "draft": body}
        result = structured.invoke(
            [
                SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
            ]
        )
        if isinstance(result, Critique):
            return result
        if isinstance(result, dict):
            return Critique.model_validate(result)
    except Exception:
        pass
    return Critique(summary="", points=[])


# --- grounding notes --------------------------------------------------------

def grounding_notes(
    body: str,
    sources: list[str],
    product_mode: str = "existing",
) -> list[str]:
    """Cheap, non-blocking pass: claims not backed by `sources`."""

    if not body.strip():
        return []
    model = get_chat_model(temperature=0.0, tags=["signal:grounding"])
    payload = {
        "product_mode": product_mode,
        "sources": sources,
        "draft": body,
    }
    try:
        structured = model.with_structured_output(GroundingNotes)
        result = structured.invoke(
            [
                SystemMessage(content=GROUNDING_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
            ]
        )
        if isinstance(result, GroundingNotes):
            return result.items[:5]
        if isinstance(result, dict):
            return [str(item) for item in result.get("items", [])][:5]
    except Exception:
        pass
    return []


# --- chat reply (streaming) -------------------------------------------------

def chat_reply(
    turns: list[dict[str, Any]],
    artifact: dict[str, Any],
    reply_gist: str | None,
) -> Iterator[str]:
    """Stream a short conversational reply token-by-token."""

    model = get_chat_model(streaming=True, tags=["signal:reply"])
    payload = {
        "recent_turns": turns,
        "artifact": artifact,
        "reply_should_convey": reply_gist or "a helpful, forward-moving reply",
    }
    messages = [
        SystemMessage(content=CHAT_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece
