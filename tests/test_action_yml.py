"""T078: action.yml's public contract, and the workflows around it."""

import os
import subprocess
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
        "live": "false",
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


def test_dogfood_workflow_runs_on_prs_and_on_pushes_to_main() -> None:
    path = ROOT / ".github" / "workflows" / "graph-diff.yml"
    workflow = _yaml(path)
    trigger = _dict(workflow.get("on", workflow.get(True)))  # PyYAML reads `on` as True
    assert set(trigger) == {"pull_request", "push"}
    assert _dict(trigger["push"])["branches"] == ["main"]  # main uploads the coverage baseline
    assert "pull_request_target" not in path.read_text(encoding="utf-8")
    assert _dict(workflow["permissions"])["actions"] == "read"  # to download that baseline
    steps = _dict(_dict(workflow["jobs"])["report"])["steps"]
    assert isinstance(steps, list)
    assert {"uses": "./"} in steps


def test_coverage_artifact_is_uploaded_on_main_and_downloaded_on_prs() -> None:
    steps = {str(s.get("name")): s for s in _steps()}
    upload = steps["Upload coverage for main"]
    assert str(upload["uses"]).startswith("actions/upload-artifact@")
    assert _dict(upload["with"])["name"] == "lgcov-coverage"
    assert "push" in str(upload["if"]) and "default_branch" in str(upload["if"])
    assert "pull_request" in str(steps["Download main's coverage"]["if"])


def test_live_input_reaches_lgcov_ci() -> None:
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    assert _dict(run["env"])["LIVE"] == "${{ inputs.live }}"
    assert "--live" in str(run["run"])


def test_release_ignores_the_floating_v0_tag() -> None:
    workflow = _yaml(ROOT / ".github" / "workflows" / "release.yml")
    push = _dict(_dict(workflow.get("on", workflow.get(True)))["push"])
    assert push["tags"] == ["v*.*.*"]


def test_readme_snippet_uses_the_action_on_pull_request() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uses: AMOHYZ/langgraph-regression-guard@v0" in readme
    assert "  pull_request:\n" in readme
    assert "actions: read" in readme


# Regression: Actions runs `shell: bash` as `bash --noprofile --norc -e -o pipefail`, so
# lgcov's exit 1 (drift) or a failed install killed the step before the PR comment and
# the verdict gate could run. These steps must handle exit codes themselves.
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c"]


def _step(name: str) -> str:
    for step in _steps():
        if step.get("name") == name:
            return str(step["run"])
    raise AssertionError(f"no step named {name!r}")


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_run_step_survives_drift_under_github_bash_flags(tmp_path: Path) -> None:
    # A fake `lgcov ci` that writes its report and outputs, then exits 1 for drift.
    _executable(
        tmp_path / ".venv" / "bin" / "lgcov",
        'mkdir -p "$(dirname "$3")"; echo report > "$3"; echo exit-code=1 >> "$5"; exit 1\n',
    )
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path / "tmp"),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
        "GRAPH": "",
        "CASSETTE_DIR": "",
        "TAXONOMY": "",
        "FAIL_ON": "root-drift",
        "BASE_DIR": "",
        "HARNESS_ERROR": "",
        "LIVE": "false",
        "COVERAGE_BASELINE": "",
    }
    script = _step("Run lgcov")
    result = subprocess.run(
        [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "exit-code=1" in out.read_text(encoding="utf-8")


def test_install_step_records_a_failed_install_under_github_bash_flags(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "uv", "echo 'lockfile out of date' >&2; exit 1\n")
    (tmp_path / "uv.lock").touch()
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
    }
    script = _step("Install your project and lgcov")
    result = subprocess.run(
        [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    outputs = out.read_text(encoding="utf-8")
    assert "harness-error<<LGCOV_EOF" in outputs
    assert "lockfile out of date" in outputs


def test_coverage_download_failure_is_a_warning_under_github_bash_flags(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "gh", "echo 'HTTP 403: Resource not accessible' >&2; exit 1\n")
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "DEFAULT_BRANCH": "main",
    }
    script = _step("Download main's coverage")
    result = subprocess.run(
        [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "::warning" in result.stdout
    assert "path=" not in out.read_text(encoding="utf-8")


def test_ci_runs_the_harness_selfcheck_on_every_push() -> None:
    # T074: a separate job, so a harness that misclassifies outcomes fails CI.
    workflow = _yaml(ROOT / ".github" / "workflows" / "ci.yml")
    assert "push" in _dict(workflow.get("on", workflow.get(True)))
    steps = _dict(_dict(workflow["jobs"])["selfcheck"])["steps"]
    assert isinstance(steps, list)
    assert any("lgcov selfcheck" in str(_dict(s).get("run", "")) for s in steps)
