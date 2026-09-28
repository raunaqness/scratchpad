#!/usr/bin/env bash
# Run Signal's evals locally or in CI (.github/workflows/evals.yml).
#
#   ./run_evals.sh offline   # fake LLM, no key, no network: graph wiring + retrieval calls
#   ./run_evals.sh live      # real model for interpret only; exact assertions, no judge
#   ./run_evals.sh judge     # DeepEval LLM-as-judge tests (slow, noisy, costs more)
#   ./run_evals.sh all       # everything
#
# Extra args go to pytest, e.g. `./run_evals.sh live -k knowledge_base`.
# Tracing to LangSmith/Langfuse is off unless EVAL_TRACING=1.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PYTEST="${PYTEST_BIN:-.venv/bin/pytest}"
[[ -x "$PYTEST" ]] || PYTEST=pytest

mode="${1:-offline}"
shift || true

if [[ "${EVAL_TRACING:-0}" != "1" ]]; then
    export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false LANGFUSE_ENABLED=false
fi

case "$mode" in
    offline)
        # An empty key overrides .env, so nothing can reach a model.
        OPENROUTER_API_KEY= exec "$PYTEST" -q -m "not live" "$@"
        ;;
    live)
        exec "$PYTEST" -q -m live tests/evals "$@"
        ;;
    judge)
        exec "$PYTEST" -q \
            tests/test_conversations.py \
            tests/test_helpful_tone_deepeval.py \
            tests/test_skills_deepeval.py \
            tests/test_recorded_conversations_deepeval.py \
            tests/test_note_formatting_live.py "$@"
        ;;
    all)
        exec "$PYTEST" -q "$@"
        ;;
    *)
        echo "usage: $0 {offline|live|judge|all} [pytest args]" >&2
        exit 2
        ;;
esac
