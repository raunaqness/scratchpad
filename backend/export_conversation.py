"""Export one conversation thread — turns, trajectory, and any Langfuse
feedback scores — into a single JSON fixture.

This is the first half of a "real conversation → regression fixture" loop:
have a conversation in /app, leave feedback via the always-open feedback box
(see backend/tracing.py's record_feedback), then export it here. The
resulting JSON is meant to seed both deterministic pytest assertions (mode,
trajectory, whether the scratchpad changed) and DeepEval scenarios (turns +
an expected_outcome narrative) — see docs/plan-house-voice-reader.md's sibling
docs for the existing patterns this is meant to feed into.

Usage:
    python -m backend.export_conversation <user_id> <conversation_id> [--out PATH]

Defaults to tests/fixtures/conversations/<conversation_id>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from backend.app import aget_thread_state
from backend.threads_store import get_thread
from backend.tracing import _client as langfuse_client  # noqa: SLF001 - export tool, not app code
from backend.tracing import turn_trace_id

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "conversations"


def _slice_trajectory(trajectory: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split the thread's flat trajectory into per-turn slices — each turn
    starts with an ``interpret`` step."""

    slices: list[list[dict[str, Any]]] = []
    for entry in trajectory:
        if entry.get("step") == "interpret" or not slices:
            slices.append([])
        slices[-1].append(entry)
    return slices


def _turn_feedback(client: Any, conversation_id: str, turn_index: int) -> list[dict[str, Any]]:
    if client is None:
        return []
    trace_id = turn_trace_id(conversation_id, turn_index)
    if trace_id is None:
        return []
    try:
        trace = client.api.trace.get(trace_id)
    except Exception:  # noqa: BLE001 - a missing/unreachable trace isn't fatal to export
        return []
    return [
        {"comment": s.comment, "timestamp": s.timestamp.isoformat() if s.timestamp else None}
        for s in (trace.scores or [])
        if s.name == "user_feedback"
    ]


def _load_existing_expected(out_path: Path) -> dict[int, Any]:
    """Preserve any hand-authored ``expected`` block from a prior export at
    this same path, keyed by turn_index — re-running the exporter (e.g. to
    pick up a new feedback comment) must never silently discard a decision
    someone already wrote into the fixture."""

    if not out_path.exists():
        return {}
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        t["turn_index"]: t["expected"]
        for t in prior.get("turns", [])
        if "expected" in t
    }


async def export_conversation(
    user_id: str, conversation_id: str, *, out_path: Path | None = None
) -> dict[str, Any]:
    thread = await get_thread(conversation_id)
    state = await aget_thread_state(conversation_id)
    turns = state.get("turns", [])
    trajectory_slices = _slice_trajectory(state.get("trajectory", []))

    client = langfuse_client()
    existing_expected = _load_existing_expected(out_path) if out_path else {}

    exported_turns: list[dict[str, Any]] = []
    for turn_index in range(len(turns) // 2):
        user_entry = turns[turn_index * 2]
        assistant_entry = turns[turn_index * 2 + 1]
        turn_trajectory = (
            trajectory_slices[turn_index] if turn_index < len(trajectory_slices) else []
        )
        mode = next(
            (s.get("mode") for s in turn_trajectory if s.get("step") == "interpret"),
            None,
        )
        turn: dict[str, Any] = {
            "turn_index": turn_index,
            "user_message": user_entry.get("content", ""),
            "assistant_message": assistant_entry.get("content", ""),
            "mode": mode,
            "trajectory": turn_trajectory,
            "feedback": _turn_feedback(client, conversation_id, turn_index),
        }
        if turn_index in existing_expected:
            turn["expected"] = existing_expected[turn_index]
        exported_turns.append(turn)

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "title": thread.get("title") if thread else "",
        "turns": exported_turns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id")
    parser.add_argument("conversation_id")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_path = args.out or (_DEFAULT_DIR / f"{args.conversation_id}.json")
    data = asyncio.run(
        export_conversation(args.user_id, args.conversation_id, out_path=out_path)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(data['turns'])} turns to {out_path}")


if __name__ == "__main__":
    main()
