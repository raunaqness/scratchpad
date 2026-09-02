#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTEST_BIN="${PYTEST_BIN:-"$ROOT_DIR/.venv/bin/pytest"}"
RUNS_DIR="${DEEPEVAL_RUNS_DIR:-"$ROOT_DIR/.deepeval-runs"}"
RUN_ID="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_DIR="$RUNS_DIR/$RUN_ID"
JSONL_LOG="$ROOT_DIR/data/logs/deepeval-runs.jsonl"

mkdir -p "$RUN_DIR"

if [[ -f "$JSONL_LOG" ]]; then
    log_lines_before="$(wc -l < "$JSONL_LOG")"
else
    log_lines_before=0
fi

{
    printf 'run_id=%s\n' "$RUN_ID"
    printf 'started_at=%s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf 'command=%q ' "$PYTEST_BIN"
    printf '%q ' -q -s tests/test_conversations.py tests/test_product_deepeval.py
    printf '\n'
} > "$RUN_DIR/run-info.txt"

set +e
"$PYTEST_BIN" -q -s \
    tests/test_conversations.py \
    tests/test_product_deepeval.py \
    --maxfail=7 2>&1 | tee "$RUN_DIR/pytest.log"
pytest_status="${PIPESTATUS[0]}"
set -e

if [[ -f "$JSONL_LOG" ]]; then
    awk -v skip="$log_lines_before" 'NR > skip' \
        "$JSONL_LOG" > "$RUN_DIR/deepeval-runs.jsonl"
else
    : > "$RUN_DIR/deepeval-runs.jsonl"
fi

{
    printf 'finished_at=%s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf 'exit_code=%s\n' "$pytest_status"
    printf 'results_dir=%s\n' "$RUN_DIR"
} >> "$RUN_DIR/run-info.txt"

printf '\nDeepEval run results: %s\n' "$RUN_DIR"
exit "$pytest_status"
