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
class Timing:
    """T089: one `lgcov ci` run's wall-clock: total = agent + judges + overhead."""

    total: float
    agent: float  # replaying the runs: live node calls and serving
    judges: float = 0.0  # the KPI gate's judge calls
    live: int = 0  # node calls that ran
    served: int = 0  # node calls answered from the cassette
    judge_calls: int = 0
    tokens: int | None = None
    budget: float | None = None
    budget_hit: tuple[str, float] | None = None  # the node it stopped at, seconds elapsed

    @property
    def overhead(self) -> float:
        return self.total - self.agent - self.judges


def _secs(s: float) -> str:
    if s >= 10:
        return f"{s:.0f} s"
    return f"{s:.2g} s" if s == 0 or s >= 0.01 else "<0.01 s"


def format_timing(t: Timing) -> str:
    """E.g. `⏱ 42 s · agent 31 s (3 live / 1 served) · judges 9 s (12 calls) · budget 90 s`."""
    parts = [f"⏱ {_secs(t.total)}", f"agent {_secs(t.agent)} ({t.live} live / {t.served} served)"]
    if t.judge_calls:
        tokens = ""
        if t.tokens is not None:
            tokens = (
                f", {t.tokens / 1000:.1f}k tokens" if t.tokens >= 1000 else f", {t.tokens} tokens"
            )
        parts.append(f"judges {_secs(t.judges)} ({t.judge_calls} calls{tokens})")
    parts.append("no budget" if t.budget is None else f"budget {_secs(t.budget)}")
    if t.budget_hit is not None:
        parts.append(f"budget hit at node `{t.budget_hit[0]}` after {_secs(t.budget_hit[1])}")
    return " · ".join(parts)


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
    under_floor: str = ""  # thresholds.min_node_coverage missed: "Node coverage 80% is under ..."
    l2_lines: tuple[str, ...] = ()  # T091: the KPI gate table, then the sampling table
    calibration_lines: tuple[str, ...] = ()  # T102: band changes and saved-case re-checks
    blocked: tuple[str, ...] = ()  # T091: blocking KPIs whose verdict is BLOCK
    inconclusive: tuple[str, ...] = ()  # T091: blocking KPIs that are INCONCLUSIVE
    not_checked: tuple[str, ...] = ()  # T089: nodes replay stopped before checking
    timing: Timing | None = None  # T089
    tier: str = ""  # T089: pr or nightly
    lint_summary: str = ""  # T104: "🧹 Lint: 2 warnings"; never changes the exit code
    lint_lines: tuple[str, ...] = ()  # T104: one per finding


def _title(r: CiResult, v: ActionVerdict) -> str:
    if r.crash:
        return "🔴 lgcov: agent failure"
    drifted = drift_exit_code(r.fail_on, len(r.root_drift), len(r.propagated_drift))
    if v == "fail" and not drifted:  # the KPI gate, the coverage floor or nodes not checked
        if r.blocked:
            return "🔴 lgcov: KPI regression"
        return "🔴 lgcov: coverage under the floor" if r.under_floor else "🔴 lgcov: inconclusive"
    if v == "pass" and (r.inconclusive or r.not_checked):
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
    if r.lint_summary:
        lines += [r.lint_summary, ""]
    if r.lint_lines:
        lines += ["<details>", "<summary>Lint findings</summary>", ""]
        lines += [*(f"- {x}" for x in r.lint_lines), "", "</details>", ""]
    if r.replay_lines:
        lines += [*r.replay_lines, ""]
    if r.coverage_line:
        lines += [f"Coverage: {r.coverage_line}", ""]
    if r.under_floor:
        lines += [f"🔴 {r.under_floor}", ""]
    if r.newly_uncovered:
        lines += ["Newly uncovered: " + ", ".join(f"`{n}`" for n in r.newly_uncovered), ""]
    if r.l2_lines:
        lines += ["### KPI gate (L2)", "", *r.l2_lines, ""]
    if r.calibration_lines:
        lines += ["### Judge calibration", "", *r.calibration_lines, ""]
    lines += [*r.notes, ""] if r.notes else []
    lines += [format_timing(r.timing), ""] if r.timing else []
    lines.append(
        f"fail-on: `{r.fail_on}`{f' · tier: `{r.tier}`' if r.tier else ''} · "
        f"root drift: {len(r.root_drift)} · "
        f"propagated drift: {len(r.propagated_drift)} · verdict: **{v}**"
    )
    return "\n".join(lines) + "\n"
