"""T078: action.yml's public contract, and the workflows around it."""

from pathlib import Path

import yaml

from lgcov.drift_gate import FAIL_ON_VALUES

ROOT = Path(__file__).resolve().parents[1]


def _dict(value: object) -> dict[object, object]:
    assert isinstance(value, dict)
    return value


def _yaml(path: Path) -> dict[object, object]:
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _dict(raw)


def _steps() -> list[dict[object, object]]:
    steps = _dict(_yaml(ROOT / "action.yml")["runs"])["steps"]
    assert isinstance(steps, list)
    return [_dict(s) for s in steps]


def test_action_is_composite_with_the_documented_inputs_and_outputs() -> None:
    action = _yaml(ROOT / "action.yml")
    assert _dict(action["runs"])["using"] == "composite"
    defaults = {name: _dict(spec).get("default") for name, spec in _dict(action["inputs"]).items()}
    assert defaults == {
        "graph-entrypoint": "",
        "cassette-dir": "",
        "taxonomy": "",
        "fail-on": "root-drift",
        "python-version": "3.12",
        "github-token": "${{ github.token }}",
    }
    assert set(_dict(action["outputs"])) == {
        "report-path",
        "root-drift-count",
        "propagated-drift-count",
        "verdict",
    }


def test_fail_on_is_checked_first_with_the_same_values_as_python() -> None:
    first = str(_steps()[0].get("run", ""))
    assert "|".join(FAIL_ON_VALUES) in first
    assert f"fail-on must be one of {', '.join(FAIL_ON_VALUES)}" in first


def test_no_expression_is_interpolated_into_a_shell_script() -> None:
    # Inputs reach scripts through env: only, so a value can't inject shell code.
    for step in _steps():
        assert "${{" not in str(step.get("run", "")), step.get("name")


def test_dogfood_workflow_runs_this_action_on_pull_request_only() -> None:
    path = ROOT / ".github" / "workflows" / "graph-diff.yml"
    workflow = _yaml(path)
    trigger = workflow.get("on", workflow.get(True))  # PyYAML reads a bare `on` as True
    assert set(_dict(trigger)) == {"pull_request"}
    assert "pull_request_target" not in path.read_text(encoding="utf-8")
    steps = _dict(_dict(workflow["jobs"])["report"])["steps"]
    assert isinstance(steps, list)
    assert {"uses": "./"} in steps


def test_release_ignores_the_floating_v0_tag() -> None:
    workflow = _yaml(ROOT / ".github" / "workflows" / "release.yml")
    push = _dict(_dict(workflow.get("on", workflow.get(True)))["push"])
    assert push["tags"] == ["v*.*.*"]


def test_readme_snippet_uses_the_action_on_pull_request() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uses: AMOHYZ/langgraph-regression-guard@v0" in readme
    assert "on: pull_request" in readme
