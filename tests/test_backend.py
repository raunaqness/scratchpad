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


def test_three_product_facts_are_required():
    assert not app._has_minimum_product_facts(
        {"product_facts": ["compact", "40.2 megapixels"]}
    )
    assert app._has_minimum_product_facts(
        {"product_facts": ["compact", "40.2 megapixels", "hybrid viewfinder"]}
    )


def test_draft_grounding_allows_supplied_facts_and_neutral_copy():
    request = {
        "company": "Fujifilm",
        "product_name": "X100",
        "product_description": "A compact camera for street photography.",
    }
    assert app._draft_is_grounded(
        "Introducing the Fujifilm X100, a compact camera for street photography.",
        request,
    )


def test_draft_grounding_requires_product_name():
    request = {
        "company": "Fujifilm",
        "product_description": "A compact camera for street photography.",
    }
    assert not app._draft_is_grounded(
        "A compact camera for street photography.",
        request,
    )


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
    requests = []
    fake_model = FakeModel(
        [
            {
                "scope": "linkedin_post",
                "company": "Fujifilm",
                "product_name": "X100",
                "product_description": "A compact camera for street photography.",
                "product_facts": [
                    "compact camera",
                    "street photography",
                    "designed for photography",
                ],
                "tone": "confident",
                "length": "under 10 words",
                "needs_clarification": False,
                "progress_update": (
                    "I collected the confirmed facts and validated the draft."
                ),
            },
            {"scope": "linkedin_post"},
            {
                "scope": "linkedin_post",
                "product_description": "A compact camera for travel.",
                "tone": "professional",
            },
        ]
    )
    monkeypatch.setattr(
        app,
        "_model",
        lambda: fake_model,
    )
    monkeypatch.setattr(
        app,
        "create_linkedin_post",
        lambda request: (
            requests.append(request)
            or (
                f"{request['company']} {request['product_name']}: "
                f"{request['product_description']}"
            )
        ),
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
        third = app.run_conversation(
            user_id="test-user",
            conversation_id="test-conversation",
            user_message="Update the description and use a professional tone.",
        )
    finally:
        settings.data_dir = original_data_dir

    assert first["status"] == "complete"
    assert second["status"] == "complete"
    assert third["status"] == "complete"
    assert first["assistant_message"].startswith(
        "I collected the confirmed facts and validated the draft."
    )
    assert requests[1] == requests[0]
    assert requests[2]["product_description"] == "A compact camera for travel."
    assert requests[2]["tone"] == "professional"
    stored = json.loads(
        (tmp_path / "conversations" / "test-conversation.json").read_text()
    )
    memory = json.loads((tmp_path / "memory" / "test-user.json").read_text())
    assert stored["analysis"]["product_name"] == "X100"
    assert stored["analysis"]["product_description"] == "A compact camera for travel."
    assert memory["facts"][2]["key"] == "product_description"
    assert memory["facts"][2]["value"] == "A compact camera for travel."
    assert memory["preferences"]["tone"]["value"] == "professional"
    assert any(
        item["step"] == "validate_constraints" and item["status"] == "passed"
        for item in stored["trajectory"]
    )


def test_draft_is_persisted_validated_and_edited(tmp_path, monkeypatch):
    original_data_dir = settings.data_dir
    settings.data_dir = tmp_path
    create_requests = []
    edit_requests = []
    fake_model = FakeModel(
        [
            {
                "scope": "linkedin_post",
                "intent": "request_post",
                "company": "Fujifilm",
                "product_name": "X100",
                "product_facts": [
                    "compact camera",
                    "street photography",
                    "designed for photography",
                ],
            },
            {"scope": "linkedin_post", "intent": "validate_draft"},
            {
                "scope": "linkedin_post",
                "intent": "edit_draft",
                "edit_instruction": "Make the opening more direct.",
            },
        ]
    )
    monkeypatch.setattr(app, "_model", lambda: fake_model)
    monkeypatch.setattr(
        app,
        "create_linkedin_post",
        lambda request: (
            create_requests.append(request)
            or "Fujifilm X100: compact camera for street photography."
        ),
    )
    monkeypatch.setattr(
        app,
        "edit_linkedin_post",
        lambda request, draft, instruction: (
            edit_requests.append((request, draft, instruction))
            or "Fujifilm X100: designed for photography."
        ),
    )

    try:
        first = app.run_conversation(
            user_id="draft-user",
            conversation_id="draft-conversation",
            user_message="Create the LinkedIn post.",
        )
        validated = app.run_conversation(
            user_id="draft-user",
            conversation_id="draft-conversation",
            user_message="Validate the current draft.",
        )
        edited = app.run_conversation(
            user_id="draft-user",
            conversation_id="draft-conversation",
            user_message="Make the opening more direct.",
        )
    finally:
        settings.data_dir = original_data_dir

    assert first["status"] == "complete"
    assert validated["status"] == "complete"
    assert "validated the current draft" in validated["assistant_message"]
    assert len(create_requests) == 1
    assert len(edit_requests) == 1
    assert edit_requests[0][1] == first["draft"]
    assert edit_requests[0][2] == "Make the opening more direct."
    assert edited["draft_version"] == 2
    stored = json.loads(
        (tmp_path / "conversations" / "draft-conversation.json").read_text()
    )
    assert stored["draft_version"] == 2
    assert [item["version"] for item in stored["draft_history"]] == [1, 2]
    assert stored["validation"]["status"] == "passed"
