"""DeepEval checks for Signal's helpful conversational tone."""

import pytest
from deepeval import assert_test
from deepeval.metrics import ConversationalGEval
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

import app
from config import settings
from tests.conftest import openrouter_eval_model


CASES = [
    {
        "name": "greeting",
        "user": "Hey, is this thing on?",
        "required": "Yes",
        "criteria": (
            "The assistant warmly acknowledges the user, confirms it is "
            "available, and briefly explains how it can help. It does not "
            "respond with a generic refusal or an unrelated capability list."
        ),
    },
    {
        "name": "future_blog_request",
        "user": (
            "Help me write a blog post for a product I am launching: "
            "Marshall Emberton Bluetooth speaker."
        ),
        "required": "blog posts",
        "criteria": (
            "The assistant acknowledges the user's broader content goal "
            "helpfully, explains that blog creation is planned but not yet "
            "available, preserves the product context, and gives a useful "
            "next step for the currently available LinkedIn workflow. It "
            "does not sound abrupt or repeat a generic refusal."
        ),
    },
    {
        "name": "missing_product_facts",
        "user": "I want a LinkedIn post for the Marshall Emberton speaker.",
        "required": "three",
        "criteria": (
            "The assistant is warm and helpful, acknowledges the product "
            "request, and asks for the missing product name or three key "
            "product features in a clear next step. It does not claim to "
            "research facts or sound like a generic policy error."
        ),
    },
]


@pytest.fixture
def isolated_data_dir(tmp_path):
    """Keep conversational tone tests from modifying local application data."""

    original_data_dir = settings.data_dir
    settings.data_dir = tmp_path
    yield
    settings.data_dir = original_data_dir


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_helpful_assistant_tone(case, isolated_data_dir):
    """Evaluate helpful acknowledgements with DeepEval and a basic contract."""

    result = app.run_conversation(
        user_id=f"tone-{case['name']}",
        conversation_id=f"tone-{case['name']}",
        user_message=case["user"],
    )
    response = result["assistant_message"]
    assert case["required"].casefold() in response.casefold()

    test_case = ConversationalTestCase(
        scenario=case["user"],
        expected_outcome=case["criteria"],
        chatbot_role=(
            "A warm, concise Signal assistant that helps users create "
            "grounded LinkedIn posts and explains future capabilities "
            "helpfully."
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
