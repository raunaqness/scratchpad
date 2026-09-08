"""DeepEval checks for skill outputs (derived artifacts).

Each scenario builds a scratchpad over a few fixed turns, runs a skill, and
judges the *derived artifact* on two axes:

* well-formed for its type (GEval), and
* grounded in the scratchpad it was built from (FaithfulnessMetric — the
  scratchpad body + sources are the retrieval context).

Needs a live OpenRouter key; skips otherwise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("deepeval")

from deepeval import assert_test
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import (
    ConversationalTestCase,
    LLMTestCase,
    SingleTurnParams,
    Turn,
)

import backend.app as app
from backend.config import settings
from tests.conftest import openrouter_eval_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = PROJECT_ROOT / "skill_scenarios.json"
RUN_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "deepeval-runs.jsonl"


def load_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def write_run_log(scenario: dict, body: str, status: str, error: str | None = None) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "suite": "skills",
        "scenario_name": scenario["name"],
        "skill_id": scenario["skill_id"],
        "status": status,
        "error": error,
        "output_preview": body[:400],
    }
    with RUN_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "signal.db")


def _run_scenario(scenario: dict) -> dict:
    cid = f"skill-{scenario['name']}"
    for turn in scenario["setup_turns"]:
        app.run_conversation(user_id=cid, conversation_id=cid, user_message=turn)
    return app.run_conversation(
        user_id=cid, conversation_id=cid, user_message=scenario["build_turn"]
    )


@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["name"])
def test_skill_output_is_wellformed_and_grounded(scenario, isolated_data_dir):
    model = openrouter_eval_model()
    result = _run_scenario(scenario)

    derived = result.get("derived", [])
    assert derived, f"{scenario['name']}: no derived artifact was produced"
    tab = derived[-1]
    assert tab["skill_id"] == scenario["skill_id"], (
        f"{scenario['name']}: routed to {tab['skill_id']}, expected {scenario['skill_id']}"
    )
    body = tab["body"]
    assert body.strip()

    scratch = result.get("scratchpad", {})
    context = (
        f"{scratch.get('body', '')}\n\n"
        f"confirmed facts:\n" + "\n".join(scratch.get("sources", []))
    ).strip()

    threshold = float(scenario.get("threshold", 0.7))
    wellformed = GEval(
        name=f"Well-formed {scenario['skill_id']}",
        criteria=scenario["wellformed_criteria"],
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=model,
        threshold=threshold,
        async_mode=False,
    )
    grounded = FaithfulnessMetric(
        model=model, threshold=threshold, async_mode=False, include_reason=True
    )

    test_case = LLMTestCase(
        input=scenario["build_turn"],
        actual_output=body,
        retrieval_context=[context],
    )
    try:
        assert_test(test_case=test_case, metrics=[wellformed, grounded], run_async=False)
    except Exception as exc:
        write_run_log(scenario, body, "failed", str(exc))
        raise
    write_run_log(scenario, body, "passed")


# A conversational sanity check: a build turn does not derail the scratchpad.
def test_build_turn_leaves_the_scratchpad_alone(isolated_data_dir):
    model = openrouter_eval_model()
    cid = "skill-noderail"
    app.run_conversation(
        user_id=cid,
        conversation_id=cid,
        user_message="Jot: RelayDB — multi-region by default, 9ms p50 reads, SQL-compatible.",
    )
    before = app.run_conversation(
        user_id=cid, conversation_id=cid, user_message="tighten that note"
    )["scratchpad"]["body"]
    after = app.run_conversation(
        user_id=cid, conversation_id=cid, user_message="Generate a blog outline from the scratchpad."
    )
    assert after["scratchpad"]["body"] == before  # untouched by the build
    assert after["derived"]

    tc = ConversationalTestCase(
        scenario="A user jots facts, tightens the note, then asks for a blog outline.",
        expected_outcome=(
            "The build produces a separate outline and does not rewrite, expand, "
            "or otherwise change the scratchpad note."
        ),
        chatbot_role="Scratchpad — skills produce separate derived artifacts and never edit the scratchpad.",
        turns=[
            Turn(role="user", content="tighten that note"),
            Turn(role="assistant", content=before),
            Turn(role="user", content="Generate a blog outline from the scratchpad."),
            Turn(role="assistant", content=after["assistant_message"]),
        ],
    )
    from deepeval.metrics import ConversationalGEval
    from deepeval.test_case import MultiTurnParams

    metric = ConversationalGEval(
        name="Build does not derail the scratchpad",
        criteria=(
            "The assistant's build reply refers to a separate artifact / tab and "
            "does not claim to have edited or replaced the scratchpad note."
        ),
        evaluation_params=[MultiTurnParams.CONTENT],
        model=model,
        threshold=0.7,
        async_mode=False,
    )
    assert_test(test_case=tc, metrics=[metric], run_async=False)
