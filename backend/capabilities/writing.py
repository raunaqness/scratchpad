"""Generative primitives for Scratchpad's own operations.

Each function is one focused LLM call. Streaming functions ``yield`` plain string
deltas; the graph nodes decide what to do with them. These know nothing about
LangGraph streaming. Skills (which build derived artifacts) live in
``backend/capabilities/skills.py``.
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
    EXPAND_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
    TIGHTEN_SYSTEM_PROMPT,
)
from backend.signal_models import Critique, GroundingNotes


def _text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    return content if isinstance(content, str) else ""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _bullets(label: str, items: Any) -> str:
    values = [str(v).strip() for v in (items or []) if str(v).strip()]
    if not values:
        return ""
    return label + "\n" + "\n".join(f"- {v}" for v in values)


def render_scratchpad(scratchpad: dict[str, Any]) -> str:
    """A plain, labelled text view of the scratchpad for a prompt.

    Deliberately NOT JSON: handing the model a JSON object invites it to reply
    with one, which then lands as raw JSON in the artifact panel.
    """

    sp = scratchpad or {}
    sections = []
    if sp.get("topic"):
        sections.append(f"SUBJECT: {sp['topic']}")
    if sp.get("product_mode"):
        sections.append(f"PRODUCT MODE: {sp['product_mode']}")
    sections.append("CURRENT BODY:\n" + ((sp.get("body") or "").strip() or "(empty)"))
    for chunk in (
        _bullets("CONFIRMED FACTS (only these are verified):", sp.get("sources")),
        _bullets("OPEN QUESTIONS (unconfirmed — do not state as fact):", sp.get("open_questions")),
        _bullets("ANGLES ON THE BOARD:", sp.get("angles")),
        _bullets("OUTLINE:", sp.get("outline")),
    ):
        if chunk:
            sections.append(chunk)
    return "\n\n".join(sections)


# --- brainstorm --------------------------------------------------------------

def brainstorm(brief: dict[str, Any]) -> dict[str, Any]:
    """Return {"angles": [...], "outline": [...], "open_questions": [...]}."""

    model = get_chat_model(tags=["signal:brainstorm"])
    response = model.invoke(
        [
            SystemMessage(content=BRAINSTORM_SYSTEM_PROMPT),
            HumanMessage(content=_json(brief)),
        ]
    )
    raw = _text(response).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"angles": [ln.strip("-* ") for ln in raw.splitlines() if ln.strip()]}
    return {
        "angles": [a for a in data.get("angles", []) if isinstance(a, str)],
        "outline": [o for o in data.get("outline", []) if isinstance(o, str)],
        "open_questions": [q for q in data.get("open_questions", []) if isinstance(q, str)],
    }


# --- expand / tighten (streaming) -----------------------------------------

def expand(scratchpad: dict[str, Any], instruction: str) -> Iterator[str]:
    """Stream a developed version of the scratchpad body."""

    model = get_chat_model(streaming=True, tags=["signal:expand"])
    human = (
        f"{render_scratchpad(scratchpad)}\n\n"
        f"INSTRUCTION: {instruction or 'develop the notes further'}\n\n"
        "Return the developed body as plain markdown prose — no JSON, no code "
        "fence, no preamble."
    )
    messages = [
        SystemMessage(content=EXPAND_SYSTEM_PROMPT),
        HumanMessage(content=human),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece


def tighten(scratchpad: dict[str, Any], instruction: str) -> Iterator[str]:
    """Stream an edited version of the scratchpad body."""

    model = get_chat_model(streaming=True, tags=["signal:tighten"])
    human = (
        f"{render_scratchpad(scratchpad)}\n\n"
        f"EDIT: {instruction or 'tighten it'}\n\n"
        "Return the edited body as plain markdown prose — no JSON, no code "
        "fence, no preamble."
    )
    messages = [
        SystemMessage(content=TIGHTEN_SYSTEM_PROMPT),
        HumanMessage(content=human),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece


# --- critique -------------------------------------------------------------

def critique(scratchpad: dict[str, Any]) -> Critique:
    model = get_chat_model(tags=["signal:critique"])
    try:
        structured = model.with_structured_output(Critique)
        result = structured.invoke(
            [
                SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
                HumanMessage(content=_json({"scratchpad": scratchpad})),
            ]
        )
        if isinstance(result, Critique):
            return result
        if isinstance(result, dict):
            return Critique.model_validate(result)
    except Exception:
        pass
    return Critique(summary="", points=[])


# --- grounding notes ----------------------------------------------------

def grounding_notes(
    text: str,
    sources: list[str],
    product_mode: str = "existing",
) -> list[str]:
    """Cheap, non-blocking pass: claims not backed by `sources`."""

    if not text.strip():
        return []
    model = get_chat_model(temperature=0.0, tags=["signal:grounding"])
    payload = {"product_mode": product_mode, "sources": sources, "text": text}
    try:
        structured = model.with_structured_output(GroundingNotes)
        result = structured.invoke(
            [
                SystemMessage(content=GROUNDING_SYSTEM_PROMPT),
                HumanMessage(content=_json(payload)),
            ]
        )
        if isinstance(result, GroundingNotes):
            return result.items[:5]
        if isinstance(result, dict):
            return [str(item) for item in result.get("items", [])][:5]
    except Exception:
        pass
    return []


# --- chat reply (streaming) ------------------------------------------------

def chat_reply(
    turns: list[dict[str, Any]],
    scratchpad: dict[str, Any],
    reply_gist: str | None,
) -> Iterator[str]:
    """Stream a short conversational reply token-by-token."""

    model = get_chat_model(streaming=True, tags=["signal:reply"])
    payload = {
        "recent_turns": turns,
        "scratchpad": scratchpad,
        "reply_should_convey": reply_gist or "a helpful, forward-moving reply",
    }
    messages = [
        SystemMessage(content=CHAT_SYSTEM_PROMPT),
        HumanMessage(content=_json(payload)),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece
