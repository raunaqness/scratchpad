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

from backend.config import settings
from backend.llm import get_chat_model
from backend.prompts import (
    BRAINSTORM_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CRITIQUE_SYSTEM_PROMPT,
    EXPAND_SYSTEM_PROMPT,
    FOLLOWUP_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
    TIGHTEN_SYSTEM_PROMPT,
)
from backend.signal_models import Critique, FollowUps, GroundingNotes


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

def brainstorm(
    brief: dict[str, Any],
    knowledge_base_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return {"angles": [...], "outline": [...], "open_questions": [...],
    "sources_used": [...]}."""

    payload = dict(brief)
    if knowledge_base_facts:
        payload["knowledge_base_facts"] = knowledge_base_facts

    model = get_chat_model(tags=["signal:brainstorm"])
    response = model.invoke(
        [
            SystemMessage(content=BRAINSTORM_SYSTEM_PROMPT),
            HumanMessage(content=_json(payload)),
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
        "sources_used": [s for s in data.get("sources_used", []) if isinstance(s, str)],
    }


# --- expand / tighten (streaming) -----------------------------------------

def _knowledge_base_facts_block(knowledge_base_facts: list[dict[str, Any]] | None) -> str:
    if not knowledge_base_facts:
        return ""
    return f"\n\nKNOWLEDGE_BASE_FACTS: {_json(knowledge_base_facts)}"


def expand(
    scratchpad: dict[str, Any],
    instruction: str,
    knowledge_base_facts: list[dict[str, Any]] | None = None,
) -> Iterator[str]:
    """Stream a developed version of the scratchpad body."""

    model = get_chat_model(streaming=True, tags=["signal:expand"])
    human = (
        f"{render_scratchpad(scratchpad)}\n\n"
        f"INSTRUCTION: {instruction or 'develop the notes further'}"
        f"{_knowledge_base_facts_block(knowledge_base_facts)}\n\n"
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


def tighten(
    scratchpad: dict[str, Any],
    instruction: str,
    knowledge_base_facts: list[dict[str, Any]] | None = None,
) -> Iterator[str]:
    """Stream an edited version of the scratchpad body."""

    model = get_chat_model(streaming=True, tags=["signal:tighten"])
    human = (
        f"{render_scratchpad(scratchpad)}\n\n"
        f"EDIT: {instruction or 'tighten it'}"
        f"{_knowledge_base_facts_block(knowledge_base_facts)}\n\n"
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

def critique(
    scratchpad: dict[str, Any],
    knowledge_base_facts: list[dict[str, Any]] | None = None,
) -> Critique:
    model = get_chat_model(tags=["signal:critique"])
    payload: dict[str, Any] = {"scratchpad": scratchpad}
    if knowledge_base_facts:
        payload["knowledge_base_facts"] = knowledge_base_facts
    try:
        structured = model.with_structured_output(Critique)
        result = structured.invoke(
            [
                SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
                HumanMessage(content=_json(payload)),
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


# --- creative follow-ups -------------------------------------------------

_FOLLOWUP_KINDS = {"fact", "perspective", "tone", "angle", "direction", "question"}


def follow_ups(scratchpad: dict[str, Any]) -> list[dict[str, str]]:
    """3-5 next-move suggestions from the current scratchpad. Read-only, hot,
    best-effort — any failure returns ``[]`` and the turn carries on."""

    model = get_chat_model(
        temperature=settings.openrouter_temperature_creative,
        tags=["signal:followup"],
    )
    try:
        structured = model.with_structured_output(FollowUps)
        result = structured.invoke(
            [
                SystemMessage(content=FOLLOWUP_SYSTEM_PROMPT),
                HumanMessage(content=render_scratchpad(scratchpad)),
            ]
        )
        items = result.items if isinstance(result, FollowUps) else (
            [FollowUps.model_validate(result).items][0]
            if isinstance(result, dict)
            else []
        )
    except Exception:  # noqa: BLE001 - never load-bearing
        return []

    out: list[dict[str, str]] = []
    for item in items:
        label = (getattr(item, "label", "") or "").strip()
        kind = getattr(item, "kind", "direction")
        if not label:
            continue
        out.append({"label": label, "kind": kind if kind in _FOLLOWUP_KINDS else "direction"})
        if len(out) == 5:
            break
    return out


# --- chat reply (streaming) ------------------------------------------------

def chat_reply(
    turns: list[dict[str, Any]],
    scratchpad: dict[str, Any],
    reply_gist: str | None,
    knowledge_base_facts: list[dict[str, Any]] | None = None,
) -> Iterator[str]:
    """Stream a short conversational reply token-by-token.

    ``knowledge_base_facts``, when non-empty, are facts retrieved from the
    user's ingest knowledge graph for this turn (see
    docs/plan-chat-knowledge-graph-integration.md) — the model decides
    whether they're relevant, not the caller.
    """

    model = get_chat_model(streaming=True, tags=["signal:reply"])
    payload = {
        "recent_turns": turns,
        "scratchpad": scratchpad,
        "reply_should_convey": reply_gist or "a helpful, forward-moving reply",
    }
    if knowledge_base_facts:
        payload["knowledge_base_facts"] = knowledge_base_facts
    messages = [
        SystemMessage(content=CHAT_SYSTEM_PROMPT),
        HumanMessage(content=_json(payload)),
    ]
    for chunk in model.stream(messages):
        piece = _text(chunk)
        if piece:
            yield piece
