"""T078: the Action's fail-on policy. Drift counts in, exit code out (pure)."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

FailOn = Literal["root-drift", "any-drift", "never"]
FAIL_ON_VALUES: tuple[FailOn, ...] = ("root-drift", "any-drift", "never")
ActionVerdict = Literal["pass", "fail", "error"]


class Outcome(IntEnum):
    """T074: what a run found; the value is the exit code."""

    PASS = 0
    AGENT_FAIL = 1  # drift fail-on cares about, or a node in the PR's graph raised
    INFRA_ERROR = 2  # install, load, cassette, judge or network trouble; never a regression


EXIT_PASS = Outcome.PASS
EXIT_DRIFT = Outcome.AGENT_FAIL
EXIT_HARNESS_ERROR = Outcome.INFRA_ERROR


def parse_fail_on(value: str) -> FailOn:
    for allowed in FAIL_ON_VALUES:
        if value == allowed:
            return allowed
    raise ValueError(f"fail-on must be one of {', '.join(FAIL_ON_VALUES)}; got {value!r}")


def drift_exit_code(fail_on: FailOn, root: int, propagated: int) -> int:
    """EXIT_DRIFT when drift that `fail_on` cares about was found, else EXIT_PASS.

    Until replay records node inputs, every run that drifts has a root (its first
    drifted node), so root-drift and any-drift fail on the same PRs.
    """
    if fail_on == "never":
        return EXIT_PASS
    if fail_on == "root-drift":
        return EXIT_DRIFT if root else EXIT_PASS
    return EXIT_DRIFT if root or propagated else EXIT_PASS


def verdict(exit_code: int) -> ActionVerdict:
    if exit_code == EXIT_PASS:
        return "pass"
    if exit_code == EXIT_DRIFT:
        return "fail"
    return "error"
