"""Deterministic, application-owned policy checks.

This is the safety boundary that does not live in a prompt. It is deliberately
small: Signal is a writing tool, so the only hard rules are "don't act like you
can publish" and "don't produce an empty draft". Scope (blog vs post vs article)
is a routing decision, not a refusal.
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


def supported_formats() -> list[str]:
    return list(_rules().get("supported_formats", ["linkedin_post"]))


def preflight(message: str) -> PolicyDecision:
    """Classify a turn for the two things the app refuses to do itself.

    Note: this does NOT block blog / article / brainstorm requests. It only
    catches "publish this for me" (Signal has no publishing integration) so the
    graph can answer honestly instead of pretending.
    """

    lowered = message.casefold()
    for phrase in _rules().get("disallowed_action_phrases", []):
        if phrase in lowered:
            return PolicyDecision(
                allowed=True,
                flag="publish_request",
                reason="Signal has no publishing integration.",
            )
    return PolicyDecision(allowed=True, flag="ok")


def check_draft(text: str) -> PolicyDecision:
    """A generated draft must be non-empty. That is the whole gate."""

    if not text or not text.strip():
        return PolicyDecision(
            allowed=False, reason="The writer returned an empty draft."
        )
    return PolicyDecision(allowed=True)
