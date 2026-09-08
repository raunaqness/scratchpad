"""The scratchpad — the freeform thinking surface, and the derived artifacts.

The **scratchpad** is one continuous doc the user jots ideas into and reworks
with Signal. It carries the thinking scaffolding (angles / outline), the
confirmed facts (`sources`), and the things still to confirm (`open_questions`).
It has no target format. Every scratchpad node returns an updated copy; the
AG-UI layer streams each version and accepts the user's edits back
(``apply_client_edits``).

A **derived artifact** is what a *skill* produces from a scratchpad snapshot — a
blog outline, a social post, a campaign. It is a plain output: not versioned,
not editable, shown as a tab.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.textutil import dedupe

ScratchpadStatus = Literal["empty", "notes", "developing", "stable"]
ProductMode = Literal["existing", "exploratory"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Scratchpad(BaseModel):
    """One freeform doc plus the thinking around it."""

    title: str = ""
    topic: str = ""
    body: str = ""  # freeform markdown — the primary surface

    # thinking scaffolding (optional, populated by brainstorm)
    angles: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # shared context
    sources: list[str] = Field(default_factory=list)  # facts the user confirmed
    open_questions: list[str] = Field(default_factory=list)  # to confirm / unverified

    product_mode: ProductMode = "existing"
    status: ScratchpadStatus = "empty"
    version: int = 0
    updated_at: str | None = None

    def touched(self, *, status: ScratchpadStatus | None = None) -> "Scratchpad":
        """Return a copy with a bumped version and fresh timestamp."""

        data = self.model_dump()
        data["version"] = int(data.get("version", 0)) + 1
        data["updated_at"] = _now()
        if status is not None:
            data["status"] = status
        return Scratchpad.model_validate(data)

    def with_sources(self, facts: list[str]) -> "Scratchpad":
        merged = dedupe([*self.sources, *[f for f in facts if isinstance(f, str)]])
        return self.model_copy(update={"sources": merged})

    def with_questions(self, questions: list[str]) -> "Scratchpad":
        merged = dedupe([*self.open_questions, *[q for q in questions if isinstance(q, str)]])
        return self.model_copy(update={"open_questions": merged})


def ensure_scratchpad(value: Any) -> Scratchpad:
    """Coerce a stored dict / None into a :class:`Scratchpad`."""

    if isinstance(value, Scratchpad):
        return value
    if isinstance(value, dict) and value:
        try:
            return Scratchpad.model_validate(value)
        except Exception:  # pragma: no cover - defensive
            pass
    return Scratchpad()


# Fields the user may hand back edited from the client. Everything else
# (version, status, sources provenance) stays server-owned.
_CLIENT_EDITABLE = {"title", "topic", "body", "outline", "angles", "tags"}


def apply_client_edits(base: Scratchpad, client_value: Any) -> Scratchpad:
    """Fold a client-edited scratchpad into the server's copy."""

    if not isinstance(client_value, dict) or not client_value:
        return base
    update = {k: v for k, v in client_value.items() if k in _CLIENT_EDITABLE}
    if not update:
        return base
    merged = base.model_copy(update=update)
    return merged.touched(status=base.status if base.status != "empty" else "notes")


class DerivedArtifact(BaseModel):
    """A skill's output. Not versioned, not editable — one tab."""

    id: str = Field(default_factory=lambda: f"d-{uuid.uuid4().hex[:12]}")
    skill_id: str = ""
    skill_name: str = ""
    title: str = ""
    body: str = ""  # markdown
    from_version: int = 0  # scratchpad version it was built from
    open_questions: list[str] = Field(default_factory=list)  # grounding notes on the output
    created_at: str = Field(default_factory=_now)
