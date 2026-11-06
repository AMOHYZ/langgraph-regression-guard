"""T153: one gate outcome, one action for the reviewer (pure). decide_action is the table in
docs/verdict-actions.md: the PR comment's headline and next step and the run's exit code all
come from it, so they can't disagree with each other or claim more than the evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lgcov.sampling import KpiStats, k_gate, named_cases
from lgcov.slices import Group, failing_groups
from lgcov.stats import Decision

Mode = Literal["enforcing", "advisory"]
MODES: tuple[Mode, ...] = ("enforcing", "advisory")
CiStatus = Literal["", "success", "failure", "cancelled", "skipped"]  # "": not passed in
CI_STATUSES: tuple[CiStatus, ...] = ("success", "failure", "cancelled", "skipped")
ReviewVerdict = Literal["pass", "fail", "needs_human", "error", "advisory"]

# T154: the headline on a first adoption PR, whose policy is all its own.
BOOTSTRAP_HEADLINE = (
    "Advisory: policy comes from this PR and is not trusted yet; enforcement starts after merge"
)
_NO_DECISION = "⚠️ No decision: needs_human"
_OVERRIDE = "If the judge is wrong, record it with `lgcov review override`."
# State: headline and next step, in the table's order; decide_action fills in the {names}.
_ROWS: dict[str, tuple[str, str]] = {
    "infra": (
        "⚠️ lgcov: harness error, not a regression",
        "Fix the error below and re-run. This PR was not checked: the agent neither passed nor "
        "failed.",
    ),
    "first_adoption": (
        f"⚠️ {BOOTSTRAP_HEADLINE}",
        "Review this PR's lgcov setup like any other config change. The results below are what "
        "lgcov would have said; they block nothing.",
    ),
    "agent_failure": (
        "🔴 lgcov: agent failure",
        "Fix the node named below. The run stopped there, so nothing after it was checked.",
    ),
    "policy_code_check": (
        "🔴 lgcov: code check failed",
        "{code_failures}. Fix the regression. If a check can legitimately vary between runs, make "
        "it advisory in `gate.advisory_kpis` on the base branch first: this PR can't change what "
        "grades it.",
    ),
    "policy_drift": (
        "🔴 lgcov: drift",
        "Open the root node. If the change is intended, re-baseline with `lgcov cassette accept "
        "--nodes {root}`; if not, fix it.",
    ),
    "block": ("🔴 lgcov: KPI regression", f"Fix the root node in the KPI table. {_OVERRIDE}"),
    "policy_floor": (
        "🔴 lgcov: coverage under the floor",
        "Add cases that reach the uncovered nodes, or lower the floor.",
    ),
    "not_checked": (
        _NO_DECISION,
        "Re-run with a bigger budget or `on-divergence: flag`, or review {not_checked} yourself: "
        "lgcov did not check them.",
    ),
    "abstained": (_NO_DECISION, f"Read the cases for {{abstained}} and decide. {_OVERRIDE}"),
    "inconclusive": (
        _NO_DECISION,
        "Read the KPI table, then merge on your own judgement or add the cases it asks for.",
    ),
    "cassettes_edited": (
        _NO_DECISION,
        "Read the cassette diff and decide: this PR changed the expected outputs it is graded "
        "on, so lgcov can't vouch for it.",
    ),
    "drift_not_enforced": (
        _NO_DECISION,
        "Read the drift table and decide. `fail-on: never` means lgcov won't fail this check on "
        "drift.",
    ),
    "advisory_drop_only": (
        _NO_DECISION,
        "{dropped} dropped, but no KPI here can block yet. Decide yourself; `lgcov calibrate` is "
        "what lets its judge block.",
    ),
    "ship_advisory_drop": (
        "⚠️ lgcov: SHIP ({blocking}), advisory drop in {dropped}",
        "Look at {dropped}: it dropped, but its judge is advisory and can't block this PR.",
    ),
    "nothing_ran": (
        "🟢 lgcov: every node served, nothing re-ran",
        "Review as usual. lgcov measured nothing here: every node's code and masked input match "
        "the recording, so it was served. A change that reaches only a masked value, such as an "
        "id or a date, or a Store isn't seen.",
    ),
    "ship": (
        "🟢 lgcov: SHIP ({blocking})",
        "Review as usual. SHIP covers these KPIs on the recorded cases, nothing else.",
    ),
    "no_drift_no_kpi": (
        "🟢 lgcov: no drift (no blocking KPI judged)",
        "Review as usual. lgcov compared the recorded runs; it judged no KPI that can block.",
    ),
}
_FINDINGS = ("agent_failure", "policy_code_check", "policy_drift", "block", "policy_floor")
_SWITCHABLE = ("not_checked", "abstained", "inconclusive")  # the switch can make these fail
_INCONCLUSIVE_NOTE = (
    "This check exits 0 because INCONCLUSIVE does not block by default. That is not a pass."
)
_UNDECIDED = {  # each state a human decides, and what its exit 0 means
    **dict.fromkeys(_SWITCHABLE, _INCONCLUSIVE_NOTE),
    "cassettes_edited": "This check exits 0 so that a re-baseline can land. That is not a pass.",
    "drift_not_enforced": (
        "This check exits 0 because `fail-on: never` does not block on drift. That is not a pass."
    ),
    "advisory_drop_only": (
        "This check exits 0 because an advisory KPI can't block. That is not a pass."
    ),
}
_SWITCHED_NOTE = (
    "This check fails because `fail-on-inconclusive` is on; that is not a finding against the "
    "agent."
)
_ADVISORY_LINE = (
    "Advisory mode: lgcov reports what it found. It does not block this merge and it does not "
    "approve this PR. Only a harness error fails this check."
)


@dataclass(frozen=True)
class CodeFailure:
    """A blocking code check that passed on the base and fails on this PR."""

    kpi: str
    cases: tuple[str, ...]
    of: int  # the cases compared


@dataclass(frozen=True)
class GateFindings:
    """What the KPI gate adds to the evidence."""

    statistical: Decision | None = None  # None: no blocking KPI judged
    k_gate: int = 0
    abstained: tuple[str, ...] = ()  # blocking KPIs whose judge didn't commit
    advisory_drops: tuple[str, ...] = ()  # advisory KPIs whose BLOCK can't act on the PR
    infra: bool = False  # a blocking KPI with no valid judgment on a side
    code_failures: tuple[CodeFailure, ...] = ()


def _judge_abstained(s: KpiStats) -> bool:
    """A BLOCK it wasn't sure of or can't stand behind (flag band), or too few judgments ran."""
    if s.decision == "BLOCK" and s.outcome == "needs_human":
        return True
    return s.judge_unreliable and s.outcome in ("pass", "needs_human")


def gate_findings(stats: Sequence[KpiStats], groups: Sequence[Group] = ()) -> GateFindings:
    blocking = [s for s in stats if s.blocking]
    outcomes = {s.outcome for s in blocking} | {g.outcome for g in groups if g.blocking}
    statistical: Decision | None = None
    if blocking:
        statistical = "SHIP" if outcomes == {"pass"} else "INCONCLUSIVE"
        if "fail" in outcomes or failing_groups(groups):
            statistical = "BLOCK"
    return GateFindings(
        statistical,
        k_gate(stats),
        tuple(s.kpi for s in blocking if _judge_abstained(s)),
        tuple(s.kpi for s in stats if not s.blocking and (s.decision == "BLOCK" or s.new_failures)),
        any(s.outcome == "infra_error" for s in blocking),
        tuple(CodeFailure(s.kpi, s.new_failures, len(s.cases)) for s in blocking if s.new_failures),
    )


@dataclass(frozen=True)
class Evidence:
    """What one `lgcov ci` run found, and what the repo lets it do about that."""

    statistical: Decision | None = None
    k_gate: int = 0
    infra: bool = False  # T074: harness or judge trouble, whatever else the run found
    crashed: bool = False  # a node raised
    enforced_drift: tuple[str, ...] = ()  # drift fail-on cares about, a policy violation: its nodes
    unenforced_drift: bool = False  # drift it doesn't care about
    under_floor: bool = False  # node coverage under the floor, a policy violation
    not_checked: tuple[str, ...] = ()  # T089: nodes replay stopped before checking
    abstained: tuple[str, ...] = ()
    advisory_drops: tuple[str, ...] = ()
    nothing_ran: bool = False  # every node was served, so this run measured nothing
    first_adoption: bool = False  # T154: the base has no policy yet, so this PR's own is used
    cassettes_edited: bool = False  # T154: this PR changed the expected outputs it is graded on
    code_failures: tuple[CodeFailure, ...] = ()  # a policy violation, whatever the test says
    inconclusive_blocks: bool = False  # the switch: an undecided run fails the check
    mode: Mode = "enforcing"
    approved_by: tuple[str, ...] = ()  # recorded only
    ci_status: CiStatus = ""


@dataclass(frozen=True)
class Action:
    """One row of docs/verdict-actions.md, filled in for this run."""

    state: str
    headline: str
    next_step: str
    exit_code: int
    verdict: ReviewVerdict
    details: tuple[str, ...]  # lines the comment shows under the next step


def _finding(e: Evidence) -> str:
    """The row of a real finding about the agent, or ''."""
    if e.crashed:
        return "agent_failure"
    if e.code_failures:  # before drift: its next step, re-baselining, would erase the failure
        return "policy_code_check"
    if e.enforced_drift:
        return "policy_drift"
    if e.statistical == "BLOCK":
        return "block"
    return "policy_floor" if e.under_floor else ""


def _state(e: Evidence) -> str:
    """The first row of the table that matches."""
    finding = _finding(e)
    if e.infra and not finding:
        return "infra"
    if e.first_adoption:
        return "first_adoption"
    if finding:
        return finding
    if e.not_checked:
        return "not_checked"
    if e.abstained:
        return "abstained"
    if e.statistical == "INCONCLUSIVE":
        return "inconclusive"
    if e.cassettes_edited:
        return "cassettes_edited"
    if e.statistical is None and e.unenforced_drift:
        return "drift_not_enforced"
    if e.statistical is None and e.advisory_drops:
        return "advisory_drop_only"
    if e.statistical == "SHIP" and e.advisory_drops:
        return "ship_advisory_drop"
    if e.nothing_ran:
        return "nothing_ran"
    return "ship" if e.statistical == "SHIP" else "no_drift_no_kpi"


def _code(names: Sequence[str]) -> str:
    return ", ".join("`" + name.replace("`", "'") + "`" for name in names)


def _details(e: Evidence) -> tuple[str, ...]:
    statistical: str = e.statistical or "none (no blocking KPI judged)"
    if e.first_adoption and e.statistical in ("SHIP", "BLOCK"):
        statistical = f"would {statistical}"  # T154: the policy behind it is this PR's own
    status = [f"Statistical verdict: {statistical}"]
    if e.approved_by:
        names = _code(e.approved_by)
        status.append(f"Human approval: {names} (recorded; it does not change the verdict)")
    if e.ci_status:
        status.append(f"Other CI: {e.ci_status}")
    return (" · ".join(status), *((_ADVISORY_LINE,) if e.mode == "advisory" else ()))


def decide_action(e: Evidence) -> Action:
    state = _state(e)
    fill = {
        "code_failures": "; ".join(
            f"`{f.kpi}` passed on the base and fails here on {len(f.cases)} of {f.of} cases "
            f"({named_cases(f.cases)})"
            for f in e.code_failures
        ),
        "root": ",".join(e.enforced_drift),
        "not_checked": _code(e.not_checked),
        "abstained": _code(e.abstained),
        "dropped": _code(e.advisory_drops),
        "blocking": f"{e.k_gate} blocking KPI{'' if e.k_gate == 1 else 's'}",
    }
    headline, step = (text.format(**fill) for text in _ROWS[state])
    advisory = e.mode == "advisory"
    code = 0
    verdict: ReviewVerdict = "pass"
    if state == "infra":
        code, verdict = 2, "error"
    elif state == "first_adoption":
        code = 2 if e.infra else 0
        verdict = "advisory"
    elif state in _FINDINGS:
        verdict = "fail"
        code = (2 if e.infra else 0) if advisory else 1  # a finding wins over an infra failure
        headline += " (advisory, not blocking)" if advisory else ""
    elif state in _UNDECIDED:
        verdict = "needs_human"
        if e.approved_by:
            names = _code(e.approved_by)
            step = f"{names} approved, so the merge is their call. lgcov still makes no claim."
        code = 1 if e.inconclusive_blocks and state in _SWITCHABLE and not advisory else 0
        step += "" if advisory else " " + (_SWITCHED_NOTE if code else _UNDECIDED[state])
    if e.infra and state != "infra":
        step += " Then fix the harness error and re-run."
    if e.ci_status in ("failure", "cancelled"):
        step = f"Fix the failing CI first. {step}"
    return Action(state, headline, step, code, verdict, _details(e))
