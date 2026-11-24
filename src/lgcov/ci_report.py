"""T078: the Action's PR comment, built from one `lgcov ci` run (pure)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lgcov.drift_gate import FailOn, HarmStatus, drift_exit_code
from lgcov.sampling import KpiStats
from lgcov.verdict_actions import Action, CiStatus, Evidence, GateFindings, Mode, decide_action

MARKER = "<!-- lgcov-report -->"  # T081: the comment upsert finds its own comment by this
HARNESS_BANNER = "⚠️ Harness error, not a regression"
CASES_MARKER = "<!-- lgcov-cases"  # T186: the report's last line, which `/lgcov override` reads
MAX_COPIED = 500  # T186: more judged cases than this, and the copy holds only their count


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
class Tally:
    """T199: the line under the comment's heading: what replay and the KPI gate found, in runs."""

    runs: int  # replay compared
    touched: int  # runs where some node drifted
    roots: tuple[str, ...] = ()  # T162: root and inconclusive nodes, in run order, once each
    worse: int | None = None  # runs some KPI scored lower than on the base; None: no KPI judged
    worse_by_kpi: tuple[tuple[str, int], ...] = ()  # (KPI, runs it scored lower), table order


def format_tally(t: Tally) -> str:
    """E.g. ``2 of 3 runs touched · 1 worse (`tone` 1) · root node: `respond` +1 more``."""
    worse = "no KPI judged" if t.worse is None else f"{t.worse} worse"
    if t.worse_by_kpi:
        worse += " (" + ", ".join(f"`{kpi}` {n}" for kpi, n in t.worse_by_kpi) + ")"
    root = "none"
    if t.roots:
        root = f"`{t.roots[0]}`" + (f" +{len(t.roots) - 1} more" if len(t.roots) > 1 else "")
    return f"{t.touched} of {t.runs} runs touched · {worse} · root node: {root}"


@dataclass(frozen=True)
class CiResult:
    fail_on: FailOn
    graph_lines: tuple[str, ...] = ()
    graph_note: str = ""
    replay_lines: tuple[str, ...] = ()
    root_drift: tuple[str, ...] = ()
    propagated_drift: tuple[str, ...] = ()
    first_drift: tuple[str, ...] = ()  # T162: each touched run's first node to drift, once each
    inconclusive: tuple[str, ...] = ()  # T162: in root_drift, but the probe couldn't run offline
    # T262: per root, from KPIs/code checks only. Missing roots read as unmeasured.
    harm_status: dict[str, HarmStatus] | None = None
    # T262: why an unmeasured root is unmeasured (e.g. presence-only covering check).
    harm_reasons: dict[str, str] | None = None
    notes: tuple[str, ...] = ()
    cassettes_edited: bool = False  # T154: this PR changed the expected outputs it is graded on
    pools_edited: bool = False  # T124: this PR changed lineage.json or a case pool folder
    unsupported: tuple[str, ...] = ()  # T133: attribution that isn't safe
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
    tally: Tally | None = None  # T199; None: replay compared no runs, so no tally line
    tier: str = ""  # T089: pr or nightly
    samples: int = 0  # T233: k, the judge samples per case; 0: no KPI judged
    lint_summary: str = ""  # T104: "🧹 Lint: 2 warnings"; never changes the exit code
    lint_lines: tuple[str, ...] = ()  # T104: one per finding
    locked: str = ""  # T114: the commits this run compared, from lock.locked_line
    first_adoption: bool = False  # T154: the base has no policy yet, so this PR's own is used
    mode: Mode = "enforcing"  # T153: the rest are what the workflow tells this run
    approved_by: tuple[str, ...] = ()
    ci_status: CiStatus = ""
    cases_copy: str = ""  # T186: cases_copy's line, the report's last; "" for none
    kpi_stats: tuple[KpiStats, ...] = ()  # T120: structured KPI rows for `lgcov propose`
    # T122: same-property |Δ| > MDE → needs_human; structural = investigate note only
    same_property_divergence: str = ""
    structural_divergence_note: str = ""

    @property
    def action(self) -> Action:
        """T153: the headline, next step, exit code and verdict this run comes to."""
        drifted = self.root_drift or self.propagated_drift
        if self.fail_on == "harmed-root":
            # T262: only harmed roots are enforced; unmeasured → needs_human; no-harm-seen alone
            # does not fail (documented choice: pass/exit 0 when every root is no-harm-seen).
            statuses = self.harm_status or {}
            harmed_roots = tuple(
                r for r in self.root_drift if statuses.get(r, "unmeasured") == "harmed"
            )
            unmeasured = any(statuses.get(r, "unmeasured") == "unmeasured" for r in self.root_drift)
            enforced_nodes = harmed_roots
            unenforced = False
            needs_human_unmeasured = bool(unmeasured) and not harmed_roots
        else:
            enforced = drift_exit_code(
                self.fail_on, len(self.root_drift), len(self.propagated_drift)
            )
            enforced_nodes = drifted if enforced else ()
            unenforced = bool(drifted) and not enforced
            needs_human_unmeasured = False
        # T133: unsafe attribution → needs_human; do not also fail on the remaining labels.
        if self.unsupported:
            enforced_nodes, unenforced, needs_human_unmeasured = (), False, False
        return decide_action(
            Evidence(
                self.gate.statistical,
                self.gate.k_gate,
                infra=bool(self.harness_error) or self.gate.infra,
                crashed=bool(self.crash),
                enforced_drift=enforced_nodes,
                unenforced_drift=unenforced,
                under_floor=bool(self.under_floor),
                not_checked=self.not_checked,
                abstained=self.gate.abstained,
                advisory_drops=self.gate.advisory_drops,
                nothing_ran=self.timing is not None and self.timing.live == 0,
                first_adoption=self.first_adoption,
                cassettes_edited=self.cassettes_edited,
                pools_edited=self.pools_edited,
                unsupported=self.unsupported,
                code_failures=self.gate.code_failures,
                hidden_failures=self.gate.hidden_failures,
                unmeasured_drift=needs_human_unmeasured,
                same_property_divergence=self.same_property_divergence,
                structural_divergence_note=self.structural_divergence_note,
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
    lines = [MARKER, f"## {action.headline}", ""]
    if r.tally is not None and action.verdict != "error":  # no counts for a PR it didn't check
        lines += [format_tally(r.tally), ""]
    lines += [f"**Next step:** {action.next_step}", ""]
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
    harm = ""
    if r.harm_status:
        reasons = r.harm_reasons or {}
        parts: list[str] = []
        for n, s in sorted(r.harm_status.items()):
            bit = f"`{n}` {s}"
            if s == "unmeasured" and (why := reasons.get(n)):
                bit = f"{bit} ({why})"
            parts.append(bit)
        harm = f" · harm: {', '.join(parts)}"
    lines.append(
        f"fail-on: `{r.fail_on}`{f' · tier: `{r.tier}`' if r.tier else ''} · "
        f"root drift: {len(r.root_drift)}"
        + (f" ({len(r.inconclusive)} inconclusive)" if r.inconclusive else "")
        + " · "
        f"propagated drift: {len(r.propagated_drift)}{harm} · verdict: **{action.verdict}**"
    )
    body = "\n".join(lines) + "\n"
    body = _claiming_no_ship(body) if action.state == "first_adoption" else body
    return body + f"{r.cases_copy}\n" if r.cases_copy else body


def cases_copy(head: str | None, kpis: Mapping[str, Mapping[str, Sequence[str]]]) -> str:
    """T186: every case a model judge scored on the PR, with its labels, bound to the PR's
    head, as the hidden line `/lgcov override` reads its judge label from (docs/review.md).
    "" without a head or a judged case. `<` and `>` are written as JSON escapes, so no name can
    end the comment early. Over MAX_COPIED cases, it holds their count instead."""
    count = sum(len(cases) for cases in kpis.values())
    if not head or not count:
        return ""
    copied: dict[str, object] = {"head": head}
    if count > MAX_COPIED:
        copied |= {"kpis": None, "cases": count}
    else:
        copied["kpis"] = {k: {c: list(v) for c, v in cases.items()} for k, cases in kpis.items()}
    text = json.dumps(copied, sort_keys=True, separators=(", ", ": "))
    escaped = text.replace("<", "\\u003c").replace(">", "\\u003e")
    return f"{CASES_MARKER} {escaped} -->"


Copied = dict[str, dict[str, tuple[str, ...]]]  # KPI -> case -> the judge's labels on the PR


def read_cases_copy(line: str) -> tuple[str, Copied | None] | None:
    """T186: cases_copy's line read back: (head, KPI -> case -> labels), with None for the
    cases when there were too many to copy. None for a line that isn't exactly one."""
    if not line.startswith(f"{CASES_MARKER} ") or not line.endswith(" -->"):
        return None
    try:
        raw = json.loads(line[len(CASES_MARKER) + 1 : -len(" -->")])
    except ValueError:
        return None
    head = raw.get("head") if isinstance(raw, dict) else None
    if not isinstance(head, str) or not head:
        return None
    kpis = raw.get("kpis")
    if kpis is None:
        return head, None
    if not isinstance(kpis, dict):
        return None
    copied: Copied = {}
    for kpi, cases in kpis.items():
        if not isinstance(cases, dict):
            return None
        for case, labels in cases.items():
            if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
                return None
            copied.setdefault(kpi, {})[case] = tuple(labels)
    return head, copied
