"""DeepEval conversation tests for the think-pad.

Fixed user turns (deterministic input), the real backend, one focused metric per
scenario. The default ``ConversationSimulator`` is intentionally not used here —
it produced non-actionable failures for a workflow this specific. Needs a live
OpenRouter key; skips otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("deepeval")

from deepeval import assert_test
from deepeval.metrics import (
    ConversationalGEval,
    ConversationCompletenessMetric,
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
)
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

from backend.app import run_conversation
from backend.config import settings
from tests.conftest import openrouter_eval_model

SCENARIOS = json.loads(
    (Path(__file__).resolve().parents[1] / "scenario.json").read_text(encoding="utf-8")
)

_GEVAL = {
    "unsupported_claim_prevention": (
        "Unsupported Claim Prevention",
        "The assistant uses only facts the user provided and never states an "
        "unverified spec, number, price, date, or customer claim as fact. "
        "Unverifiable specifics are raised as questions instead.",
    ),
}


def _metric(name: str, model):
    if name in _GEVAL:
        label, criteria = _GEVAL[name]
        return ConversationalGEval(
            name=label,
            criteria=criteria,
            evaluation_params=[MultiTurnParams.CONTENT],
            model=model,
            threshold=0.7,
            async_mode=False,
        )
    return {
        "conversation_completeness": ConversationCompletenessMetric,
        "knowledge_retention": KnowledgeRetentionMetric,
        "role_adherence": RoleAdherenceMetric,
    }[name](model=model, threshold=0.7, async_mode=False)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "signal.db")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_think_pad_scenario(scenario, isolated_state):
    model = openrouter_eval_model()
    turns: list[Turn] = []
    for message in scenario["turns"]:
        result = run_conversation(
            user_id=f"eval-{scenario['name']}",
            conversation_id=f"eval-{scenario['name']}",
            user_message=message,
        )
        turns.append(Turn(role="user", content=message))
        turns.append(Turn(role="assistant", content=result["assistant_message"]))

    test_case = ConversationalTestCase(
        scenario=scenario["turns"][0],
        expected_outcome=scenario["expected_outcome"],
        chatbot_role=scenario["chatbot_role"],
        turns=turns,
    )
    assert_test(test_case=test_case, metrics=[_metric(scenario["metric"], model)], run_async=False)
