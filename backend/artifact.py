"""The live artifact — Signal's shared workspace with the user.

The artifact is the thing the user watches change while they talk to Signal. It
starts as a board of angles, becomes an outline, then a draft. Every graph node
returns an updated copy; the AG-UI layer streams each version to the client and
accepts the client's edits back in (``apply_client_edits``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.textutil import dedupe

ArtifactKind = Literal["idea_board", "outline", "draft"]
ArtifactFormat = Literal["linkedin_post", "linkedin_article", "blog_post"]
ArtifactStatus = Literal["exploring", "drafting", "refining", "stable", "empty"]
ProductMode = Literal["existing", "exploratory"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactSection(BaseModel):
    heading: str = ""
    body: str = ""


class Artifact(BaseModel):
    """One evolving piece of content plus the thinking around it."""

    kind: ArtifactKind = "idea_board"
    format: ArtifactFormat = "linkedin_post"
    product_mode: ProductMode = "existing"
    title: str = ""
    topic: str = ""

    # brainstorming surface
    angles: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)

    # draft surface
    body: str = ""
    sections: list[ArtifactSection] = Field(default_factory=list)

    # shared context
    sources: list[str] = Field(default_factory=list)  # facts the user confirmed
    open_questions: list[str] = Field(default_factory=list)  # to confirm / unverified

    status: ArtifactStatus = "empty"
    version: int = 0
    updated_at: str | None = None

    def touched(self, *, status: ArtifactStatus | None = None) -> "Artifact":
        """Return a copy with a bumped version and fresh timestamp."""

        data = self.model_dump()
        data["version"] = int(data.get("version", 0)) + 1
        data["updated_at"] = _now()
        if status is not None:
            data["status"] = status
        return Artifact.model_validate(data)

    def with_sources(self, facts: list[str]) -> "Artifact":
        merged = dedupe([*self.sources, *[f for f in facts if isinstance(f, str)]])
        return self.model_copy(update={"sources": merged})

    def with_questions(self, questions: list[str]) -> "Artifact":
        merged = dedupe([*self.open_questions, *[q for q in questions if isinstance(q, str)]])
        return self.model_copy(update={"open_questions": merged})


def ensure_artifact(value: Any) -> Artifact:
    """Coerce stored dict / None into an :class:`Artifact`."""

    if isinstance(value, Artifact):
        return value
    if isinstance(value, dict) and value:
        try:
            return Artifact.model_validate(value)
        except Exception:  # pragma: no cover - defensive
            pass
    return Artifact()


# Fields the user is allowed to hand back edited from the client. Everything else
# (version, status, sources provenance) stays server-owned.
_CLIENT_EDITABLE = {"title", "topic", "body", "outline", "angles", "sections"}


def apply_client_edits(base: Artifact, client_value: Any) -> Artifact:
    """Fold a client-edited artifact into the server's copy."""

    if not isinstance(client_value, dict) or not client_value:
        return base
    update = {k: v for k, v in client_value.items() if k in _CLIENT_EDITABLE}
    if not update:
        return base
    merged = base.model_copy(update=update)
    return merged.touched(status=base.status if base.status != "empty" else "drafting")
