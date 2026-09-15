"""Standalone (non-pytest) runner for the 3 feedback-derived DeepEval cases.

Unlike `pytest tests/test_recorded_conversations_deepeval.py`, this captures
score + reason for EVERY case, pass or fail, and dumps them as JSON — used to
compare before/after a prompt change across loop iterations.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path("/home/raunaq/signal_v2")
sys.path.insert(0, str(REPO_ROOT))

from deepeval.metrics import ConversationalGEval
from deepeval.test_case import ConversationalTestCase, MultiTurnParams, Turn

from backend.app import run_conversation
from backend.config import settings
from tests.conftest import openrouter_eval_model
from tests.test_recorded_conversations_deepeval import _CASES, _ROLE


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="eval-iter-"))
    settings.data_dir = tmp_dir
    settings.db_path = tmp_dir / "signal.db"

    model = openrouter_eval_model()
    results = []

    for conversation_id, turn_index, user_messages, feedback_comments in _CASES:
        eval_conversation_id = f"eval-{conversation_id}-turn{turn_index}"
        turns: list[Turn] = []
        for message in user_messages:
            result = run_conversation(
                user_id=f"eval-{conversation_id}",
                conversation_id=eval_conversation_id,
                user_message=message,
            )
            turns.append(Turn(role="user", content=message))
            turns.append(Turn(role="assistant", content=result["assistant_message"]))

        final_reply = turns[-1].content
        expected_outcome = (
            "A real user had this exact conversation and reported a problem with "
            "the assistant's FINAL reply specifically (the earlier turns are just "
            "context that led up to it). The corrected system must not reproduce "
            "the reported problem:\n" + "\n---\n".join(feedback_comments)
        )
        metric = ConversationalGEval(
            name="Matches user-reported expectation",
            criteria=(
                "Judge only the assistant's final reply in this conversation. "
                "expected_outcome is a real user's bug report about that exact "
                "reply, in their own words — read it as a description of the "
                "problem to avoid, not a literal script the reply must follow. "
                "Pass only if the final reply does not exhibit the reported "
                "problem."
            ),
            evaluation_params=[MultiTurnParams.CONTENT],
            model=model,
            threshold=0.7,
            async_mode=False,
        )
        test_case = ConversationalTestCase(
            scenario=user_messages[0],
            expected_outcome=expected_outcome,
            chatbot_role=_ROLE,
            turns=turns,
        )
        metric.measure(test_case)
        results.append(
            {
                "conversation_id": conversation_id,
                "turn_index": turn_index,
                "score": metric.score,
                "threshold": metric.threshold,
                "success": bool(metric.score is not None and metric.score >= metric.threshold),
                "reason": metric.reason,
                "final_reply": final_reply,
            }
        )
        print(
            f"turn{turn_index}: score={metric.score:.2f} "
            f"success={metric.score >= metric.threshold}"
        )

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/eval_iteration_result.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
