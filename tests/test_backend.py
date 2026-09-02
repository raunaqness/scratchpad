"""Fast backend regression tests that do not call a remote model."""

import json

import app
from config import settings


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeModel:
    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)

    def invoke(self, _messages):
        return FakeResponse(json.dumps(next(self.responses)))


def test_maximum_words_is_extracted():
    assert app._maximum_words("under 100 words") == 100
    assert app._maximum_words("maximum of 80 words") == 80
    assert app._maximum_words("medium") is None


def test_confirmed_fields_survive_later_extraction():
    merged = app._merge_analysis(
        {"company": "Fujifilm", "product_name": "X100"},
        {"scope": "linkedin_post", "tone": "confident"},
    )
    assert merged["company"] == "Fujifilm"
    assert merged["product_name"] == "X100"
    assert merged["tone"] == "confident"


def test_conversation_persists_analysis_and_trajectory(tmp_path, monkeypatch):
    original_data_dir = settings.data_dir
    settings.data_dir = tmp_path
    monkeypatch.setattr(
        app,
        "_model",
        lambda: FakeModel(
            [
                {
                    "scope": "linkedin_post",
                    "company": "Fujifilm",
                    "product_name": "X100",
                    "product_description": "A compact camera for street photography.",
                    "tone": "confident",
                    "length": "under 10 words",
                    "needs_clarification": False,
                },
                {"scope": "linkedin_post"},
            ]
        ),
    )
    monkeypatch.setattr(
        app,
        "create_linkedin_post",
        lambda _request: "Fujifilm X100: compact street photography camera.",
    )

    try:
        first = app.run_conversation(
            user_id="test-user",
            conversation_id="test-conversation",
            user_message="Create the post with the supplied campaign details.",
        )
        second = app.run_conversation(
            user_id="test-user",
            conversation_id="test-conversation",
            user_message="Use the same confirmed details.",
        )
    finally:
        settings.data_dir = original_data_dir

    assert first["status"] == "complete"
    assert second["status"] == "complete"
    stored = json.loads(
        (tmp_path / "conversations" / "test-conversation.json").read_text()
    )
    assert stored["analysis"]["product_name"] == "X100"
    assert stored["trajectory"][-1]["step"] == "validate_constraints"
