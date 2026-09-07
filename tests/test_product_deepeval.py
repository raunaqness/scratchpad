"""Fixed-input DeepEval scenarios for the Amazon product catalog."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.metrics import (
    ConversationCompletenessMetric,
    ConversationalGEval,
    GoalAccuracyMetric,
    KnowledgeRetentionMetric,
)
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

import backend.app as app
from backend.config import settings
from tests.conftest import openrouter_eval_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = PROJECT_ROOT / "product_deepeval_scenarios.json"
RUN_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "deepeval-runs.jsonl"
ROLE = (
    "You are Signal, a constrained LinkedIn marketing assistant. "
    "Use only user-provided product information, do not invent claims, "
    "and do not perform tasks outside LinkedIn post creation."
)


def load_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def metric_for(model, metric_name: str, threshold: float = 0.7):
    if metric_name == "conversation_completeness":
        return ConversationCompletenessMetric(
            model=model, threshold=threshold, async_mode=False
        )
    if metric_name == "knowledge_retention":
        return KnowledgeRetentionMetric(
            model=model, threshold=threshold, async_mode=False
        )
    if metric_name == "goal_accuracy":
        return GoalAccuracyMetric(
            model=model, threshold=threshold, async_mode=False
        )
    if metric_name == "tone_adherence":
        return ConversationalGEval(
            name="Tone Adherence",
            criteria=(
                "The assistant's final LinkedIn post uses the explicitly "
                "requested tone without changing the requested style."
            ),
            evaluation_params=[MultiTurnParams.CONTENT],
            model=model,
            threshold=threshold,
            async_mode=False,
        )
    if metric_name == "unsupported_claim_prevention":
        return ConversationalGEval(
            name="Unsupported Claim Prevention",
            criteria=(
                "The assistant uses only facts provided by the user and does "
                "not invent specifications or performance claims."
            ),
            evaluation_params=[MultiTurnParams.CONTENT],
            model=model,
            threshold=threshold,
            async_mode=False,
        )
    raise ValueError(f"Unsupported metric: {metric_name}")


def write_run_log(scenario: dict, test_case, metric, status: str, error=None):
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scenario_name": scenario["name"],
        "metric": getattr(metric, "name", type(metric).__name__),
        "status": status,
        "error": error,
        "turns": [
            {"role": turn.role, "content": turn.content}
            for turn in test_case.turns
        ],
    }
    with RUN_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def isolated_data_dir(tmp_path):
    original_data_dir = settings.data_dir
    settings.data_dir = tmp_path
    yield
    settings.data_dir = original_data_dir


def build_fixed_test_case(scenario: dict) -> ConversationalTestCase:
    turns = []
    for user_message in scenario["user_turns"]:
        result = app.run_conversation(
            user_id=f"fixed-{scenario['name']}",
            conversation_id=f"fixed-{scenario['name']}",
            user_message=user_message,
        )
        turns.extend(
            [
                Turn(role="user", content=user_message),
                Turn(role="assistant", content=result["assistant_message"]),
            ]
        )
    return ConversationalTestCase(
        scenario=scenario["scenario"],
        expected_outcome=scenario["expected_outcome"],
        turns=turns,
        chatbot_role=ROLE,
        metadata={
            "scenario_name": scenario["name"],
            "product_file": scenario["product_file"],
        },
    )


@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda item: item["name"])
def test_fixed_product_scenario_with_deepeval_metric(
    scenario, isolated_data_dir
):
    model = openrouter_eval_model()
    test_case = build_fixed_test_case(scenario)
    metric = metric_for(
        model,
        scenario["metric"],
        threshold=float(scenario.get("threshold", 0.7)),
    )
    try:
        assert_test(
            test_case=test_case,
            metrics=[metric],
            run_async=False,
        )
    except Exception as exc:
        write_run_log(scenario, test_case, metric, "failed", str(exc))
        raise
    write_run_log(scenario, test_case, metric, "passed")
