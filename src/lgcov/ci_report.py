"""T078: the Action's PR comment, built from one `lgcov ci` run (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from lgcov.drift_gate import ActionVerdict, FailOn, drift_exit_code, verdict

MARKER = "<!-- lgcov-report -->"  # T081: the comment upsert finds its own comment by this
HARNESS_BANNER = "⚠️ Harness error, not a regression"
_TITLES = {
    "pass": "🟢 lgcov: pass",
    "fail": "🔴 lgcov: drift",
    "error": "⚠️ lgcov: harness error, not a regression",
}


@dataclass(frozen=True)
class CiResult:
    fail_on: FailOn
    exit_code: int
    graph_lines: tuple[str, ...] = ()
    graph_note: str = ""
    replay_lines: tuple[str, ...] = ()
    root_drift: tuple[str, ...] = ()
    propagated_drift: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    harness_error: str = ""
    harness_node: str = ""  # T074: the network trouble surfaced inside this node
    crash: str = ""  # T074: e.g. "Node `classify` raised ValueError: ..."; an agent failure
    coverage_line: str = ""  # T083, e.g. "Nodes 75% (−25% vs main) · Edges 60% (±0)"
    newly_uncovered: tuple[str, ...] = ()
    l2_lines: tuple[str, ...] = ()  # T091: the KPI gate table, then the sampling table
    blocked: tuple[str, ...] = ()  # T091: blocking KPIs whose verdict is BLOCK
    inconclusive: tuple[str, ...] = ()  # T091: blocking KPIs that are INCONCLUSIVE


def _title(r: CiResult, v: ActionVerdict) -> str:
    if r.crash:
        return "🔴 lgcov: agent failure"
    drifted = drift_exit_code(r.fail_on, len(r.root_drift), len(r.propagated_drift))
    if v == "fail" and not drifted:  # T091: the KPI gate failed it, not drift
        return "🔴 lgcov: KPI regression" if r.blocked else "🔴 lgcov: inconclusive"
    if v == "pass" and r.inconclusive:
        return "⚠️ lgcov: inconclusive, needs a human"
    return _TITLES[v]


def render_ci_report(r: CiResult) -> str:
    v = verdict(r.exit_code)
    lines = [MARKER, f"## {_title(r, v)}", ""]
    if r.crash:
        lines += [f"🔴 {r.crash}", ""]
    if r.harness_error:
        # Keep a stray ``` in the error from closing the fence early.
        error = r.harness_error.replace("```", "'''")
        banner = HARNESS_BANNER
        if r.harness_node:
            banner = f"⚠️ Harness error (transport) in node `{r.harness_node}`, not a regression"
        lines += [
            f"{banner}: lgcov could not finish, so this PR was not checked.",
            "",
            "```",
            error,
            "```",
            "",
        ]
    if r.graph_lines or r.graph_note:
        lines += ["### Graph changes", ""]
        lines += [r.graph_note, ""] if r.graph_note else []
        lines += [*r.graph_lines, ""]
    if r.replay_lines:
        lines += [*r.replay_lines, ""]
    if r.coverage_line:
        lines += [f"Coverage: {r.coverage_line}", ""]
    if r.newly_uncovered:
        lines += ["Newly uncovered: " + ", ".join(f"`{n}`" for n in r.newly_uncovered), ""]
    if r.l2_lines:
        lines += ["### KPI gate (L2)", "", *r.l2_lines, ""]
    lines += [*r.notes, ""] if r.notes else []
    lines.append(
        f"fail-on: `{r.fail_on}` · root drift: {len(r.root_drift)} · "
        f"propagated drift: {len(r.propagated_drift)} · verdict: **{v}**"
    )
    return "\n".join(lines) + "\n"
