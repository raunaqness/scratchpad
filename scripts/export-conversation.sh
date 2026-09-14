#!/usr/bin/env bash
# Export one conversation thread (turns, trajectory, feedback) into
# tests/fixtures/conversations/<conversation_id>.json.
#
# Runs backend/export_conversation.py inside the dev backend container,
# where the DB and Langfuse connections actually live. tests/ is bind-mounted
# there (docker-compose.test.yml), so the file lands straight on this repo —
# no docker cp needed.
#
# Usage:
#   scripts/export-conversation.sh <user_id> <conversation_id> [--out PATH]
#
# Find a conversation_id: check the thread sidebar in /app, or
#   docker compose -f docker-compose.test.yml exec -T scratchpad-test-backend \
#     python -c "
#   import asyncio
#   from backend.threads_store import list_threads
#   asyncio.run(list_threads('<user_id>', limit=5))" 2>/dev/null

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <user_id> <conversation_id> [--out PATH]" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yml"
SERVICE="scratchpad-test-backend"

if ! docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "error: $SERVICE isn't running — start it with:" >&2
  echo "  docker compose -f $COMPOSE_FILE up -d $SERVICE" >&2
  exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
  python -m backend.export_conversation "$@"
