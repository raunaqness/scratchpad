"""Skill execution — build a derived artifact from a scratchpad snapshot.

``run_skill`` is deliberately narrow: it is handed the scratchpad snapshot dict
and nothing else (no transcript, no web, no memory). It streams the derived
artifact body token-by-token; the graph's ``build`` node collects it into a
``DerivedArtifact`` and never touches the scratchpad.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.capabilities.writing import render_scratchpad
from backend.config import settings
from backend.llm import get_chat_model
from backend.prompts import skill_system_prompt
from backend.skills.registry import Skill

# The whole scratchpad is a skill's input — the full knowledge base, not a brief.
_SNAPSHOT_KEYS = (
    "title", "topic", "body", "angles", "outline",
    "sources", "open_questions", "tags", "product_mode",
)


def scratchpad_snapshot(scratchpad: dict[str, Any]) -> dict[str, Any]:
    return {key: scratchpad.get(key) for key in _SNAPSHOT_KEYS if scratchpad.get(key) not in (None, "", [])}


def run_skill(
    skill: Skill,
    scratchpad: dict[str, Any],
    hints: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Stream the derived artifact body for ``skill`` from a scratchpad snapshot."""

    model = get_chat_model(
        streaming=True,
        temperature=settings.openrouter_temperature_creative,
        tags=["signal:skill", f"skill:{skill.id}"],
    )
    human = render_scratchpad(scratchpad_snapshot(scratchpad))
    live_hints = {k: v for k, v in (hints or {}).items() if v}
    if live_hints:
        human += "\n\nHINTS:\n" + "\n".join(f"- {k}: {v}" for k, v in live_hints.items())
    human += (
        f"\n\nProduce the {skill.name.lower()} as plain markdown — no JSON, no "
        "code fence, no preamble."
    )
    messages = [
        SystemMessage(content=skill_system_prompt(skill.id)),
        HumanMessage(content=human),
    ]
    for chunk in model.stream(messages):
        content = getattr(chunk, "content", chunk)
        if isinstance(content, str) and content:
            yield content
