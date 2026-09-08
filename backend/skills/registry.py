"""The skill registry.

A **skill** transforms the current scratchpad into a derived artifact. Skills
are declared here; adding one is: create ``skills/<id>/SKILL.md``, add an entry
below, and (if it needs bespoke wording) a branch in
``prompts.skill_system_prompt``. The graph's ``build`` node resolves
``TurnPlan.skill_id`` against this registry; ``policy.check_skill`` gates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    # a short imperative for the UI button ("Generate a blog outline")
    action: str

    @property
    def craft_notes(self) -> str:
        return _craft_notes(self.id)


@lru_cache(maxsize=32)
def _craft_notes(skill_id: str) -> str:
    path = _SKILLS_DIR / skill_id / "SKILL.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, _, text = text.partition("\n---\n")
    return text.strip()


SKILLS: dict[str, Skill] = {
    "blog_outline": Skill(
        id="blog_outline",
        name="Blog outline",
        description="A working outline for a blog post — title, lede, sections, close.",
        action="Generate a blog outline",
    ),
    "social_post": Skill(
        id="social_post",
        name="Social post",
        description="A short LinkedIn / social post — one idea, a feed-first hook.",
        action="Generate a social post",
    ),
    "marketing_campaign": Skill(
        id="marketing_campaign",
        name="Marketing campaign",
        description="A lightweight campaign plan — objective, audience, message, channels, sequence.",
        action="Generate a marketing campaign",
    ),
}


def get_skill(skill_id: str | None) -> Skill | None:
    return SKILLS.get(skill_id) if skill_id else None


def skill_choices() -> list[dict[str, str]]:
    """Compact list for the 'which skill?' reply and the UI buttons."""

    return [
        {"id": s.id, "name": s.name, "description": s.description, "action": s.action}
        for s in SKILLS.values()
    ]
