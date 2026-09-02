"""DeepEval end-to-end tests for Signal's conversational scenarios."""

import os

import pytest
from deepeval import assert_test
from deepeval.dataset import ConversationalGolden
from deepeval.metrics import (
    ConversationCompletenessMetric,
    ConversationalGEval,
    GoalAccuracyMetric,
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
    TurnRelevancyMetric,
)
from deepeval.simulator import ConversationSimulator
from deepeval.test_case import Turn

from app import run_conversation
from tests.conftest import openrouter_eval_model
from tests.goldens import load_goldens

MAX_USER_SIMULATIONS = int(os.getenv("SIGNAL_MAX_USER_SIMULATIONS", "3"))
ROLE = (
    "You are Signal, a constrained LinkedIn marketing assistant. "
    "Use only user-provided product information, do not invent claims, "
    "and do not perform tasks outside LinkedIn post creation."
)


def signal_callback(input: str, turns: list[Turn], thread_id: str) -> Turn:
    """Adapt DeepEval's simulator callback to Signal's backend contract."""

    result = run_conversation(
        user_id=f"deepeval-{thread_id}",
        conversation_id=thread_id,
        user_message=input,
    )
    response = result.get("assistant_message") or result.get("response")
    if not isinstance(response, str):
        raise TypeError(
            "run_conversation() must return an assistant_message or response string"
        )
    return Turn(role="assistant", content=response)


METRIC_TYPES = {
    "conversation_completeness": ConversationCompletenessMetric,
    "turn_relevancy": TurnRelevancyMetric,
    "knowledge_retention": KnowledgeRetentionMetric,
    "role_adherence": RoleAdherenceMetric,
    "goal_accuracy": GoalAccuracyMetric,
}

CUSTOM_METRICS = {
    "tone_adherence": (
        "Tone Adherence",
        "The assistant's final LinkedIn post uses the tone explicitly requested "
        "by the user, without changing the requested style.",
    ),
    "unsupported_claim_prevention": (
        "Unsupported Claim Prevention",
        "The assistant uses only facts provided by the user and does not invent "
        "prices, dates, specifications, availability, testimonials, or claims.",
    ),
}


def metric_for(model, golden: ConversationalGolden):
    """Build one focused metric for the scenario under test."""

    metadata = golden.additional_metadata or {}
    metric_name = metadata.get("focus_metric", "conversation_completeness")
    if metric_name in CUSTOM_METRICS:
        name, criteria = CUSTOM_METRICS[metric_name]
        return ConversationalGEval(
            name=name,
            criteria=criteria,
            model=model,
            threshold=0.7,
            async_mode=False,
        )
    metric_type = METRIC_TYPES.get(metric_name)
    if metric_type is None:
        pytest.skip(f"{metric_name} is reserved until an external tool exists")
    return metric_type(model=model, threshold=0.7, async_mode=False)


def test_scenario_dataset_contract():
    """Validate the local scenario file without making any LLM calls."""

    goldens = load_goldens()
    assert len(goldens) == 7
    for golden in goldens:
        assert golden.name
        assert golden.scenario
        assert golden.expected_outcome
        assert golden.persona is not None


@pytest.mark.parametrize("golden", load_goldens(), ids=lambda golden: golden.name)
def test_signal_conversation(golden: ConversationalGolden):
    """Simulate and evaluate one scenario against the current backend."""

    model = openrouter_eval_model()
    simulator = ConversationSimulator(
        model_callback=signal_callback,
        simulator_model=model,
        async_mode=False,
    )
    test_cases = simulator.simulate(
        conversational_goldens=[golden],
        max_user_simulations=MAX_USER_SIMULATIONS,
    )

    assert len(test_cases) == 1
    test_case = test_cases[0]
    test_case.chatbot_role = ROLE
    test_case.metadata = {
        **(test_case.metadata or {}),
        "scenario_name": golden.name,
    }
    assert_test(
        test_case=test_case,
        metrics=[metric_for(model, golden)],
        run_async=False,
    )
