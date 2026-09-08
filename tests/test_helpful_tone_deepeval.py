"""DeepEval checks for Scratchpad's helpful, collaborative tone."""

from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

from deepeval import assert_test
from deepeval.metrics import ConversationalGEval
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

import backend.app as app
from backend.config import settings
from tests.conftest import openrouter_eval_model


CASES = [
    {
        "name": "greeting",
        "user": "Hey, is this thing on?",
        "criteria": (
            "The assistant warmly acknowledges the user, confirms it is here, "
            "and briefly says it is a scratchpad for working through ideas. It "
            "does not respond with a generic refusal or a wall of rules."
        ),
    },
    {
        "name": "jot_is_captured",
        "user": "Jot this down: we just shipped single sign-on for our API dashboard.",
        "criteria": (
            "The assistant captures the note and offers a concrete next step "
            "(develop it, or a few angles). It does NOT immediately turn the "
            "note into a finished post, and does not add a headline or hashtags."
        ),
    },
    {
        "name": "vague_request_gets_angles",
        "user": "I want to think about our new caching layer. Not sure what to say.",
        "criteria": (
            "The assistant proposes 2-3 concrete angles rather than one generic "
            "take, recommends one, and asks which to pursue or for a missing "
            "detail. It does not demand a fixed number of facts before helping "
            "and does not claim to research the product."
        ),
    },
    {
        "name": "publish_request_is_honest",
        "user": "Perfect. Now publish this to LinkedIn for me.",
        "criteria": (
            "The assistant clearly says it cannot publish, schedule, or post "
            "anywhere, and offers to get something ready to paste instead. It "
            "does not claim anything was published."
        ),
    },
    {
        "name": "build_request_is_engaged",
        "user": "Turn this into a blog outline.",
        "criteria": (
            "The assistant engages with the build request — it either produces "
            "an outline or, if there is nothing on the scratchpad yet, asks the "
            "user to jot something down first. It does not say blog outlines are "
            "unavailable."
        ),
    },
]


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "signal.db")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_helpful_assistant_tone(case, isolated_data_dir):
    result = app.run_conversation(
        user_id=f"tone-{case['name']}",
        conversation_id=f"tone-{case['name']}",
        user_message=case["user"],
    )
    response = result["assistant_message"]
    assert response.strip()

    test_case = ConversationalTestCase(
        scenario=case["user"],
        expected_outcome=case["criteria"],
        chatbot_role=(
            "A warm, concise thinking partner for a freeform scratchpad. It "
            "captures and reworks raw ideas, offers a few angles when a request "
            "is vague, builds a blog outline / social post / marketing campaign "
            "from the scratchpad on request, and is honest that it cannot "
            "publish."
        ),
        turns=[
            Turn(role="user", content=case["user"]),
            Turn(role="assistant", content=response),
        ],
    )
    metric = ConversationalGEval(
        name=f"Helpful Tone: {case['name']}",
        criteria=case["criteria"],
        evaluation_params=[MultiTurnParams.CONTENT],
        model=openrouter_eval_model(),
        threshold=0.7,
        async_mode=False,
    )
    assert_test(test_case=test_case, metrics=[metric], run_async=False)
