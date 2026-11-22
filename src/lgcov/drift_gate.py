"""T078: the Action's fail-on policy. Drift counts in, exit code out (pure).

T262: each root also gets a harm status from lgcov's own KPIs/code checks. fail-on:
harmed-root fails only on harmed roots; unmeasured is needs_human (never a pass);
no-harm-seen alone does not fail. root-drift stays the default (any root fails).

A check measures a root only when it can observe that root's output or something
downstream of it. Same-node presence (`node_visited` / `node_not_visited` on the root)
and checks scoped only to upstream nodes do not measure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import IntEnum
from typing import Any, Literal

FailOn = Literal["root-drift", "any-drift", "never", "harmed-root"]
FAIL_ON_VALUES: tuple[FailOn, ...] = ("root-drift", "any-drift", "never", "harmed-root")
ActionVerdict = Literal["pass", "fail", "error"]

# Per drift root, from KPIs/code checks only — never from a synthetic truth label.
HarmStatus = Literal["harmed", "no harm seen", "unmeasured"]

# Trajectory presence checks: they see whether a node ran, not its outputs.
PRESENCE_CHECK_TYPES: frozenset[str] = frozenset({"node_visited", "node_not_visited"})

REASON_NO_CHECK = "no covering check"
REASON_PRESENCE = "only a presence check covers this node"
REASON_UPSTREAM = "only an upstream check covers this node"
REASON_NON_OBSERVING = "only non-observing checks cover this node"
REASON_DID_NOT_RUN = "covering checks did not run"


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


def _first_index(path: Sequence[str], name: str) -> int | None:
    try:
        return path.index(name)
    except ValueError:
        return None


def check_observes_root(
    root: str,
    check: Mapping[str, Any] | None,
    *,
    path: Sequence[str] | None = None,
    judge: str | None = None,
) -> bool:
    """True when this check can observe the root's output or something downstream of it.

    `check` is None for non-code KPIs (LLM / jev): the taxonomy node match is enough.
    `judge: fake` never observes (constant label; T264). Presence on the root itself never
    observes. With `path`, a presence (or node_sequence) check scoped only to nodes that
    appear before the root does not observe either. `output_contains` / `output_matches`
    observe the named node's output.
    """
    if judge == "fake":
        return False
    if check is None:
        return True
    kind = check.get("type", "")
    if kind == "fake":
        return False
    if kind in ("output_contains", "output_matches"):
        return True
    if kind in PRESENCE_CHECK_TYPES:
        target = check.get("node", "")
        if not target or target == root:
            return False
        if path is None:
            # Different-node presence without an order: treat as observing run effects.
            return True
        root_i = _first_index(path, root)
        target_i = _first_index(path, target)
        if root_i is None or target_i is None:
            return False
        return target_i > root_i
    if kind == "node_sequence":
        names = tuple(n.strip() for n in check.get("nodes", "").split(",") if n.strip())
        if not names:
            return False
        if path is None:
            return True
        root_i = _first_index(path, root)
        if root_i is None:
            return False
        return any(
            (i := _first_index(path, n)) is not None and i > root_i for n in names if n != root
        )
    # tool_*, max_steps, tool_sequence: run-level / downstream effects.
    return True


def _is_presence_on_root(root: str, check: Mapping[str, Any] | None) -> bool:
    if check is None:
        return False
    return check.get("type", "") in PRESENCE_CHECK_TYPES and check.get("node", "") == root


def _is_upstream_only(
    root: str, check: Mapping[str, Any] | None, path: Sequence[str] | None
) -> bool:
    """True when the check only looks at nodes that run before the root on `path`."""
    if check is None or path is None:
        return False
    if check_observes_root(root, check, path=path):
        return False
    kind = check.get("type", "")
    if kind in PRESENCE_CHECK_TYPES:
        target = check.get("node", "")
        root_i = _first_index(path, root)
        target_i = _first_index(path, target) if target else None
        return (
            bool(target)
            and target != root
            and root_i is not None
            and target_i is not None
            and target_i < root_i
        )
    if kind == "node_sequence":
        names = tuple(n.strip() for n in check.get("nodes", "").split(",") if n.strip())
        root_i = _first_index(path, root)
        if root_i is None or not names:
            return False
        idxs = [_first_index(path, n) for n in names]
        if any(i is None for i in idxs):
            return False
        return all(i is not None and i < root_i for i in idxs)
    return False


def _unmeasured_reason(
    root: str,
    candidates: Sequence[str],
    kpi_checks: Mapping[str, Mapping[str, Any] | None] | None,
    path: Sequence[str] | None,
    *,
    observing_none_ran: bool,
) -> str:
    if observing_none_ran:
        return REASON_DID_NOT_RUN
    if not candidates:
        return REASON_NO_CHECK

    def check_of(name: str) -> Mapping[str, Any] | None:
        if kpi_checks is None:
            return None
        return kpi_checks.get(name)

    if all(_is_presence_on_root(root, check_of(k)) for k in candidates):
        return REASON_PRESENCE
    if all(_is_upstream_only(root, check_of(k), path) for k in candidates):
        return REASON_UPSTREAM
    return REASON_NON_OBSERVING


def root_harm_statuses(
    roots: Sequence[str],
    kpi_nodes: Mapping[str, str],
    failed_kpis: frozenset[str],
    judged_kpis: frozenset[str],
    *,
    kpi_checks: Mapping[str, Mapping[str, Any] | None] | None = None,
    path: Sequence[str] | None = None,
    kpi_judges: Mapping[str, str] | None = None,
) -> dict[str, HarmStatus]:
    """T262: harm status per drift root from lgcov's own checks only.

    A KPI is a candidate when its taxonomy `node` matches that root. It measures the root
    only when its check can observe the root's output or something downstream (`kpi_checks`
    + optional run `path`). `failed_kpis` are blocking KPIs that BLOCK or code checks that
    newly fail; `judged_kpis` are those that actually ran (a `when:` skip is not judged).
    No truth label is read.
    """
    statuses, _ = root_harm(
        roots,
        kpi_nodes,
        failed_kpis,
        judged_kpis,
        kpi_checks=kpi_checks,
        path=path,
        kpi_judges=kpi_judges,
    )
    return statuses


def root_unmeasured_reasons(
    roots: Sequence[str],
    kpi_nodes: Mapping[str, str],
    judged_kpis: frozenset[str],
    *,
    kpi_checks: Mapping[str, Mapping[str, Any] | None] | None = None,
    path: Sequence[str] | None = None,
    failed_kpis: frozenset[str] | None = None,
    kpi_judges: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Reason string per unmeasured root (empty map entries omitted for measured roots)."""
    _, reasons = root_harm(
        roots,
        kpi_nodes,
        failed_kpis or frozenset(),
        judged_kpis,
        kpi_checks=kpi_checks,
        path=path,
        kpi_judges=kpi_judges,
    )
    return reasons


def root_harm(
    roots: Sequence[str],
    kpi_nodes: Mapping[str, str],
    failed_kpis: frozenset[str],
    judged_kpis: frozenset[str],
    *,
    kpi_checks: Mapping[str, Mapping[str, Any] | None] | None = None,
    path: Sequence[str] | None = None,
    kpi_judges: Mapping[str, str] | None = None,
) -> tuple[dict[str, HarmStatus], dict[str, str]]:
    """Statuses plus a reason for each unmeasured root."""
    covering: dict[str, list[str]] = {}
    for kpi, node in kpi_nodes.items():
        covering.setdefault(node, []).append(kpi)
    out: dict[str, HarmStatus] = {}
    reasons: dict[str, str] = {}
    for root in roots:
        candidates = covering.get(root, [])
        observing: list[str] = []
        for kpi in candidates:
            check = None if kpi_checks is None else kpi_checks.get(kpi)
            kind = None if kpi_judges is None else kpi_judges.get(kpi)
            if check_observes_root(root, check, path=path, judge=kind):
                observing.append(kpi)
        ran = [k for k in observing if k in judged_kpis]
        if not observing:
            out[root] = "unmeasured"
            reasons[root] = _unmeasured_reason(
                root, candidates, kpi_checks, path, observing_none_ran=False
            )
        elif not ran:
            out[root] = "unmeasured"
            reasons[root] = _unmeasured_reason(
                root, candidates, kpi_checks, path, observing_none_ran=True
            )
        elif any(k in failed_kpis for k in observing):
            out[root] = "harmed"
        else:
            out[root] = "no harm seen"
    return out, reasons


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
