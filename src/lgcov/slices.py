"""T090: KPI results per node and per slice (the KPI's failure mode), each group's paired
cases pooled into one paired test. The overall verdict is the worst group's, never a mean,
and a thin group, with fewer cases than it needs to see a drop of its margin at power 0.8,
can't block on its own: its BLOCK becomes INCONCLUSIVE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lgcov.judge_gate import OUTCOMES, GateOutcome
from lgcov.sampling import KpiStats
from lgcov.stats import Decision, PairedResult, cases_needed, decide, paired_test

GROUP_BY = ("node", "slice")
_SEVERITY: dict[GateOutcome, int] = {"pass": 0, "needs_human": 1, "infra_error": 1, "fail": 2}
_ICONS: dict[GateOutcome, str] = {
    "pass": "🟢",
    "fail": "🔴",
    "needs_human": "⚠️",
    "infra_error": "⚠️",
}


@dataclass(frozen=True)
class Group:
    node: str  # "" when not grouped by node
    slice: str  # "" when not grouped by slice, or a node's total over its slices
    kpis: tuple[str, ...]  # the KPIs whose cases are pooled
    blocking: bool  # a judge in it can block (band blocking, not advisory)
    result: PairedResult | None  # None: no paired case
    margin: float
    needed: int | None  # cases to detect a drop of `margin`; None: no effect to detect
    decision: Decision | None
    outcome: GateOutcome
    left_out: tuple[str, ...] = ()  # the group's flag or advisory KPIs, beside blocking ones

    @property
    def thin(self) -> bool:
        return self.result is not None and self.needed is not None and self.result.n < self.needed

    @property
    def where(self) -> str:
        if self.node and self.slice:
            return f"slice `{self.slice}` at node `{self.node}`"
        return f"node `{self.node}`" if self.node else f"slice `{self.slice}`"


def _group(
    node: str, slice_: str, members: Sequence[KpiStats], alpha: float, power: float
) -> Group:
    blocking = [m for m in members if m.band == "blocking"]
    pool = blocking or list(members)  # only blocking judges' cases decide a blocking group
    units = [case for m in pool for case in m.cases]
    kpis = tuple(sorted(m.kpi for m in pool))
    left_out = tuple(sorted(m.kpi for m in members if m not in pool))
    margin = min(m.margin for m in pool)
    if not units:
        return Group(
            node, slice_, kpis, bool(blocking), None, margin, None, None, "needs_human", left_out
        )
    r = paired_test([list(b) for b, _ in units], [list(n) for _, n in units], alpha, power)
    needed = cases_needed(r.sd, margin, alpha, power)
    decision = decide(r.delta, r.ci, margin, r.p <= alpha)
    if decision == "BLOCK" and needed is not None and r.n < needed:
        decision = "INCONCLUSIVE"  # thin: too few cases to block on its own
    outcome = OUTCOMES[decision]
    if not blocking and outcome == "fail":
        outcome = "needs_human"  # advisory or flag judges only: they can't fail the gate
    return Group(node, slice_, kpis, bool(blocking), r, margin, needed, decision, outcome, left_out)


def group_kpis(
    stats: Sequence[KpiStats],
    where: Mapping[str, tuple[str, str]],
    group_by: Sequence[str] = GROUP_BY,
    alpha: float = 0.05,
    power: float = 0.8,
) -> list[Group]:
    """where: KPI -> (node, slice). Groups by the attributes in group_by; by both, a node
    with two or more slices also gets its total. Sorted by node, slice, the total last."""
    keyed: dict[tuple[str, str], list[KpiStats]] = {}
    for s in stats:
        node, slice_ = where.get(s.kpi, ("", ""))
        key = (node if "node" in group_by else "", slice_ if "slice" in group_by else "")
        keyed.setdefault(key, []).append(s)
    groups = [_group(n, sl, members, alpha, power) for (n, sl), members in keyed.items()]
    if "node" in group_by and "slice" in group_by:
        nodes: dict[str, list[KpiStats]] = {}
        for (node, _), members in keyed.items():
            nodes.setdefault(node, []).extend(members)
        for node, members in nodes.items():
            if len({where[m.kpi][1] for m in members if m.kpi in where}) > 1:
                groups.append(_group(node, "", members, alpha, power))
    return sorted(groups, key=lambda g: (g.node, g.slice == "", g.slice))


def overall(groups: Sequence[Group]) -> tuple[GateOutcome, str]:
    """The worst blocking group's outcome, and where it is; never an average."""
    blocking = [g for g in groups if g.blocking]
    if not blocking:
        return "pass", ""
    worst = max(blocking, key=lambda g: _SEVERITY[g.outcome])  # the first of the worst
    return (worst.outcome, worst.where) if worst.outcome != "pass" else ("pass", "")


def _verdict(g: Group) -> str:
    if g.result is None or g.decision is None:
        return "⚠️ no paired cases"
    shown = f"{_ICONS[g.outcome]} {g.decision}"
    if g.thin:
        shown += f" · ⚠️ thin (n={g.result.n}, need {g.needed})"
    return shown


def _row(first: str, g: Group) -> str:
    kpis = ", ".join(f"`{k}`" for k in g.kpis)
    if g.left_out:
        kpis += "; not pooled: " + ", ".join(f"`{k}`" for k in g.left_out)
    r = g.result
    if r is None:
        return f"| {first} | {kpis} | 0 | — | — | — | {_verdict(g)} |"
    return (
        f"| {first} | {kpis} | {r.n} | {r.base_rate:.0%} | {r.new_rate:.0%} "
        f"| {r.delta:+.2f} [{r.ci[0]:+.2f}, {r.ci[1]:+.2f}] | {_verdict(g)} |"
    )


def render_groups(groups: Sequence[Group]) -> list[str]:
    """Markdown: one table per node with its slices as rows (a node's total last), or one
    table of nodes or of slices when grouped by one only; then the overall verdict."""
    if not groups:
        return []
    by_node = any(g.node for g in groups) and any(g.slice for g in groups)
    lines: list[str] = []
    header = [
        "| {} | KPIs | n | base | PR | Δ (95% CI) | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    if by_node:
        for node in sorted({g.node for g in groups}):
            lines += [f"#### Node `{node}`", "", header[0].format("slice"), header[1]]
            for g in (g for g in groups if g.node == node):
                lines.append(_row(f"`{g.slice}`" if g.slice else "all slices", g))
            lines.append("")
    else:
        kind = "node" if any(g.node for g in groups) else "slice"
        lines += [f"#### Per {kind}", "", header[0].format(kind), header[1]]
        lines += [_row(f"`{g.node or g.slice}`", g) for g in groups]
        lines.append("")
    outcome, where = overall(groups)
    verdicts = {"pass": "SHIP", "fail": "BLOCK", "needs_human": "INCONCLUSIVE"}
    shown = verdicts.get(outcome, outcome)
    lines.append(f"Overall: {_ICONS[outcome]} {shown}" + (f", from {where}." if where else "."))
    return lines


def groups_exit(code: int, groups: Sequence[Group], inconclusive_blocks: bool = False) -> int:
    """The KPIs' exit code, made 1 by a blocking group's BLOCK (or its INCONCLUSIVE, with
    inconclusive_blocks); 1 wins over an infra error's 2."""
    outcomes = {g.outcome for g in groups if g.blocking}
    if "fail" in outcomes or (inconclusive_blocks and "needs_human" in outcomes):
        return 1
    return code


def failing_groups(groups: Sequence[Group]) -> tuple[str, ...]:
    return tuple(g.where for g in groups if g.blocking and g.outcome == "fail")
