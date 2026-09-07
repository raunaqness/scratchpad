"""Typed contracts shared across the Signal graph.

``TurnPlan`` is the structured interpretation of one user turn — it replaces the
old 14-key free-text ``SignalAnalysis`` blob. The graph trusts a validated
``TurnPlan`` and routes on it; it does not second-guess it with regexes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["brainstorm", "draft", "revise", "critique", "chat"]
ContentFormat = Literal["linkedin_post", "linkedin_article", "blog_post"]
ProductMode = Literal["existing", "exploratory"]
SafetyFlag = Literal["ok", "publish_request", "disallowed"]


class TurnPlan(BaseModel):
    """What the user wants this turn and how to move the artifact forward."""

    mode: Mode = "chat"
    format: ContentFormat | None = None
    product_mode: ProductMode | None = None
    topic: str | None = None

    # new facts stated explicitly this turn (verbatim, short)
    confirmed_facts: list[str] = Field(default_factory=list)

    # optional constraints the user gave
    tone: str | None = None
    length: str | None = None
    audience: str | None = None
    cta: str | None = None

    # brainstorm / draft steering
    chosen_angle: str | None = None
    revise_instruction: str | None = None

    # True when the user switches the piece to a DIFFERENT subject than the
    # artifact's current topic (a rename / correction), not just adding detail.
    # The graph rebases the artifact when this is set alongside a new `topic`.
    subject_changed: bool = False

    # conversation control
    clarifying_question: str | None = None
    reply_gist: str | None = None
    safety_flag: SafetyFlag = "ok"


class GroundingNotes(BaseModel):
    """Claims in a draft that are not backed by the confirmed sources."""

    items: list[str] = Field(default_factory=list)


class Critique(BaseModel):
    """Reviewer feedback on the current draft."""

    summary: str = ""
    points: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    """Result of a deterministic, application-owned policy check."""

    allowed: bool
    reason: str | None = None
    flag: SafetyFlag = "ok"
    limits: dict[str, Any] = Field(default_factory=dict)
