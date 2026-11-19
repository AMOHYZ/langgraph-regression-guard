"""T078: the Action's fail-on policy. Drift counts in, exit code out (pure).

T262: each root also gets a harm status from lgcov's own KPIs/code checks. fail-on:
harmed-root fails only on harmed roots; unmeasured is needs_human (never a pass);
no-harm-seen alone does not fail. root-drift stays the default (any root fails).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import IntEnum
from typing import Literal

FailOn = Literal["root-drift", "any-drift", "never", "harmed-root"]
FAIL_ON_VALUES: tuple[FailOn, ...] = ("root-drift", "any-drift", "never", "harmed-root")
ActionVerdict = Literal["pass", "fail", "error"]

# Per drift root, from KPIs/code checks only — never from a synthetic truth label.
HarmStatus = Literal["harmed", "no harm seen", "unmeasured"]


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


def root_harm_statuses(
    roots: Sequence[str],
    kpi_nodes: Mapping[str, str],
    failed_kpis: frozenset[str],
    judged_kpis: frozenset[str],
) -> dict[str, HarmStatus]:
    """T262: harm status per drift root from lgcov's own checks only.

    A KPI covers a root when its taxonomy `node` matches that root. `failed_kpis` are
    blocking KPIs that BLOCK or code checks that newly fail; `judged_kpis` are those that
    actually ran. No truth label is read.
    """
    covering: dict[str, list[str]] = {}
    for kpi, node in kpi_nodes.items():
        covering.setdefault(node, []).append(kpi)
    out: dict[str, HarmStatus] = {}
    for root in roots:
        kpis = covering.get(root, [])
        ran = [k for k in kpis if k in judged_kpis]
        if not ran:
            out[root] = "unmeasured"
        elif any(k in failed_kpis for k in kpis):
            out[root] = "harmed"
        else:
            out[root] = "no harm seen"
    return out


def drift_exit_code(
    fail_on: FailOn,
    root: int,
    propagated: int,
    *,
    harmed: int | None = None,
) -> int:
    """EXIT_DRIFT when drift that `fail_on` cares about was found, else EXIT_PASS.

    T162: `root` counts root nodes and the inconclusive ones (a changed node the offline
    probe can't run, or drift no changed node explains), so root-drift and any-drift still
    fail on nearly the same PRs: root-drift passes only when every drifted node is cleared.

    T262: harmed-root fails only when `harmed` roots exist. Without a harmed count (replay
    with no KPI gate), it does not fail: unmeasured roots are needs_human via CiResult, not
    an exit 1 here.
    """
    if fail_on == "never":
        return EXIT_PASS
    if fail_on == "harmed-root":
        return EXIT_DRIFT if (harmed or 0) else EXIT_PASS
    if fail_on == "root-drift":
        return EXIT_DRIFT if root else EXIT_PASS
    return EXIT_DRIFT if root or propagated else EXIT_PASS


def verdict(exit_code: int) -> ActionVerdict:
    if exit_code == EXIT_PASS:
        return "pass"
    if exit_code == EXIT_DRIFT:
        return "fail"
    return "error"
