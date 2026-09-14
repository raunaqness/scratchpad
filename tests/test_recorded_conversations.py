"""Regression tests replayed from real, feedback-reviewed conversations.

Each fixture under tests/fixtures/conversations/*.json is exported from a
real thread via backend/export_conversation.py (see
scripts/export-conversation.sh) — turns, per-turn trajectory, and any
feedback left through the always-open feedback box in /app (see
backend/tracing.py's record_feedback).

A turn only becomes an assertion once a human has added an "expected" block
to it in the fixture. That block is never inferred from what actually
happened — for a turn with real feedback attached, what happened is exactly
what's being reported as wrong. Turns with no feedback and an "expected"
block encode "this is confirmed-acceptable behavior, pin it down."

Replay runs against the offline fake LLM (`install_models`, see
tests/conftest.py), with `interpret`'s structured output pinned to the
turn's real recorded `mode`. That means this file can only test the
MECHANICAL behavior of routing and state management GIVEN that
interpretation (does `note` append verbatim, does `brainstorm` populate
angles, does the scratchpad version actually bump) — never whether
`interpret` would classify the raw message the same way against the real
model, because we're the ones force-feeding it the mode.

Some real feedback turns out to be about exactly that classification
question (or about live-model behavior like citing facts) — those turns
have `expected["testable_via"] = "deepeval"` instead of the default
"pytest", and are asserted in test_recorded_conversations_deepeval.py
against the real model instead. They show up below as an explicit skip
naming the reason and pointing at that file, not a silent gap. Turns with
feedback but no `expected` block at all yet are a separate, live TODO,
surfaced via test_feedback_turn_awaiting_expected_decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app import run_conversation
from backend.signal_models import TurnPlan
from tests.conftest import FakeModelScript

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "conversations"


def _load_fixtures() -> list[dict[str, Any]]:
    if not _FIXTURES_DIR.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_FIXTURES_DIR.glob("*.json"))
    ]


_FIXTURES = _load_fixtures()


def _check_turn(
    turn: dict[str, Any], result: dict[str, Any], prev_version: int, prev_derived: list
) -> None:
    expected = turn["expected"]
    label = f"turn {turn['turn_index']} ({turn['mode']})"
    sp = result["scratchpad"]

    if "mode" in expected:
        assert result["mode"] == expected["mode"], (
            f"{label}: expected mode={expected['mode']!r}, got {result['mode']!r}"
        )
    if "status" in expected:
        assert sp["status"] == expected["status"], (
            f"{label}: expected status={expected['status']!r}, got {sp['status']!r}"
        )
    if "scratchpad_changed" in expected:
        changed = sp["version"] != prev_version
        assert changed == expected["scratchpad_changed"], (
            f"{label}: expected scratchpad_changed={expected['scratchpad_changed']}, "
            f"version went {prev_version} -> {sp['version']}"
        )
    if "derived_unchanged" in expected:
        unchanged = result["derived"] == prev_derived
        assert unchanged == expected["derived_unchanged"], (
            f"{label}: expected derived_unchanged={expected['derived_unchanged']}, "
            f"derived went {prev_derived!r} -> {result['derived']!r}"
        )
    if expected.get("note_verbatim_append"):
        assert turn["user_message"].strip() in sp["body"], (
            f"{label}: user message not found verbatim in the scratchpad body"
        )
    if expected.get("angles_populated"):
        assert sp["angles"], f"{label}: expected angles to be populated, got none"


def _replay(fixture: dict[str, Any], install_models) -> tuple[int, int]:
    """Replay every turn of one fixture in order (state carries across
    turns, same as a real thread). Asserts pytest-testable `expected` blocks
    inline; skips (does not fail) turns whose `expected` needs a different
    layer. Returns (checked, skipped_to_other_layer)."""

    conversation_id = f"fixture-{fixture['conversation_id']}"
    script = install_models(FakeModelScript(plan=TurnPlan(mode="chat")))

    prev_version = 0
    prev_derived: list = []
    checked = 0
    other_layer = 0
    for turn in fixture["turns"]:
        script.plan = TurnPlan(mode=turn["mode"] or "chat")
        result = run_conversation(
            user_id="fixture-replay",
            conversation_id=conversation_id,
            user_message=turn["user_message"],
        )
        expected = turn.get("expected")
        if expected is not None:
            if expected.get("testable_via", "pytest") == "pytest":
                _check_turn(turn, result, prev_version, prev_derived)
                checked += 1
            else:
                other_layer += 1
        prev_version = result["scratchpad"]["version"]
        prev_derived = result["derived"]

    return checked, other_layer


@pytest.mark.parametrize(
    "fixture",
    _FIXTURES,
    ids=[f["conversation_id"] for f in _FIXTURES],
)
def test_recorded_conversation_expected_turns(fixture, isolated_state, install_models):
    checked, _other_layer = _replay(fixture, install_models)
    assert checked > 0, "fixture has no pytest-testable `expected` block yet — nothing to test"


def _other_layer_cases() -> list[tuple[str, int, str, str]]:
    cases = []
    for fixture in _FIXTURES:
        for turn in fixture["turns"]:
            expected = turn.get("expected")
            testable_via = (expected or {}).get("testable_via", "pytest")
            if expected is not None and testable_via != "pytest":
                cases.append(
                    (fixture["conversation_id"], turn["turn_index"], testable_via, expected.get("why", ""))
                )
    return cases


@pytest.mark.parametrize(
    "conversation_id,turn_index,testable_via,why",
    _other_layer_cases(),
    ids=[f"{c}-turn{t}" for c, t, _, _ in _other_layer_cases()],
)
def test_feedback_turn_needs_other_layer(conversation_id, turn_index, testable_via, why):
    """This turn has a decided `expected` block, but it can't be checked
    deterministically — usually because the real bug is in live-model
    classification or content, which this file's force-fed-mode replay
    structurally can't exercise. See test_recorded_conversations_deepeval.py."""

    pytest.skip(f"needs the {testable_via} layer instead: {why[:200]}")


def _pending_cases() -> list[tuple[str, int, str]]:
    cases = []
    for fixture in _FIXTURES:
        for turn in fixture["turns"]:
            if "expected" not in turn and turn.get("feedback"):
                cases.append(
                    (fixture["conversation_id"], turn["turn_index"], turn["feedback"][0]["comment"])
                )
    return cases


@pytest.mark.parametrize(
    "conversation_id,turn_index,comment",
    _pending_cases(),
    ids=[f"{c}-turn{t}" for c, t, _ in _pending_cases()],
)
def test_feedback_turn_awaiting_expected_decision(conversation_id, turn_index, comment):
    """Real feedback exists on this turn but no `expected` block does yet —
    a product decision is needed before this can become a real assertion
    (see the module docstring). Shows up as skipped, not silently absent —
    run `pytest -rs` to list every turn still waiting on a decision."""

    pytest.skip(f"needs a product decision: {comment[:200]}")
