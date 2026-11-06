"""T078: the Action's PR comment, built from one `lgcov ci` run (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from lgcov.drift_gate import FailOn, drift_exit_code
from lgcov.verdict_actions import Action, CiStatus, Evidence, GateFindings, Mode, decide_action

MARKER = "<!-- lgcov-report -->"  # T081: the comment upsert finds its own comment by this
HARNESS_BANNER = "⚠️ Harness error, not a regression"


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
    graph_lines: tuple[str, ...] = ()
    graph_note: str = ""
    replay_lines: tuple[str, ...] = ()
    root_drift: tuple[str, ...] = ()
    propagated_drift: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    cassettes_edited: bool = False  # T154: this PR changed the expected outputs it is graded on
    harness_error: str = ""
    harness_node: str = ""  # T074: the network trouble surfaced inside this node
    crash: str = ""  # T074: e.g. "Node `classify` raised ValueError: ..."; an agent failure
    coverage_line: str = ""  # T083, e.g. "Nodes 75% (−25% vs main) · Edges 60% (±0)"
    newly_uncovered: tuple[str, ...] = ()
    under_floor: str = ""  # thresholds.min_node_coverage missed: "Node coverage 80% is under ..."
    l2_lines: tuple[str, ...] = ()  # T091: the KPI gate table, then the sampling table
    calibration_lines: tuple[str, ...] = ()  # T102: band changes and saved-case re-checks
    gate: GateFindings = GateFindings()  # T153: what the KPI gate found, for decide_action
    inconclusive_blocks: bool = False  # T153: fail-on-inconclusive or gate.inconclusive_blocks
    not_checked: tuple[str, ...] = ()  # T089: nodes replay stopped before checking
    timing: Timing | None = None  # T089
    tier: str = ""  # T089: pr or nightly
    lint_summary: str = ""  # T104: "🧹 Lint: 2 warnings"; never changes the exit code
    lint_lines: tuple[str, ...] = ()  # T104: one per finding
    locked: str = ""  # T114: the commits this run compared, from lock.locked_line
    first_adoption: bool = False  # T154: the base has no policy yet, so this PR's own is used
    mode: Mode = "enforcing"  # T153: the rest are what the workflow tells this run
    approved_by: tuple[str, ...] = ()
    ci_status: CiStatus = ""

    @property
    def action(self) -> Action:
        """T153: the headline, next step, exit code and verdict this run comes to."""
        drifted = self.root_drift or self.propagated_drift
        enforced = drift_exit_code(self.fail_on, len(self.root_drift), len(self.propagated_drift))
        return decide_action(
            Evidence(
                self.gate.statistical,
                self.gate.k_gate,
                infra=bool(self.harness_error) or self.gate.infra,
                crashed=bool(self.crash),
                enforced_drift=drifted if enforced else (),
                unenforced_drift=bool(drifted) and not enforced,
                under_floor=bool(self.under_floor),
                not_checked=self.not_checked,
                abstained=self.gate.abstained,
                advisory_drops=self.gate.advisory_drops,
                nothing_ran=self.timing is not None and self.timing.live == 0,
                first_adoption=self.first_adoption,
                cassettes_edited=self.cassettes_edited,
                inconclusive_blocks=self.inconclusive_blocks,
                mode=self.mode,
                approved_by=self.approved_by,
                ci_status=self.ci_status,
            )
        )


def _harness_banner(r: CiResult, action: Action) -> str:
    if action.verdict != "error":  # T153: replay did check it, and what it found stands
        what = "⚠️ Harness error: lgcov could not finish this run."
        return f"{what} What it found before that stands; anything after it was not checked."
    banner = HARNESS_BANNER
    if r.harness_node:
        banner = f"⚠️ Harness error (transport) in node `{r.harness_node}`, not a regression"
    return f"{banner}: lgcov could not finish, so this PR was not checked."


def _claiming_no_ship(body: str) -> str:
    """T154: a first adoption PR's report: what it found, with nothing to read as a SHIP."""
    lines = [
        line.replace("🟢 SHIP", "would SHIP")
        for line in body.splitlines()
        if not line.startswith("**What SHIP and BLOCK guarantee.**")
    ]
    return "\n".join(lines) + "\n"


def render_ci_report(r: CiResult) -> str:
    action = r.action
    lines = [MARKER, f"## {action.headline}", "", f"**Next step:** {action.next_step}", ""]
    lines += [line for detail in action.details for line in (detail, "")]
    if r.crash:
        lines += [f"🔴 {r.crash}", ""]
    if r.harness_error:
        # Keep a stray ``` in the error from closing the fence early.
        error = r.harness_error.replace("```", "'''")
        lines += [
            _harness_banner(r, action),
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
    lines += [r.locked, ""] if r.locked else []
    lines.append(
        f"fail-on: `{r.fail_on}`{f' · tier: `{r.tier}`' if r.tier else ''} · "
        f"root drift: {len(r.root_drift)} · "
        f"propagated drift: {len(r.propagated_drift)} · verdict: **{action.verdict}**"
    )
    body = "\n".join(lines) + "\n"
    return _claiming_no_ship(body) if action.state == "first_adoption" else body
