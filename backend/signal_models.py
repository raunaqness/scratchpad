"""Typed contracts shared across the Scratchpad graph.

``TurnPlan`` is the structured interpretation of one user turn — the graph
trusts a validated ``TurnPlan`` and routes on it; it does not second-guess it
with regexes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Scratchpad operations + the two non-scratchpad modes (build a derived
# artifact, or just reply).
Mode = Literal[
    "note",       # capture the user's raw text into the scratchpad verbatim
    "expand",     # develop a rough note into fuller prose / bullets
    "tighten",    # a specific edit to the scratchpad, nothing else
    "brainstorm", # put angles / directions on the board
    "critique",   # an editor's read of the scratchpad
    "build",      # run a skill: turn the scratchpad into a derived artifact
    "chat",       # answer, clarify, greet
]
ProductMode = Literal["existing", "exploratory"]
SafetyFlag = Literal["ok", "publish_request", "disallowed"]


class TurnPlan(BaseModel):
    """What the user wants this turn and how to move the scratchpad forward."""

    mode: Mode = "chat"
    product_mode: ProductMode | None = None
    topic: str | None = None

    # build mode: which skill to run (id from the registry), plus optional hints
    skill_id: str | None = None
    tone: str | None = None
    length: str | None = None
    audience: str | None = None
    cta: str | None = None

    # new facts stated explicitly this turn (verbatim, short)
    confirmed_facts: list[str] = Field(default_factory=list)

    # scratchpad steering
    chosen_angle: str | None = None
    edit_instruction: str | None = None  # for `tighten`
    note_text: str | None = None         # for `note` (defaults to the raw message)

    # True when the user switches the scratchpad to a DIFFERENT subject than its
    # current topic (a rename / correction), not just adding detail.
    subject_changed: bool = False

    # conversation control
    clarifying_question: str | None = None
    reply_gist: str | None = None
    safety_flag: SafetyFlag = "ok"


class GroundingNotes(BaseModel):
    """Claims in text that are not backed by the confirmed sources."""

    items: list[str] = Field(default_factory=list)


FollowUpKind = Literal[
    "fact",         # a concrete detail the artifact will need and the scratchpad lacks
    "perspective",  # a stakeholder lens / counter-view the user is not holding
    "tone",         # a deliberate voice / stance choice for the eventual artifact
    "angle",        # a sharper, more surprising way into the same material
    "direction",    # a scope / strategy fork worth deciding now
    "question",     # a provoking question that would change the work if answered
]


class FollowUp(BaseModel):
    """One proposed next move. ``label`` is sent verbatim as the user's next
    message if they click the button."""

    label: str
    kind: FollowUpKind = "direction"


class FollowUps(BaseModel):
    """The creative agent's 3-5 next moves after a scratchpad change."""

    items: list[FollowUp] = Field(default_factory=list)


class Critique(BaseModel):
    """Reviewer feedback on the current scratchpad."""

    summary: str = ""
    points: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    """Result of a deterministic, application-owned policy check."""

    allowed: bool
    reason: str | None = None
    flag: SafetyFlag = "ok"
    limits: dict[str, Any] = Field(default_factory=dict)
