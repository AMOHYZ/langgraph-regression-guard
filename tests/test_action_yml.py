"""T078: action.yml's public contract, and the workflows around it."""

import base64
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from lgcov.drift_gate import FAIL_ON_VALUES
from lgcov.verdict_actions import CI_STATUSES, MODES, Evidence, decide_action

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
        "fail-on-inconclusive": "false",
        "on-divergence": "flag",
        "budget-seconds": "",
        "tier": "",  # T089: empty is lgcov.yaml's tier, else pr
        "mode": "enforcing",  # T153
        "approved-by": "",
        "ci-status": "",
        "phase": "all",  # T179
    }
    assert set(_dict(action["outputs"])) == {
        "report-path",
        "root-drift-count",
        "propagated-drift-count",
        "inconclusive-count",  # T162
        "first-drift",
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
    jobs = _dict(workflow["jobs"])
    candidate, report = _dict(jobs["candidate"]), _dict(jobs["report"])
    # T179: the job that decides checks out the base, so `uses: ./` is the base's action.yml, and it
    # holds what the candidate job doesn't.
    assert report["needs"] == "candidate"
    assert report["if"] == "${{ !cancelled() }}"
    assert _dict(report["permissions"])["pull-requests"] == "write"
    assert _dict(report["permissions"])["actions"] == "read"  # to download that baseline
    # The demo's KPIs, so live runs here reach the KPI gate (L2). Both jobs get the same inputs.
    taxonomy = "examples/demo_agent/taxonomy.yaml"
    assert (ROOT / taxonomy).is_file()
    assert report["steps"] == [
        {
            "uses": "actions/checkout@v4",
            "with": {"ref": "${{ github.event.pull_request.base.sha }}"},
        },
        {"uses": "./", "with": {"phase": "evaluate", "taxonomy": taxonomy}},
    ]
    assert candidate["permissions"] == {"contents": "read"}
    assert candidate["steps"] == [
        {"uses": "actions/checkout@v4", "with": {"persist-credentials": False}},
        {"uses": "./", "with": {"phase": "candidate", "taxonomy": taxonomy}},
    ]
    assert "permissions" not in workflow


def test_the_temporary_two_job_workflow_is_gone() -> None:
    assert not (ROOT / ".github" / "workflows" / "two-jobs.yml").exists()


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


def test_fail_on_inconclusive_is_checked_first_and_reaches_lgcov_ci() -> None:
    # T091: INCONCLUSIVE exits 0 unless this is true; then 1. T153: an infra error still exits 2.
    assert "fail-on-inconclusive must be true or false" in str(_steps()[0].get("run", ""))
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    assert _dict(run["env"])["FAIL_ON_INCONCLUSIVE"] == "${{ inputs.fail-on-inconclusive }}"
    assert "--fail-on-inconclusive" in str(run["run"])


def test_divergence_and_budget_inputs_are_checked_first_and_reach_lgcov_ci() -> None:
    # T089: a served node whose input diverged, and a wall-clock budget for live calls.
    first = str(_steps()[0].get("run", ""))
    assert "on-divergence must be one of fail, flag, live" in first
    assert "budget-seconds must be a number of seconds" in first
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    env = _dict(run["env"])
    assert env["ON_DIVERGENCE"] == "${{ inputs.on-divergence }}"
    assert env["BUDGET_SECONDS"] == "${{ inputs.budget-seconds }}"
    assert "--on-divergence" in str(run["run"]) and "--budget-seconds" in str(run["run"])


def test_mode_and_ci_status_are_checked_first_with_the_same_values_as_python() -> None:
    first = str(_steps()[0].get("run", ""))
    assert "|".join(MODES) in first
    assert f"mode must be one of {', '.join(MODES)}" in first
    assert "|".join(CI_STATUSES) in first
    assert f"ci-status must be one of {', '.join(CI_STATUSES)}, or empty" in first


def test_mode_approval_and_ci_status_reach_lgcov_ci() -> None:
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    env = _dict(run["env"])
    assert env["MODE"] == "${{ inputs.mode }}"
    assert env["APPROVED_BY"] == "${{ inputs.approved-by }}"
    assert env["CI_STATUS"] == "${{ inputs.ci-status }}"
    for flag in ("--mode", "--approved-by", "--ci-status"):
        assert flag in str(run["run"])


def test_the_fallback_report_uses_the_infra_headline() -> None:
    # When lgcov can't even start, the Action writes this one comment itself.
    headline = decide_action(Evidence(infra=True)).headline
    assert f'"## {headline}"' in _step("Run lgcov")


def test_no_decision_adds_a_warning_because_the_tick_can_still_be_green(tmp_path: Path) -> None:
    script = _step("Exit with the verdict")
    for verdict, code, warned in (("needs_human", "0", True), ("pass", "0", False)):
        env = {**os.environ, "CODE": code, "VERDICT": verdict, "FAIL_ON": "root-drift"}
        result = subprocess.run(
            [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert ("::warning title=lgcov::No decision: needs_human" in result.stdout) == warned


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
        "FAIL_ON_INCONCLUSIVE": "false",
        "ON_DIVERGENCE": "flag",
        "BUDGET_SECONDS": "",
        "TIER": "pr",
        "NIGHTLY_URL": "",
        "COVERAGE_BASELINE": "",
        "MODE": "enforcing",
        "APPROVED_BY": "",
        "CI_STATUS": "",
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


def test_tier_input_is_checked_first_and_reaches_lgcov_ci() -> None:
    # T089 PR B: pr on every push; nightly runs everything live, on a schedule.
    assert "tier must be pr or nightly" in str(_steps()[0].get("run", ""))
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    assert _dict(run["env"])["TIER"] == "${{ inputs.tier }}"
    assert "--tier" in str(run["run"])


def test_the_nightly_report_is_uploaded_and_prs_link_main_s_latest() -> None:
    steps = {str(s.get("name")): s for s in _steps()}
    upload = steps["Upload the nightly report"]
    assert str(upload["uses"]).startswith("actions/upload-artifact@")
    assert _dict(upload["with"])["name"] == "lgcov-nightly"
    assert "nightly" in str(upload["if"])
    assert "pull_request" in str(steps["Find main's latest nightly run"]["if"])
    run = steps["Run lgcov"]
    assert _dict(run["env"])["NIGHTLY_URL"] == "${{ steps.nightly.outputs.url }}"
    assert "--nightly-url" in str(run["run"])


def test_no_nightly_run_yet_is_fine_under_github_bash_flags(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "gh", "echo 'HTTP 404: Not Found' >&2; exit 1\n")
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
    script = _step("Find main's latest nightly run")
    result = subprocess.run(
        [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "url=" not in out.read_text(encoding="utf-8")


def test_ci_runs_the_harness_selfcheck_on_every_push() -> None:
    # T074: a separate job, so a harness that misclassifies outcomes fails CI.
    workflow = _yaml(ROOT / ".github" / "workflows" / "ci.yml")
    assert "push" in _dict(workflow.get("on", workflow.get(True)))
    steps = _dict(_dict(workflow["jobs"])["selfcheck"])["steps"]
    assert isinstance(steps, list)
    assert any("lgcov selfcheck" in str(_dict(s).get("run", "")) for s in steps)


# T154: on a PR the gate's policy comes from the base commit, and never from the PR's checkout.
def _run_step_calls(tmp_path: Path, event: str, base_dir: str) -> list[str]:
    """Run the `Run lgcov` step with fake lgcovs: the program it called, then its arguments."""
    record = 'printf "%s\\n" "$0" "$@" > "$RUNNER_TEMP/called"\n'
    _executable(tmp_path / ".venv" / "bin" / "lgcov", record)
    _executable(tmp_path / "bin" / "uvx", record)
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
        "EVENT_NAME": event,
        "BASE_DIR": base_dir,
        "HARNESS_ERROR": "",
        "LIVE": "false",
        "FAIL_ON_INCONCLUSIVE": "false",
        "ON_DIVERGENCE": "flag",
        "FAIL_ON": "root-drift",
        **dict.fromkeys(("GRAPH", "CASSETTE_DIR", "TAXONOMY", "BUDGET_SECONDS", "TIER"), ""),
        **dict.fromkeys(("NIGHTLY_URL", "COVERAGE_BASELINE", "APPROVED_BY", "CI_STATUS"), ""),
        "MODE": "enforcing",
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Run lgcov")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "called").read_text(encoding="utf-8").splitlines()


def test_a_pr_run_reads_its_policy_from_the_base_checkout(tmp_path: Path) -> None:
    called = _run_step_calls(tmp_path, "pull_request", "/base")
    assert called[0] == ".venv/bin/lgcov"
    assert called[called.index("--policy-dir") + 1] == "/base"
    assert called[called.index("--base-dir") + 1] == "/base"


def test_a_pr_run_with_no_base_checkout_gives_no_verdict(tmp_path: Path) -> None:
    called = _run_step_calls(tmp_path, "pull_request", "")
    assert called[0].endswith("/uvx")  # no project code runs for a report that says this
    assert "--policy-dir" not in called
    assert "gate has no policy it can trust" in called[called.index("--harness-error") + 1]


def test_a_push_has_no_base_and_reads_its_own_policy(tmp_path: Path) -> None:
    called = _run_step_calls(tmp_path, "push", "")
    assert called[0] == ".venv/bin/lgcov"
    assert "--policy-dir" not in called and "--harness-error" not in called


def test_the_run_step_is_told_the_event() -> None:
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    assert _dict(run["env"])["EVENT_NAME"] == "${{ github.event_name }}"


# T154: the verify step sits between the run and the comment, and rewrites the report in place,
# so the comment step needs no change and a result that doesn't bind posts no verdict.
def test_the_result_is_verified_after_the_run_and_before_the_comment() -> None:
    names = [str(s.get("name")) for s in _steps()]
    run, verify = names.index("Run lgcov"), names.index("Verify the result")
    assert verify == run + 1
    assert names[verify + 1] == "Post or update the PR comment"
    step = _steps()[verify]
    assert "pull_request" in str(step["if"]) and "'2'" in str(step["if"])  # 2 has no verdict
    env = _dict(step["env"])
    assert env["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "--result-out" in _step("Run lgcov")


def test_the_run_exits_with_the_verified_verdict() -> None:
    verified = "${{ steps.verify.outputs.%s || steps.lgcov.outputs.%s }}"
    exit_env = _dict(_steps()[-1]["env"])
    assert exit_env["CODE"] == verified % ("exit-code", "exit-code")
    assert exit_env["VERDICT"] == verified % ("verdict", "verdict")
    output = _dict(_dict(_yaml(ROOT / "action.yml")["outputs"])["verdict"])
    assert output["value"] == verified % ("verdict", "verdict")


def test_fork_prs_keep_the_job_summary_fallback() -> None:
    comment = {str(s.get("name")): s for s in _steps()}["Post or update the PR comment"]
    script = str(_dict(comment["with"])["script"])
    assert "core.summary.addRaw(body).write()" in script
    assert "PRs from forks get a read-only token" in script


def _verify_step(tmp_path: Path, fake_uvx: str) -> tuple[str, str]:
    """Run the verify step with a fake lgcov: the outputs it set, and the report it left."""
    _executable(tmp_path / "bin" / "uvx", fake_uvx)
    out, report = tmp_path / "github_output", tmp_path / "report.md"
    out.touch()
    report.write_text("## 🟢 lgcov: pass\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
        "REPORT_PATH": str(report),
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
        "BASE_DIR": "/base",
        "TAXONOMY": "",
        "FAIL_ON": "root-drift",
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Verify the result")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return out.read_text(encoding="utf-8"), report.read_text(encoding="utf-8")


def test_a_result_that_cannot_be_verified_gives_no_verdict(tmp_path: Path) -> None:
    outputs, report = _verify_step(tmp_path, "echo 'no network' >&2; exit 1\n")
    assert "exit-code=2\n" in outputs and "verdict=error\n" in outputs
    assert "🟢" not in report and "could not be verified" in report


def test_the_verify_step_passes_on_what_lgcov_verified(tmp_path: Path) -> None:
    fake = (
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--github-output" ]; then\n'
        '    printf "exit-code=1\\nverdict=fail\\n" >> "$2"\n'
        "  fi\n"
        "  shift\n"
        "done\n"
    )
    outputs, report = _verify_step(tmp_path, fake)
    assert outputs == "exit-code=1\nverdict=fail\n"
    assert report == "## 🟢 lgcov: pass\n"  # lgcov verified it, so the step leaves it


# T179: two jobs, so the PR's code never runs where the verdict is decided. A composite Action can't
# define jobs, so the workflow declares them and tells each which half to run.
PHASES = {
    "all": [
        "Check inputs",
        "Install uv",
        "Check out the commits",
        "Install your project and lgcov",
        "Download main's coverage",
        "Find main's latest nightly run",
        "Run lgcov",
        "Verify the result",
        "Post or update the PR comment",
        "Upload coverage for main",
        "Upload the nightly report",
        "Exit with the verdict",
    ],
    # The fetch comes first because it is the one step with a token it can use: from the install
    # on, the PR's code can run, and it could wrap `git` for any step after that.
    "candidate": [
        "Check inputs",
        "Install uv",
        "Check out the commits",
        "Install your project and lgcov",
        "Run lgcov",
        "Upload what this job did",
    ],
    "evaluate": [
        "Check inputs",
        "Install uv",
        "Check out the commits",
        "Install lgcov for this job",
        "Download main's coverage",
        "Find main's latest nightly run",
        "Download what the candidate job did",
        "Run lgcov",
        "Verify the result",
        "Post or update the PR comment",
        "Upload coverage for main",
        "Upload the nightly report",
        "Exit with the verdict",
    ],
}


def _runs_in(step: dict[object, object], phase: str) -> bool:
    wanted = re.findall(r"inputs\.phase (==|!=) '(\w+)'", str(step.get("if", "")))
    return all((phase == named) == (op == "==") for op, named in wanted)


@pytest.mark.parametrize("phase", PHASES)
def test_each_phase_runs_its_own_steps_in_order(phase: str) -> None:
    assert [str(s["name"]) for s in _steps() if _runs_in(s, phase)] == PHASES[phase]


def test_phase_is_checked_first() -> None:
    first = str(_steps()[0].get("run", ""))
    assert "all|candidate|evaluate" in first
    assert "phase must be one of all, candidate, evaluate" in first


def test_a_bad_phase_stops_the_run(tmp_path: Path) -> None:
    env = {
        **os.environ,
        **dict.fromkeys(("LIVE", "FAIL_ON_INCONCLUSIVE"), "false"),
        **dict.fromkeys(("BUDGET_SECONDS", "TIER", "CI_STATUS"), ""),
        "FAIL_ON": "root-drift",
        "ON_DIVERGENCE": "flag",
        "MODE": "enforcing",
        "PHASE": "verdict",
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Check inputs")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "phase must be one of all, candidate, evaluate; got 'verdict'" in result.stdout


def test_the_job_that_decides_restores_no_cache() -> None:
    # Caches are shared within a PR, so the candidate job could leave a poisoned one for this job.
    uv = {str(s.get("name")): s for s in _steps()}["Install uv"]
    assert _dict(uv["with"])["enable-cache"] == "${{ inputs.phase != 'evaluate' }}"


def test_the_job_that_decides_installs_lgcov_pinned_and_away_from_the_checkouts() -> None:
    # A PR's uv.toml or pyproject.toml could name another index, and this job holds the keys.
    step = {str(s.get("name")): s for s in _steps()}["Install lgcov for this job"]
    assert step["working-directory"] == "${{ runner.temp }}"
    run = str(step["run"])
    assert "uv sync --no-config --project " in run
    assert "--locked" in run and "--no-dev" in run
    assert _dict(step["env"])["UV_PROJECT_ENVIRONMENT"] == "${{ runner.temp }}/lgcov-venv"


def test_the_upload_is_read_back_by_the_other_job() -> None:
    steps = {str(s.get("name")): s for s in _steps()}
    up, down = (
        _dict(steps["Upload what this job did"]["with"]),
        _dict(steps["Download what the candidate job did"]["with"]),
    )
    assert up["name"] == down["name"] == "lgcov-candidate"
    assert up["path"] == "${{ runner.temp }}/lgcov/candidate.json"
    assert '--candidate-out "$RUNNER_TEMP/lgcov/candidate.json"' in _step("Run lgcov")
    assert down["path"] == "${{ runner.temp }}/lgcov-candidate"
    assert '--candidate-in "$RUNNER_TEMP/lgcov-candidate/candidate.json"' in _step("Run lgcov")
    assert (
        up["retention-days"] == 1
    )  # live outputs, readable by whoever can read the repo's Actions
    assert up["overwrite"] is True  # a re-run replaces it


def test_no_upload_is_for_lgcov_to_report() -> None:
    # A missing file is no verdict, exit 2, and the comment says so. It can't stop the run here.
    steps = {str(s.get("name")): s for s in _steps()}
    assert steps["Download what the candidate job did"]["continue-on-error"] is True
    assert steps["Upload what this job did"]["continue-on-error"] is True


FAKE_LGCOV = 'printf "%s\\n" "$0" "$PWD" "$@" > "$RUNNER_TEMP/called"\n'


def _phase_run(
    tmp_path: Path, phase: str, pr_dir: str = "", harness_error: str = ""
) -> tuple[list[str], str]:
    """Run the `Run lgcov` step in a phase with fake lgcovs: the program it called, the folder it
    called it from and its arguments, then the outputs it set."""
    _executable(tmp_path / ".venv" / "bin" / "lgcov", FAKE_LGCOV)
    _executable(tmp_path / "bin" / "uvx", FAKE_LGCOV)
    evaluator = tmp_path / "evaluator" / "lgcov"
    _executable(evaluator, FAKE_LGCOV)
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
        "EVENT_NAME": "pull_request",
        "BASE_DIR": "/base",
        "HARNESS_ERROR": harness_error,
        "LIVE": "false",
        "FAIL_ON_INCONCLUSIVE": "false",
        "ON_DIVERGENCE": "flag",
        "FAIL_ON": "root-drift",
        **dict.fromkeys(("GRAPH", "CASSETTE_DIR", "TAXONOMY", "BUDGET_SECONDS", "TIER"), ""),
        **dict.fromkeys(("NIGHTLY_URL", "COVERAGE_BASELINE", "APPROVED_BY", "CI_STATUS"), ""),
        "MODE": "enforcing",
        "PHASE": phase,
        "PR_DIR": pr_dir,
        "EVALUATOR": str(evaluator),
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Run lgcov")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    called = tmp_path / "called"
    lines = called.read_text(encoding="utf-8").splitlines() if called.exists() else []
    return lines, out.read_text(encoding="utf-8")


def test_the_candidate_phase_runs_the_pr_s_code_and_decides_nothing(tmp_path: Path) -> None:
    called, outputs = _phase_run(tmp_path, "candidate")
    assert called[0] == ".venv/bin/lgcov"
    assert called[called.index("--candidate-out") + 1] == f"{tmp_path}/lgcov/candidate.json"
    assert called[called.index("--policy-dir") + 1] == "/base"
    assert "--candidate-in" not in called
    assert outputs == ""  # no fallback report: the other job says what went wrong


def test_a_failed_install_in_the_candidate_job_reaches_the_other_job(tmp_path: Path) -> None:
    called, _ = _phase_run(tmp_path, "candidate", harness_error="Installing failed")
    assert called[0].endswith("/uvx")  # no project to run: lgcov alone can write the file
    assert "--candidate-out" in called
    assert called[called.index("--harness-error") + 1] == "Installing failed"


def test_the_evaluate_phase_reads_the_pr_as_data_with_lgcov_from_the_action(tmp_path: Path) -> None:
    data = tmp_path / "pr"
    data.mkdir()
    called, _ = _phase_run(tmp_path, "evaluate", str(data))
    assert called[0] == f"{tmp_path}/evaluator/lgcov"  # never .venv/bin/lgcov
    assert Path(called[1]).resolve() == data.resolve()
    assert called[called.index("--candidate-in") + 1] == (
        f"{tmp_path}/lgcov-candidate/candidate.json"
    )
    assert called[called.index("--policy-dir") + 1] == "/base"
    assert "--candidate-out" not in called and "--harness-error" not in called


def test_the_evaluate_phase_with_no_pr_commit_gives_no_verdict(tmp_path: Path) -> None:
    called, _ = _phase_run(tmp_path, "evaluate")
    assert called[0] == f"{tmp_path}/evaluator/lgcov"
    assert "could not read the PR's commit" in called[called.index("--harness-error") + 1]


def test_the_evaluate_phase_with_no_way_into_the_pr_s_commit_gives_no_verdict(
    tmp_path: Path,
) -> None:
    called, _ = _phase_run(tmp_path, "evaluate", str(tmp_path / "no-such-folder"))
    assert Path(called[1]).resolve() == tmp_path.resolve()
    assert "could not read the PR's commit" in called[called.index("--harness-error") + 1]


def test_the_evaluate_phase_with_no_lgcov_gives_no_verdict(tmp_path: Path) -> None:
    data = tmp_path / "pr"
    data.mkdir()
    _executable(tmp_path / "evaluator" / "lgcov", "exit 127\n")
    script = _step("Run lgcov").replace('"$EVALUATOR"', f'"{tmp_path}/no/such/lgcov"')
    out = tmp_path / "github_output"
    out.touch()
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "EVENT_NAME": "pull_request",
        "BASE_DIR": "/base",
        "HARNESS_ERROR": "",
        "LIVE": "false",
        "FAIL_ON_INCONCLUSIVE": "false",
        "ON_DIVERGENCE": "flag",
        "FAIL_ON": "root-drift",
        **dict.fromkeys(("GRAPH", "CASSETTE_DIR", "TAXONOMY", "BUDGET_SECONDS", "TIER"), ""),
        **dict.fromkeys(("NIGHTLY_URL", "COVERAGE_BASELINE", "APPROVED_BY", "CI_STATUS"), ""),
        "MODE": "enforcing",
        "PHASE": "evaluate",
        "PR_DIR": str(data),
    }
    result = subprocess.run(
        [*GITHUB_BASH, script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    outputs = out.read_text(encoding="utf-8")
    assert "exit-code=2\n" in outputs and "verdict=error\n" in outputs
    assert "lgcov could not start" in (tmp_path / "lgcov" / "report.md").read_text(encoding="utf-8")


def test_the_evaluate_phase_is_told_where_the_pr_and_lgcov_are() -> None:
    run = {str(s.get("name")): s for s in _steps()}["Run lgcov"]
    env = _dict(run["env"])
    assert env["PHASE"] == "${{ inputs.phase }}"
    assert env["PR_DIR"] == "${{ steps.base.outputs.pr-dir }}"
    assert env["EVALUATOR"] == "${{ runner.temp }}/lgcov-venv/bin/lgcov"


def test_the_verify_step_is_told_the_phase_and_where_lgcov_is() -> None:
    # Without them it would still work, through uvx, with no lock behind what it installs.
    verify = {str(s.get("name")): s for s in _steps()}["Verify the result"]
    env = _dict(verify["env"])
    assert env["PHASE"] == "${{ inputs.phase }}"
    assert env["EVALUATOR"] == "${{ runner.temp }}/lgcov-venv/bin/lgcov"


def _verify_in_phase(tmp_path: Path, phase: str) -> list[str]:
    _executable(tmp_path / "bin" / "uvx", FAKE_LGCOV)
    _executable(tmp_path / "evaluator" / "lgcov", FAKE_LGCOV)
    out, report = tmp_path / "github_output", tmp_path / "report.md"
    out.touch()
    report.write_text("## 🟢 lgcov: pass\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "ACTION_PATH": str(tmp_path),
        "UV_PYTHON": "3.12",
        "REPORT_PATH": str(report),
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
        "BASE_DIR": "/base",
        "TAXONOMY": "",
        "FAIL_ON": "root-drift",
        "PHASE": phase,
        "EVALUATOR": str(tmp_path / "evaluator" / "lgcov"),
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Verify the result")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "called").read_text(encoding="utf-8").splitlines()


def test_the_evaluate_phase_verifies_with_the_same_lgcov(tmp_path: Path) -> None:
    assert _verify_in_phase(tmp_path, "evaluate")[0] == f"{tmp_path}/evaluator/lgcov"


def test_one_job_verifies_with_lgcov_from_the_action(tmp_path: Path) -> None:
    assert _verify_in_phase(tmp_path, "all")[0].endswith("/uvx")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


@pytest.fixture
def commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    """A repo with a base commit, then a PR's, which holds a symlink out of the checkout."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    repo = tmp_path / "repo"
    (repo / "cassettes").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "cassettes" / "baseline.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "cassettes" / "baseline.json").unlink()
    (repo / "cassettes" / "baseline.json").symlink_to("/etc/passwd")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the PR")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _commits_step(cwd: Path, tmp_path: Path, phase: str, base: str, pr: str) -> tuple[str, str]:
    """Run the step that checks the commits out: the outputs it set, and what it printed."""
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "BASE_SHA": base,
        "PR_SHA": pr,
        "PHASE": phase,
        "GH_TOKEN": "s3cret",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        [*GITHUB_BASH, _step("Check out the commits")],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return out.read_text(encoding="utf-8"), result.stdout + result.stderr


def test_the_pr_s_commit_is_data_so_a_symlink_in_it_leads_nowhere(
    commits: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, base, pr = commits
    outputs, _ = _commits_step(repo, tmp_path, "evaluate", base, pr)
    assert f"dir={tmp_path}/lgcov-base\n" in outputs
    assert f"pr-dir={tmp_path}/lgcov-pr\n" in outputs
    linked = tmp_path / "lgcov-pr" / "cassettes" / "baseline.json"
    assert not linked.is_symlink()
    assert linked.read_text(encoding="utf-8") == "/etc/passwd"
    # A plain file where the commit has a symlink isn't an uncommitted change the lock warns of.
    assert _git(tmp_path / "lgcov-pr", "status", "--porcelain") == ""
    assert _git(tmp_path / "lgcov-pr", "rev-parse", "HEAD") == pr
    assert _git(tmp_path / "lgcov-base", "rev-parse", "HEAD") == base


@pytest.mark.parametrize("phase", ["all", "candidate"])
def test_the_other_phases_check_out_only_the_base(
    commits: tuple[Path, str, str], tmp_path: Path, phase: str
) -> None:
    repo, base, pr = commits
    outputs, _ = _commits_step(repo, tmp_path, phase, base, pr)
    assert outputs == f"dir={tmp_path}/lgcov-base\n"
    assert not (tmp_path / "lgcov-pr").exists()


def test_a_push_has_no_base_but_still_reads_its_commit_as_data(
    commits: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, _, pr = commits
    outputs, _ = _commits_step(repo, tmp_path, "evaluate", "", pr)
    assert outputs == f"pr-dir={tmp_path}/lgcov-pr\n"


def test_commits_that_cannot_be_checked_out_are_for_lgcov_to_report(
    commits: tuple[Path, str, str], tmp_path: Path
) -> None:
    # Unknown to the repo, and no remote to fetch from. No outputs, so the run gives no verdict.
    repo, _, _ = commits
    outputs, printed = _commits_step(repo, tmp_path, "evaluate", "c" * 40, "d" * 40)
    assert outputs == ""
    assert "could not check out the base commit" in printed
    assert "could not check out the PR's commit" in printed


def _fake_git(tmp_path: Path, fetch: str) -> None:
    _executable(
        tmp_path / "bin" / "git",
        f'case "$1" in\n  cat-file) exit 1 ;;\n  fetch) {fetch} ;;\n  *) exit 0 ;;\nesac\n',
    )


def test_a_fetch_that_needs_the_token_gets_it_just_for_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A candidate job has persist-credentials: false, so a private repo's fetch fails without it.
    _fake_git(
        tmp_path,
        'if [ -z "${GIT_CONFIG_VALUE_0:-}" ]; then exit 1; fi; '
        'printf "%s\\n" "$GIT_CONFIG_KEY_0" "$GIT_CONFIG_VALUE_0" > "$RUNNER_TEMP/auth"',
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    outputs, printed = _commits_step(tmp_path, tmp_path, "candidate", "a" * 40, "")
    assert outputs == f"dir={tmp_path}/lgcov-base\n"
    key, value = (tmp_path / "auth").read_text(encoding="utf-8").splitlines()
    encoded = base64.b64encode(b"x-access-token:s3cret").decode()
    assert (key, value) == ("http.extraheader", f"AUTHORIZATION: basic {encoded}")
    assert "s3cret" not in printed and encoded not in printed


def test_a_fetch_that_needs_no_token_never_sees_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_git(
        tmp_path,
        'if [ -n "${GIT_CONFIG_VALUE_0:-}" ]; then touch "$RUNNER_TEMP/auth"; fi; exit 0',
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    outputs, _ = _commits_step(tmp_path, tmp_path, "candidate", "a" * 40, "")
    assert outputs == f"dir={tmp_path}/lgcov-base\n"
    assert not (tmp_path / "auth").exists()


def test_the_commits_step_gets_the_token_and_no_other_later_step_with_the_pr_s_code_does() -> None:
    steps = {str(s.get("name")): s for s in _steps()}
    env = _dict(steps["Check out the commits"]["env"])
    assert env["GH_TOKEN"] == "${{ inputs.github-token }}"
    assert env["PR_SHA"] == "${{ github.sha }}"
    assert env["PHASE"] == "${{ inputs.phase }}"
    for name in ("Install your project and lgcov", "Run lgcov", "Upload what this job did"):
        assert "github-token" not in str(steps[name]), name
