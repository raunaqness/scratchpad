"""Deterministic product workflow tests without ConversationSimulator."""

import json
from pathlib import Path

import pytest

import backend.app as app
from backend.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = PROJECT_ROOT / "product_scenarios.json"
PRODUCTS_PATH = PROJECT_ROOT / "amazon_products"


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeModel:
    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)

    def invoke(self, _messages):
        return FakeResponse(json.dumps(next(self.responses)))


def load_product_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def configure_fakes(monkeypatch, responses: list[dict], requests: list[dict]):
    fake_model = FakeModel(responses)
    monkeypatch.setattr(app, "_model", lambda: fake_model)
    monkeypatch.setattr(
        app,
        "create_linkedin_post",
        lambda request: (
            requests.append(request)
            or f"{request['product_name']}: {request.get('product_description', '')}"
        ),
    )


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    original_data_dir = settings.data_dir
    settings.data_dir = tmp_path
    yield
    settings.data_dir = original_data_dir


def test_product_scenarios_reference_real_product_files():
    scenarios = load_product_scenarios()
    assert len(scenarios) == 3

    for scenario in scenarios:
        product_path = PROJECT_ROOT / scenario["product_file"]
        product = json.loads(product_path.read_text(encoding="utf-8"))
        assert product_path.parent == PRODUCTS_PATH
        assert scenario["company"] == product["brand"]
        assert scenario["product_facts"]
        assert len(scenario["product_facts"]) >= 3


@pytest.mark.parametrize("scenario", load_product_scenarios(), ids=lambda item: item["name"])
def test_three_facts_generate_without_implicit_word_limit(
    scenario, isolated_data_dir, monkeypatch
):
    requests = []
    configure_fakes(
        monkeypatch,
        [
            {
                "scope": "linkedin_post",
                "company": scenario["company"],
                "product_name": scenario["product_name"],
                "product_facts": scenario["product_facts"],
            }
        ],
        requests,
    )

    result = app.run_conversation(
        user_id=f"user-{scenario['name']}",
        conversation_id=f"conversation-{scenario['name']}",
        user_message=scenario["user_request"],
    )

    assert result["status"] == "complete"
    assert requests[0]["product_facts"] == scenario["product_facts"]
    assert "length" not in requests[0]


def test_sandisk_accumulates_facts_until_three(
    isolated_data_dir, monkeypatch
):
    scenario = next(
        item for item in load_product_scenarios()
        if item["name"] == "sandisk_extreme_collects_three_facts"
    )
    requests = []
    configure_fakes(
        monkeypatch,
        [
            {
                "scope": "linkedin_post",
                "company": scenario["company"],
                "product_name": scenario["product_name"],
                "product_facts": scenario["product_facts"][:2],
            },
            {
                "scope": "linkedin_post",
                "product_facts": [scenario["product_facts"][2]],
            },
        ],
        requests,
    )

    first = app.run_conversation(
        user_id="user-sandisk",
        conversation_id="conversation-sandisk",
        user_message="I want a post for this SanDisk card.",
    )
    second = app.run_conversation(
        user_id="user-sandisk",
        conversation_id="conversation-sandisk",
        user_message="Here is one more product fact.",
    )

    assert first["status"] == "needs_clarification"
    assert "at least three" in first["assistant_message"]
    assert second["status"] == "complete"
    assert requests[0]["product_facts"] == scenario["product_facts"][:3]


def test_random_information_does_not_become_product_facts(
    isolated_data_dir, monkeypatch
):
    requests = []
    configure_fakes(
        monkeypatch,
        [{"scope": "linkedin_post"}],
        requests,
    )

    result = app.run_conversation(
        user_id="user-random",
        conversation_id="conversation-random",
        user_message="I enjoy cooking pasta and hiking on weekends.",
    )

    assert result["status"] == "needs_clarification"
    assert "product name" in result["assistant_message"]
    assert requests == []


def test_recipe_request_is_redirected_without_generation(
    isolated_data_dir, monkeypatch
):
    requests = []
    configure_fakes(
        monkeypatch,
        [{"scope": "out_of_scope"}],
        requests,
    )

    result = app.run_conversation(
        user_id="user-recipe",
        conversation_id="conversation-recipe",
        user_message="Tell me a recipe for cooking pasta.",
    )

    assert result["status"] == "out_of_scope"
    assert "LinkedIn posts" in result["assistant_message"]
    assert requests == []


def test_explicit_length_is_preserved_for_marshall(
    isolated_data_dir, monkeypatch
):
    scenario = next(
        item for item in load_product_scenarios()
        if item["name"] == "marshall_willen_two_explicit_length"
    )
    requests = []
    configure_fakes(
        monkeypatch,
        [
            {
                "scope": "linkedin_post",
                "company": scenario["company"],
                "product_name": scenario["product_name"],
                "product_facts": scenario["product_facts"],
                "tone": scenario["tone"],
                "length": scenario["length"],
            }
        ],
        requests,
    )

    result = app.run_conversation(
        user_id="user-marshall",
        conversation_id="conversation-marshall",
        user_message=scenario["user_request"],
    )

    assert result["status"] == "complete"
    assert requests[0]["tone"] == "professional"
    assert requests[0]["length"] == "under 60 words"
    post = result["assistant_message"].split("\n\n", maxsplit=1)[1]
    assert len(post.split()) <= 60
