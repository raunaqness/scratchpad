"""DeepEval conversation tests built directly from real, feedback-annotated
conversations (see tests/fixtures/conversations/*.json, exported via
backend/export_conversation.py / scripts/export-conversation.sh).

Unlike tests/test_conversations.py's hand-designed scenarios, the judged
expectation here is never written by a developer — it's the user's own
feedback text, verbatim, pulled straight from the fixture. Paraphrasing
feedback into a spec is itself a place misunderstandings creep in; it isn't
needed here, since the feedback already says what should have happened.

Only turns whose `expected["testable_via"] == "deepeval"` are picked up —
that marks a turn where the real problem is in live-model behavior
(classification, whether a reply cites facts) that the offline fake-LLM
replay in test_recorded_conversations.py structurally cannot exercise,
because that file has to force-feed the historical `mode` rather than let
`interpret` decide for real.

Needs a live OpenRouter key; skips otherwise, same as test_conversations.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepeval")

from deepeval import assert_test
from deepeval.metrics import ConversationalGEval
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

from backend.app import run_conversation
from backend.config import settings
from tests.conftest import openrouter_eval_model

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "conversations"

_ROLE = (
    "Scratchpad, a thinking partner for a freeform document. It captures the "
    "user's raw ideas, develops them on request, brainstorms angles, and can "
    "build a derived artifact (blog outline, social post, campaign) via a "
    "skill — but only when that artifact is genuinely what's being asked for, "
    "not just because a message contains a word like 'build' or 'generate'."
)


def _deepeval_cases() -> list[tuple[str, int, list[str], list[str]]]:
    """(conversation_id, turn_index, user_messages_up_to_and_including_turn,
    feedback_comments) for every turn marked testable_via: deepeval."""

    cases = []
    if not _FIXTURES_DIR.exists():
        return cases
    for path in sorted(_FIXTURES_DIR.glob("*.json")):
        fixture: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        for turn in fixture["turns"]:
            expected = turn.get("expected")
            if not expected or expected.get("testable_via") != "deepeval":
                continue
            prefix = [
                t["user_message"]
                for t in fixture["turns"]
                if t["turn_index"] <= turn["turn_index"]
            ]
            comments = [f["comment"] for f in turn["feedback"]]
            cases.append((fixture["conversation_id"], turn["turn_index"], prefix, comments))
    return cases


_CASES = _deepeval_cases()


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "signal.db")


@pytest.mark.parametrize(
    "conversation_id,turn_index,user_messages,feedback_comments",
    _CASES,
    ids=[f"{c}-turn{t}" for c, t, _, _ in _CASES],
)
def test_feedback_turn_matches_expectation(
    conversation_id, turn_index, user_messages, feedback_comments, isolated_state
):
    model = openrouter_eval_model()
    eval_conversation_id = f"eval-{conversation_id}-turn{turn_index}"

    turns: list[Turn] = []
    for message in user_messages:
        result = run_conversation(
            user_id=f"eval-{conversation_id}",
            conversation_id=eval_conversation_id,
            user_message=message,
        )
        turns.append(Turn(role="user", content=message))
        turns.append(Turn(role="assistant", content=result["assistant_message"]))

    expected_outcome = (
        "A real user had this exact conversation and reported a problem with "
        "the assistant's FINAL reply specifically (the earlier turns are just "
        "context that led up to it). The corrected system must not reproduce "
        "the reported problem:\n" + "\n---\n".join(feedback_comments)
    )
    metric = ConversationalGEval(
        name="Matches user-reported expectation",
        criteria=(
            "Judge only the assistant's final reply in this conversation. "
            "expected_outcome is a real user's bug report about that exact "
            "reply, in their own words — read it as a description of the "
            "problem to avoid, not a literal script the reply must follow. "
            "Pass only if the final reply does not exhibit the reported "
            "problem."
        ),
        evaluation_params=[MultiTurnParams.CONTENT],
        model=model,
        threshold=0.7,
        async_mode=False,
    )

    test_case = ConversationalTestCase(
        scenario=user_messages[0],
        expected_outcome=expected_outcome,
        chatbot_role=_ROLE,
        turns=turns,
    )
    assert_test(test_case=test_case, metrics=[metric], run_async=False)
