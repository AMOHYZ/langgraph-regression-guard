"""T186: review/action.yml and the workflow that runs it, read as files. The token reaches only
the steps that read and write GitHub; the step that runs the command holds none. lgcov comes from
the Action's own lock with no cache and no uv config, and nothing from the event or the comment
reaches a script (docs/review.md, "How it runs")."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION: dict[str, object] = yaml.safe_load(
    (ROOT / "review" / "action.yml").read_text(encoding="utf-8")
)
WORKFLOW = (ROOT / ".github" / "workflows" / "lgcov-review.yml").read_text(encoding="utf-8")
REVIEW_DOC = (ROOT / "docs" / "review.md").read_text(encoding="utf-8")
DOC_YAML = REVIEW_DOC.split("```yaml\n", 1)[1].split("```", 1)[0]


def _steps() -> list[dict[str, object]]:
    runs = ACTION["runs"]
    assert isinstance(runs, dict) and runs["using"] == "composite"
    steps: list[dict[str, object]] = runs["steps"]
    return steps


def _step(command: str) -> dict[str, object]:
    """The one step whose script runs `lgcov review <command>`."""
    found = [s for s in _steps() if f"review {command}" in str(s.get("run", ""))]
    assert len(found) == 1, command
    return found[0]


def _env(step: dict[str, object]) -> dict[str, str]:
    env = step.get("env", {})
    assert isinstance(env, dict)
    return {str(k): str(v) for k, v in env.items()}


def test_the_token_reaches_fetch_and_commit_only() -> None:
    holders = [s for s in _steps() if any("token" in v for v in _env(s).values())]
    assert holders == [_step("fetch"), _step("commit")]
    for step in holders:
        assert _env(step)["GH_TOKEN"] == "${{ inputs.github-token }}"
    assert not any("token" in str(v).lower() for v in _env(_step("apply")).values())


def test_lgcov_comes_from_the_action_s_lock_with_no_cache_and_no_uv_config() -> None:
    setup = [s for s in _steps() if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert len(setup) == 1
    assert setup[0]["with"] == {"enable-cache": False}
    install = [s for s in _steps() if "uv sync" in str(s.get("run", ""))]
    assert len(install) == 1
    script = str(install[0]["run"])
    assert '--no-config --project "$ACTION_PATH/.." --locked --no-dev' in script
    assert install[0]["working-directory"] == "${{ runner.temp }}"
    assert "uvx" not in yaml.safe_dump(ACTION)


def test_no_script_holds_an_expression_and_none_reads_the_event() -> None:
    """Values reach a script through env, so none is pasted into it; and the event's fields,
    the comment's above all, reach lgcov only through $GITHUB_EVENT_PATH."""
    for step in _steps():
        assert "${{" not in str(step.get("run", "")), step.get("name")
    assert "github.event" not in yaml.safe_dump(ACTION)
    assert '--event "$GITHUB_EVENT_PATH"' in str(_step("fetch")["run"])


def test_the_steps_run_in_order_and_commit_answers_unless_install_failed() -> None:
    names = [str(s.get("run", "")) for s in _steps()]
    order = [
        i for c in ("fetch", "apply", "commit") for i, r in enumerate(names) if f"review {c}" in r
    ]
    assert order == sorted(order)
    assert "if" not in _step("apply")  # it runs only when fetch succeeded
    assert _step("commit")["if"] == "${{ !cancelled() && steps.install.outcome == 'success' }}"


def test_every_input_has_a_description_and_the_token_defaults_to_the_job_s() -> None:
    inputs = ACTION["inputs"]
    assert isinstance(inputs, dict)
    assert set(inputs) == {"github-token", "taxonomy", "cassette-dir", "python-version"}
    assert all(spec.get("description") for spec in inputs.values())
    assert inputs["github-token"]["default"] == "${{ github.token }}"


def test_this_repo_s_review_workflow_is_the_doc_s() -> None:
    assert yaml.safe_load(WORKFLOW) == yaml.safe_load(DOC_YAML)


def test_the_workflow_passes_only_inputs_the_action_has() -> None:
    inputs = ACTION["inputs"]
    assert isinstance(inputs, dict)
    passed = re.findall(r"^\s{10}([a-z-]+):", WORKFLOW.split("uses: ./review", 1)[1], re.M)
    assert passed and set(passed) <= set(inputs)


def test_the_taxonomy_input_reaches_fetch_through_env() -> None:
    fetch = _step("fetch")
    assert _env(fetch)["TAXONOMY"] == "${{ inputs.taxonomy }}"
    assert '[ -n "$TAXONOMY" ] && args+=(--taxonomy "$TAXONOMY")' in str(fetch["run"])
