"""T154: what a GitHub workflow must keep so a PR can't grade itself (pure: parsed YAML in,
problems out). tests/test_workflow_lint.py runs it over this repo's workflows.

- Never `pull_request_target`: it runs with the base's secrets and a write token.
- No secret in a job that runs the PR's code, which could read it and send it anywhere.

T179: with the Action's `phase` input, the candidate job holds nothing worth taking, and the job
that decides can't be skipped into a pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ACTION = "AMOHYZ/langgraph-regression-guard@"
# An expression reading the secrets context: ${{ secrets.X }}, secrets['X'], toJSON(secrets).
_READS_SECRETS = re.compile(r"\$\{\{[^}]*\bsecrets\b")


def _mapping(value: object) -> Mapping[object, object]:
    return value if isinstance(value, dict) else {}


def _triggers(workflow: Mapping[object, object]) -> set[str]:
    raw = workflow.get("on", workflow.get(True))  # PyYAML reads the key `on` as True
    if isinstance(raw, str):
        return {raw}
    return {str(t) for t in raw} if isinstance(raw, list | dict) else set()


def _steps(job: Mapping[object, object]) -> list[Mapping[object, object]]:
    steps = job.get("steps")
    return [_mapping(step) for step in steps] if isinstance(steps, list) else []


def _uses(step: Mapping[object, object]) -> str:
    return str(step.get("uses", ""))


def _with(step: Mapping[object, object]) -> Mapping[object, object]:
    return _mapping(step.get("with"))


def _phases(job: Mapping[object, object]) -> set[str]:
    """Which half of a gate run the job's steps run; `all` is both, in one job."""
    return {
        str(_with(step).get("phase", "all"))
        for step in _steps(job)
        if _uses(step).startswith(("./", ACTION))
    }


def _runs_pr_code(job: Mapping[object, object]) -> bool:
    """It checks out anything but the base, or runs a reusable workflow, or this Action in a phase
    that installs the project."""
    if "uses" in job or _phases(job) - {"evaluate"}:
        return True
    return any(
        _uses(step).startswith("actions/checkout@")
        and "base" not in str(_with(step).get("ref", ""))
        for step in _steps(job)
    )


def _phase_problems(
    name: object, job: Mapping[object, object], workflow: Mapping[object, object]
) -> list[str]:
    problems = []
    phases = _phases(job)
    if "candidate" in phases:
        permissions = job.get("permissions", workflow.get("permissions"))
        if permissions is None or "write" in str(permissions):
            problems.append(
                f"job {name} runs the PR's code and can hold a write token; "
                "set permissions: contents: read"
            )
        kept = [
            step
            for step in _steps(job)
            if _uses(step).startswith("actions/checkout@")
            and str(_with(step).get("persist-credentials", True)).lower() != "false"
        ]
        if kept:
            problems.append(
                f"job {name} leaves its token in .git/config, where the PR's code can read it; "
                "set persist-credentials: false"
            )
    condition = str(job.get("if", ""))
    if "evaluate" in phases and "!cancelled()" not in condition and "always()" not in condition:
        problems.append(
            f"job {name} decides the PR but is skipped if the candidate job fails, and GitHub "
            "counts a skipped check as passing; add if: ${{ !cancelled() }}"
        )
    return problems


def workflow_problems(doc: object) -> list[str]:
    """What's wrong with one parsed workflow file; [] when nothing is."""
    workflow = _mapping(doc)
    triggers = _triggers(workflow)
    problems = []
    if "pull_request_target" in triggers:
        problems.append(
            "uses pull_request_target, which runs with the base's secrets and a write token; "
            "use pull_request"
        )
    if "pull_request" not in triggers:
        return problems
    everywhere = _READS_SECRETS.search(str(workflow.get("env", "")))
    for name, raw in _mapping(workflow.get("jobs")).items():
        job = _mapping(raw)
        reads = everywhere or job.get("secrets") == "inherit" or _READS_SECRETS.search(str(job))
        if reads and _runs_pr_code(job):
            problems.append(
                f"job {name} runs the PR's code and can read a secret; "
                "that code could send it anywhere"
            )
        problems += _phase_problems(name, job, workflow)
    return problems
