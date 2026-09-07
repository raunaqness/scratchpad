"""Per-user durable memory.

Thread / conversation state lives in the LangGraph SQLite checkpointer. This
module is only the small, cross-thread slice: things worth remembering about a
person between conversations (their company, house tone, recurring product).

Recall is deliberately shallow — memory is passed to the turn interpreter as
context, never auto-merged into an artifact. The user re-stating a fact is what
puts it into `sources`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings

_DURABLE_FACT_KEYS = ("audience",)
_DURABLE_PREF_KEYS = ("tone",)


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", (value or "").strip())
    if not safe:
        raise ValueError("user_id must not be empty")
    return safe[:200]


def _path(user_id: str) -> Path:
    return settings.data_dir / "memory" / f"{_safe_id(user_id)}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_memory(user_id: str) -> dict[str, Any]:
    path = _path(user_id)
    if not path.exists():
        return {"user_id": _safe_id(user_id), "facts": {}, "preferences": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"user_id": _safe_id(user_id), "facts": {}, "preferences": {}}
    data.setdefault("facts", {})
    data.setdefault("preferences", {})
    return data


def compact(memory: dict[str, Any]) -> dict[str, Any]:
    """The trimmed view handed to the interpreter prompt."""

    facts = {k: v.get("value") if isinstance(v, dict) else v
             for k, v in memory.get("facts", {}).items()}
    prefs = {k: v.get("value") if isinstance(v, dict) else v
             for k, v in memory.get("preferences", {}).items()}
    return {k: v for k, v in {**facts, **{f"preferred_{k}": p for k, p in prefs.items()}}.items() if v}


def save_memory(user_id: str, plan: dict[str, Any], artifact: dict[str, Any]) -> None:
    """Persist only durable, explicitly-stated signal from this turn."""

    memory = load_memory(user_id)
    facts: dict[str, Any] = memory.get("facts", {})
    prefs: dict[str, Any] = memory.get("preferences", {})

    plan = plan or {}
    for key in _DURABLE_FACT_KEYS:
        value = plan.get(key)
        if value:
            facts[key] = {"value": value, "source": "user_conversation", "updated_at": _now()}
    for key in _DURABLE_PREF_KEYS:
        value = plan.get(key) or (artifact or {}).get(key)
        if value:
            prefs[key] = {"value": value, "source": "user_conversation", "updated_at": _now()}

    memory.update({"facts": facts, "preferences": prefs, "updated_at": _now()})
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
