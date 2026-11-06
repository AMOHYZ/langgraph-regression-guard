"""T154: what a GitHub workflow must keep so a PR can't grade itself (pure: parsed YAML in,
problems out). tests/test_workflow_lint.py runs it over this repo's workflows.

- Never `pull_request_target`: it runs with the base's secrets and a write token.
- No secret in a job that runs the PR's code, which could read it and send it anywhere.
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


def _runs_pr_code(job: Mapping[object, object]) -> bool:
    """It checks out anything but the base, or runs this Action or a reusable workflow."""
    if "uses" in job:
        return True
    steps = job.get("steps")
    for step in steps if isinstance(steps, list) else []:
        uses = str(_mapping(step).get("uses", ""))
        if uses.startswith(("./", ACTION)):
            return True
        ref = str(_mapping(_mapping(step).get("with")).get("ref", ""))
        if uses.startswith("actions/checkout@") and "base" not in ref:
            return True
    return False


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
    return problems
