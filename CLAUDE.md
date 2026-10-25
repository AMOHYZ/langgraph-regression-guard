# CLAUDE.md: rules for agents working in this repo

langgraph-regression-guard: catch silent regressions in LangGraph agents on
every PR. Python 3.12, managed with uv.

## Commands
- Install: `uv sync`
- Test: `uv run pytest -q`
- Lint and format: `uv run ruff check --fix . && uv run ruff format .`
- Types: `uv run mypy --strict src tests`
- Everything before a commit: `uv run pre-commit run --all-files`

## 1. Tests first
- Write or update a failing test before you change behaviour, and show it failing.
- Then write the smallest code that makes it pass. Do not change a test just to make it pass.
- Every bug fix comes with a regression test.
- Tests live in `tests/`, mirror the module name (`test_<module>.py`), and run offline:
  no network calls and no real LLM calls. Use fixtures or fakes.

## 2. Small diffs
- One concern per change. Aim for fewer than about 200 changed lines.
- Do not reformat, rename or reorganise code you were not asked to touch.
- No new dependencies without asking first. Say why it is needed.
- Before finishing, list the files you changed and the commands you ran, with their results.

## 3. Strict typing
- Everything passes `mypy --strict`. Type every function signature, including tests.
- No `Any`, no `# type: ignore` without a comment explaining why.
- Prefer frozen `dataclass`es or `TypedDict` for data passed between functions.
- Keep core logic pure (inputs in, values out); keep I/O at the edges.

## 4. No secrets
- Never commit API keys, tokens, `.env` files or credentials. Read them from environment variables.
- Never print or log secret values. `.env` stays in `.gitignore`.
- No employer code or data in this repo. Personal accounts and public examples only.

## Layout
- `src/lgcov/`: library code
- `tests/`: pytest tests
- `tests/fixtures/`: small example graphs and snapshots (JSON)
