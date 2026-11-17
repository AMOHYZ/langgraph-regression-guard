"""T154: what a GitHub workflow must keep so a PR can't grade itself (pure: parsed YAML in,
problems out). tests/test_workflow_lint.py runs it over this repo's workflows.

- Never `pull_request_target`: it runs with the base's secrets and a write token.
- No secret in a job that runs the PR's code, which could read it and send it anywhere.

T179: with the Action's `phase` input, the candidate job holds nothing worth taking, and the job
that decides can't be skipped into a pass.

T186: a workflow on `issue_comment` starts only for a maintainer, reads the comment only in an
`if`, and checks out and runs nothing of the PR (docs/review.md, "What the lint checks").
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


# T186: what the comment says, which anyone could have written: only an `if` may read it.
_READS_COMMENT = re.compile(r"\$\{\{[^}]*\bgithub\.event\.(comment\.|issue\.title\b|issue\.body\b)")
_GATE_LOCAL = ("./", "./.")  # the repo root, whole: `./review` is another action


def _outside_if(value: object) -> str:
    """Every value under this one as text, but for what an `if` key holds."""
    if isinstance(value, dict):
        return " ".join(_outside_if(v) for k, v in value.items() if k != "if")
    if isinstance(value, list):
        return " ".join(_outside_if(v) for v in value)
    return str(value)


def _comment_problems(workflow: Mapping[object, object]) -> list[str]:
    """T186: the rules for a workflow on `issue_comment`, which holds a write token on the base
    and runs from the default branch (docs/review.md, "What the lint checks")."""
    on = workflow.get("on", workflow.get(True))
    event = _mapping(on).get("issue_comment") if isinstance(on, dict) else None
    problems = []
    if _mapping(event).get("types") != ["created"]:
        problems.append("issue_comment: types must be exactly [created], so an edit runs nothing")
    if "permissions" not in workflow:
        problems.append("the workflow sets no top-level permissions, so jobs get the default token")
    if _READS_COMMENT.search(_outside_if({k: v for k, v in workflow.items() if k != "jobs"})):
        problems.append("the workflow reads the comment outside an `if`")
    for name, raw in _mapping(workflow.get("jobs")).items():
        job = _mapping(raw)
        if "uses" in job:
            problems.append(f"job {name} calls a reusable workflow, which this lint can't see into")
        condition = str(job.get("if", ""))
        for field in ("github.event.issue.pull_request", "github.event.comment.author_association"):
            if field not in condition:
                problems.append(f"job {name}'s `if` must read {field}")
        if _READS_COMMENT.search(_outside_if(job)):
            problems.append(f"job {name} reads the comment outside an `if`")
        for step in _steps(job):
            uses = _uses(step)
            if uses.startswith("actions/checkout@"):
                problems += [
                    f"job {name} checks out with `{key}`: only the default branch, never the PR"
                    for key in ("ref", "repository")
                    if key in _with(step)
                ]
                if str(_with(step).get("persist-credentials", True)).lower() != "false":
                    problems.append(
                        f"job {name} leaves its token in .git/config; "
                        "set persist-credentials: false"
                    )
            if uses in _GATE_LOCAL or uses.startswith(ACTION):
                problems.append(
                    f"job {name} runs the gate Action ({uses}), which installs and runs the "
                    "project; use the review action"
                )
    return problems


def workflow_problems(doc: object) -> list[str]:
    """What's wrong with one parsed workflow file; [] when nothing is."""
    workflow = _mapping(doc)
    triggers = _triggers(workflow)
    problems = _comment_problems(workflow) if "issue_comment" in triggers else []
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
