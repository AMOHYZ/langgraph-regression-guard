#!/usr/bin/env bash
# Stop hook: Claude can't end a turn while lint, format, types or tests fail.
# Exit 2 sends stderr back to Claude as the reason to keep going; exit 0 lets it stop.
# Skipped when the tree has no uncommitted changes (commits already ran pre-commit).
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}" || exit 0

if git diff --quiet HEAD -- && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  exit 0
fi

run() {
  local out
  if ! out=$("$@" 2>&1); then
    {
      printf 'Stop hook: `%s` failed. Fix it before finishing.\n\n' "$*"
      printf '%s\n' "$out" | tail -n 60
    } >&2
    exit 2
  fi
}

run uv run ruff check .
run uv run ruff format --check .
run uv run mypy --strict src tests
run uv run pytest -q
exit 0
