"""Deterministic, application-owned policy checks.

This is the safety boundary that does not live in a prompt. It is deliberately
small: Scratchpad is a thinking + writing tool, so the only hard rules are
"don't act like you can publish", "don't emit empty output", and "only run a
skill that exists". Which skill / which scratchpad op is a routing decision, not
a refusal.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.signal_models import PolicyDecision

GUARDRAILS_PATH = Path(__file__).resolve().parent / "guardrails.json"


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    try:
        return json.loads(GUARDRAILS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def supported_skills() -> list[str]:
    return list(_rules().get("supported_skills", []))


def max_scratchpad_versions(default: int = 50) -> int:
    value = _rules().get("max_scratchpad_versions", default)
    return int(value) if isinstance(value, (int, float, str)) and str(value).isdigit() else default


def preflight(message: str) -> PolicyDecision:
    """Catch the one thing the app refuses to pretend it can do: publish.

    Does NOT block any scratchpad op or skill. It only catches "publish this for
    me" so the graph can answer honestly instead of implying it happened.
    """

    lowered = message.casefold()
    for phrase in _rules().get("disallowed_action_phrases", []):
        if phrase in lowered:
            return PolicyDecision(
                allowed=True,
                flag="publish_request",
                reason="Scratchpad has no publishing integration.",
            )
    return PolicyDecision(allowed=True, flag="ok")


def check_skill(skill_id: str | None) -> PolicyDecision:
    """A build turn must name a skill that exists in the registry."""

    if skill_id and skill_id in supported_skills():
        return PolicyDecision(allowed=True)
    return PolicyDecision(
        allowed=False,
        reason="unknown or missing skill_id",
        limits={"supported_skills": supported_skills()},
    )


def check_output(text: str) -> PolicyDecision:
    """Generated output (an expansion or a skill artifact) must be non-empty."""

    if not text or not text.strip():
        return PolicyDecision(allowed=False, reason="The model returned nothing usable.")
    return PolicyDecision(allowed=True)
