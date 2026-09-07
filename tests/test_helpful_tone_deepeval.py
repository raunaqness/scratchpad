"""DeepEval checks for Signal's helpful, collaborative tone."""

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
            "and briefly says it can help brainstorm and write content. It does "
            "not respond with a generic refusal or a wall of rules."
        ),
    },
    {
        "name": "blog_request_is_helped",
        "user": (
            "Help me write a blog post for a product I'm launching: the "
            "Marshall Emberton Bluetooth speaker."
        ),
        "criteria": (
            "The assistant engages with the blog-post request directly - it "
            "does NOT say blog posts are unavailable or redirect to LinkedIn "
            "only. It offers a direction or asks one focused question, and "
            "keeps the product context."
        ),
    },
    {
        "name": "vague_request_gets_angles",
        "user": "I want a LinkedIn post about our new caching layer. Not sure what to say.",
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
            "anywhere, and offers to get the text ready to paste instead. It "
            "does not claim the post was published."
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
            "A warm, concise creative thinking-pad that helps users brainstorm "
            "and write posts, articles, and blog posts, and is honest about "
            "not being able to publish."
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
