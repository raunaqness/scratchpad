#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODUCTION_BRANCH="${PRODUCTION_BRANCH:-main}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-signal_v2}"

cd "$REPO_ROOT"

current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "$PRODUCTION_BRANCH" ]]; then
  printf 'Refusing production rebuild: currently on "%s", expected "%s".\n' \
    "$current_branch" "$PRODUCTION_BRANCH" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  printf 'Refusing production rebuild: working tree is not clean.\n' >&2
  exit 1
fi

git pull --ff-only origin "$PRODUCTION_BRANCH"

docker compose \
  -p "$COMPOSE_PROJECT_NAME" \
  up -d --build

docker compose \
  -p "$COMPOSE_PROJECT_NAME" \
  ps
