"""Skill execution — build a derived artifact from a scratchpad snapshot.

``run_skill`` is deliberately narrow: it is handed the scratchpad snapshot dict
and nothing else (no transcript, no web, no memory). It streams the derived
artifact body token-by-token; the graph's ``build`` node collects it into a
``DerivedArtifact`` and never touches the scratchpad.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm import get_chat_model
from backend.prompts import skill_system_prompt
from backend.skills.registry import Skill

# Only these scratchpad fields are handed to a skill.
_SNAPSHOT_KEYS = ("title", "topic", "body", "angles", "outline", "sources", "open_questions", "product_mode")


def scratchpad_snapshot(scratchpad: dict[str, Any]) -> dict[str, Any]:
    return {key: scratchpad.get(key) for key in _SNAPSHOT_KEYS if scratchpad.get(key) not in (None, "", [])}


def run_skill(
    skill: Skill,
    scratchpad: dict[str, Any],
    hints: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Stream the derived artifact body for ``skill`` from a scratchpad snapshot."""

    model = get_chat_model(streaming=True, tags=["signal:skill", f"skill:{skill.id}"])
    payload = {
        "scratchpad": scratchpad_snapshot(scratchpad),
        "hints": {k: v for k, v in (hints or {}).items() if v},
    }
    messages = [
        SystemMessage(content=skill_system_prompt(skill.id)),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
    ]
    for chunk in model.stream(messages):
        content = getattr(chunk, "content", chunk)
        if isinstance(content, str) and content:
            yield content
