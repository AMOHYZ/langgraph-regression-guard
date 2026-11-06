"""T153: one gate outcome, one action. decide_action against docs/verdict-actions.md, its
rules over every combination of inputs, the PR comment for each state, and the gate states
the comment used to misreport."""

from collections.abc import Iterator
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest

from lgcov.ci_report import MARKER, CiResult, Timing, render_ci_report
from lgcov.judges import KPI
from lgcov.sampling import Sample, gate_kpis, kpi_stats
from lgcov.slices import group_kpis
from lgcov.stats import Decision
from lgcov.verdict_actions import (
    MODES,
    Action,
    Evidence,
    GateFindings,
    decide_action,
    gate_findings,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "verdict-actions.md"
SNAPSHOTS = Path(__file__).parent / "fixtures" / "verdict_actions"
NO_DECISION = "⚠️ No decision: needs_human"

# One Evidence for each row of the doc's table, in its order; `_filled` matches its placeholders.
ROWS: dict[str, Evidence] = {
    "infra": Evidence(infra=True),
    "first_adoption": Evidence(first_adoption=True, statistical="SHIP", k_gate=2),
    "agent_failure": Evidence(crashed=True),
    "policy_drift": Evidence(enforced_drift=("respond",), statistical="SHIP", k_gate=2),
    "block": Evidence(statistical="BLOCK", k_gate=2),
    "policy_floor": Evidence(under_floor=True, statistical="SHIP", k_gate=2),
    "not_checked": Evidence(not_checked=("respond",)),
    "abstained": Evidence(abstained=("tone",), statistical="INCONCLUSIVE", k_gate=1),
    "inconclusive": Evidence(statistical="INCONCLUSIVE", k_gate=1),
    "cassettes_edited": Evidence(cassettes_edited=True, statistical="SHIP", k_gate=2),
    "drift_not_enforced": Evidence(unenforced_drift=True),
    "advisory_drop_only": Evidence(advisory_drops=("tone",)),
    "ship_advisory_drop": Evidence(statistical="SHIP", k_gate=2, advisory_drops=("tone",)),
    "nothing_ran": Evidence(nothing_ran=True, statistical="SHIP", k_gate=2),
    "ship": Evidence(statistical="SHIP", k_gate=2),
    "no_drift_no_kpi": Evidence(),
}


def _doc_rows() -> dict[str, list[str]]:
    rows = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 8 and cells[0].isdigit():
            rows[cells[1].strip("`")] = cells
    return rows


def _filled(cell: str) -> str:
    for placeholder, value in (
        ("<root>", "respond"),
        ("<nodes>", "respond"),
        ("<kpi>", "tone"),
        ("N blocking", "2 blocking"),
    ):
        cell = cell.replace(placeholder, value)
    return cell


def test_the_doc_lists_every_state_in_the_order_they_are_decided() -> None:
    assert list(_doc_rows()) == list(ROWS)


@pytest.mark.parametrize("state", ROWS)
def test_the_doc_table_matches_decide_action(state: str) -> None:
    _, _, _, headline, step, enforcing, advisory, verdict = _doc_rows()[state]
    action = decide_action(ROWS[state])
    assert action.state == state
    assert action.headline == _filled(headline)
    assert action.next_step.startswith(_filled(step))
    assert action.verdict == verdict.strip("`")
    assert str(action.exit_code) == enforcing.split(",")[0]
    in_advisory = decide_action(replace(ROWS[state], mode="advisory"))
    assert str(in_advisory.exit_code) == advisory.split(",")[0]
    switched = decide_action(replace(ROWS[state], inconclusive_blocks=True))
    assert switched.exit_code == (1 if "1 with the switch" in enforcing else action.exit_code)


def _every_combination() -> Iterator[Evidence]:
    verdicts: tuple[Decision | None, ...] = ("SHIP", "BLOCK", "INCONCLUSIVE", None)
    for statistical, mode in product(verdicts, MODES):
        for flags in product((False, True), repeat=12):
            infra, crashed, drift, unenforced, floor, unchecked = flags[:6]
            unsure, drop, idle, blocks, adopting, edited = flags[6:]
            yield Evidence(
                statistical=statistical,
                k_gate=0 if statistical is None else 2,
                infra=infra,
                crashed=crashed,
                enforced_drift=("respond",) if drift else (),
                unenforced_drift=unenforced,
                under_floor=floor,
                not_checked=("respond",) if unchecked else (),
                abstained=("tone",) if unsure else (),
                advisory_drops=("polite",) if drop else (),
                nothing_ran=idle,
                inconclusive_blocks=blocks,
                first_adoption=adopting,
                cassettes_edited=edited,
                mode=mode,
            )


def _finding(e: Evidence) -> bool:
    return e.crashed or bool(e.enforced_drift) or e.statistical == "BLOCK" or e.under_floor


def _shown(a: Action) -> str:
    return " ".join((a.headline, a.next_step, *a.details))


def test_infra_alone_exits_2_and_is_never_a_pass_or_a_fail_of_the_agent() -> None:
    for e in _every_combination():
        if e.infra and not _finding(e):
            a = decide_action(e)
            assert (a.state, a.exit_code, a.verdict) == ("infra", 2, "error")
            assert "🟢" not in a.headline and "🔴" not in a.headline


def test_inconclusive_is_never_green_and_nothing_is_ever_called_safe() -> None:
    for e in _every_combination():
        a = decide_action(e)
        assert "safe" not in _shown(a).lower()
        if e.statistical == "INCONCLUSIVE":
            assert "🟢" not in a.headline
            if not e.infra and not _finding(e) and not e.first_adoption:
                assert a.headline == NO_DECISION and a.verdict == "needs_human"


def test_a_policy_violation_beats_a_statistical_ship() -> None:
    for e in _every_combination():
        if e.statistical == "SHIP" and (e.enforced_drift or e.under_floor) and not e.first_adoption:
            a = decide_action(e)
            assert a.verdict == "fail" and "SHIP" not in a.headline
            assert "Statistical verdict: SHIP" in _shown(a)  # still reported, as it was
            assert a.exit_code == (1 if e.mode == "enforcing" else 2 if e.infra else 0)


def test_approval_and_other_ci_change_no_headline_exit_code_or_verdict() -> None:
    for e in _every_combination():
        a = decide_action(e)
        for ci_status in ("success", "failure"):
            b = decide_action(replace(e, approved_by=("alice",), ci_status=ci_status))
            assert (b.state, b.headline, b.exit_code, b.verdict) == (
                a.state,
                a.headline,
                a.exit_code,
                a.verdict,
            )
            assert b.details[0].startswith(a.details[0])  # the statistical text comes first
            assert "Human approval: `alice` (recorded;" in b.details[0]
            assert f"Other CI: {ci_status}" in b.details[0]


def test_advisory_mode_exits_0_unless_infra_and_says_what_it_authorises() -> None:
    authorised = "It does not block this merge and it does not approve this PR."
    for e in _every_combination():
        a = decide_action(e)
        if e.mode == "advisory":
            assert a.exit_code == (2 if e.infra else 0)
            assert authorised in _shown(a)
            assert ("(advisory, not blocking)" in a.headline) == (a.verdict == "fail")
        else:
            assert authorised not in _shown(a)
            assert "(advisory, not blocking)" not in a.headline


def test_a_first_adoption_pr_enforces_nothing_and_claims_no_ship() -> None:
    for e in _every_combination():
        if e.first_adoption and (_finding(e) or not e.infra):
            a = decide_action(e)
            assert (a.state, a.verdict) == ("first_adoption", "advisory")
            assert a.exit_code == (2 if e.infra else 0)  # an infra failure still exits 2
            assert "🟢" not in a.headline and "SHIP" not in a.headline
            assert "Statistical verdict: SHIP" not in _shown(a)
            assert "Statistical verdict: BLOCK" not in _shown(a)


def test_a_pr_that_edits_its_own_cassettes_can_never_ship() -> None:
    for e in _every_combination():
        if e.cassettes_edited and not (e.infra or e.first_adoption or _finding(e)):
            a = decide_action(e)
            assert (a.headline, a.verdict) == (NO_DECISION, "needs_human")


def test_a_re_baseline_can_still_land_with_the_switch_on() -> None:
    # T154: the gate honours a PR's cassettes, or `cassette accept` could never land.
    action = decide_action(replace(ROWS["cassettes_edited"], inconclusive_blocks=True))
    assert (action.state, action.exit_code) == ("cassettes_edited", 0)


def test_enforcing_mode_exits_by_what_the_run_found() -> None:
    for e in _every_combination():
        if e.mode == "enforcing" and not e.first_adoption:
            a = decide_action(e)
            if _finding(e):
                assert a.exit_code == 1  # a finding wins over an infra failure
            elif e.infra:
                assert a.exit_code == 2
            elif e.inconclusive_blocks and (
                e.not_checked or e.abstained or e.statistical == "INCONCLUSIVE"
            ):
                assert a.exit_code == 1 and a.verdict == "needs_human"
            else:
                assert a.exit_code == 0


def test_an_undecided_run_says_what_its_exit_code_means() -> None:
    not_a_pass = "This check exits 0 because INCONCLUSIVE does not block by default."
    assert not_a_pass in decide_action(ROWS["inconclusive"]).next_step
    assert "That is not a pass." in decide_action(ROWS["inconclusive"]).next_step
    switched = decide_action(replace(ROWS["inconclusive"], inconclusive_blocks=True))
    assert "that is not a finding against the agent" in switched.next_step
    for state in ("cassettes_edited", "drift_not_enforced", "advisory_drop_only"):
        assert "That is not a pass." in decide_action(ROWS[state]).next_step
    assert "exits 0" not in decide_action(replace(ROWS["inconclusive"], mode="advisory")).next_step


def test_an_approval_hands_an_undecided_run_to_the_approver_and_nothing_else() -> None:
    for evidence in ROWS.values():
        step = decide_action(replace(evidence, approved_by=("alice", "bob"))).next_step
        handed = "`alice`, `bob` approved, so the merge is their call." in step
        assert handed == (decide_action(evidence).verdict == "needs_human")


def test_failing_other_ci_comes_first_in_the_next_step() -> None:
    for ci_status in ("failure", "cancelled"):
        step = decide_action(replace(ROWS["ship"], ci_status=ci_status)).next_step
        assert step.startswith("Fix the failing CI first. Review as usual.")
    assert decide_action(replace(ROWS["ship"], ci_status="success")).next_step.startswith("Review")
    assert decide_action(replace(ROWS["ship"], ci_status="skipped")).next_step.startswith("Review")


def test_a_name_cannot_break_out_of_its_code_span() -> None:
    action = decide_action(replace(ROWS["ship"], approved_by=("a`b",)))
    assert "Human approval: `a'b` (recorded;" in action.details[0]


NO_DRIFT = ("### Cassette replay", "", "No cassette drift (2 runs).")
DRIFT = (
    "### Cassette replay",
    "",
    "| node | drift | runs |",
    "|---|---|---|",
    "| `respond` | root | 2/2 |",
)
RAN = Timing(12.0, 9.0, judges=3.0, live=2, served=3, judge_calls=8, budget=90)
BASE = CiResult("root-drift", tier="pr")


def _kpi_rows(*rows: str) -> tuple[str, ...]:
    return ("| KPI | n | verdict |", "|---|---|---|", *rows)


TONE_SHIP = "| tone | 8 | 🟢 SHIP |"
POLITE_DROP = "| polite (advisory) | 8 | ⚠️ BLOCK |"
SHIPPED = replace(BASE, gate=GateFindings("SHIP", 1), l2_lines=_kpi_rows(TONE_SHIP))
BLOCKED = replace(
    BASE, gate=GateFindings("BLOCK", 2), l2_lines=_kpi_rows("| tone | 8 | 🔴 BLOCK |")
)
UNDECIDED = replace(
    BASE,
    gate=GateFindings("INCONCLUSIVE", 1),
    l2_lines=_kpi_rows("| tone | 8 | ⚠️ INCONCLUSIVE |", "| ⚠️ underpowered: need 66 more cases |"),
)
DRIFTED = replace(BASE, replay_lines=DRIFT, root_drift=("respond",))
LOADING = "cannot load graph: ImportError: boom"
L2_CRASHED = "L2 gate failed: RuntimeError: boom"
UNCHECKED = "Not checked (budget hit): run 1 from `respond` (`respond`)."

# Each comment: the run, then the exit code and `verdict` it must give.
COMMENTS: dict[str, tuple[CiResult, int, str]] = {
    # The table's rows.
    "infra": (CiResult("root-drift", harness_error=LOADING), 2, "error"),
    "first_adoption": (
        replace(
            SHIPPED,
            first_adoption=True,
            replay_lines=DRIFT,
            root_drift=("respond",),
            l2_lines=(*SHIPPED.l2_lines, "", "**What SHIP and BLOCK guarantee.** SHIP needs..."),
        ),
        0,
        "advisory",
    ),
    "agent_failure": (
        CiResult(
            "root-drift", crash="Node `respond` raised ValueError: boom", root_drift=("respond",)
        ),
        1,
        "fail",
    ),
    "policy_drift": (replace(SHIPPED, replay_lines=DRIFT, root_drift=("respond",)), 1, "fail"),
    "block": (BLOCKED, 1, "fail"),
    "policy_floor": (replace(SHIPPED, under_floor="Node coverage 50% is under 80%."), 1, "fail"),
    "not_checked": (
        replace(
            BASE,
            not_checked=("respond",),
            replay_lines=(*NO_DRIFT, "", UNCHECKED),
            timing=replace(RAN, budget_hit=("respond", 88.4)),
        ),
        0,
        "needs_human",
    ),
    "abstained": (
        replace(
            BASE,
            gate=GateFindings("INCONCLUSIVE", 1, abstained=("tone",)),
            l2_lines=_kpi_rows("| tone | 8 | ⚠️ INCONCLUSIVE (low judge confidence) |"),
        ),
        0,
        "needs_human",
    ),
    "inconclusive": (UNDECIDED, 0, "needs_human"),
    "cassettes_edited": (
        replace(
            SHIPPED,
            cassettes_edited=True,
            replay_lines=NO_DRIFT,
            notes=("⚠️ This PR changes `cassettes`, the expected outputs this gate compares.",),
        ),
        0,
        "needs_human",
    ),
    "drift_not_enforced": (replace(DRIFTED, fail_on="never"), 0, "needs_human"),
    "advisory_drop_only": (
        replace(
            BASE, gate=GateFindings(advisory_drops=("polite",)), l2_lines=_kpi_rows(POLITE_DROP)
        ),
        0,
        "needs_human",
    ),
    "ship_advisory_drop": (
        replace(
            SHIPPED,
            gate=GateFindings("SHIP", 1, advisory_drops=("polite",)),
            l2_lines=_kpi_rows(TONE_SHIP, POLITE_DROP),
        ),
        0,
        "pass",
    ),
    "nothing_ran": (
        replace(SHIPPED, replay_lines=NO_DRIFT, timing=replace(RAN, live=0, served=5)),
        0,
        "pass",
    ),
    "ship": (replace(SHIPPED, replay_lines=NO_DRIFT, timing=RAN), 0, "pass"),
    "no_drift_no_kpi": (replace(BASE, replay_lines=NO_DRIFT, timing=RAN), 0, "pass"),
    # A finding that stands, with the rest of the run lost to an infra failure.
    "drift_then_infra": (replace(DRIFTED, harness_error=L2_CRASHED), 1, "fail"),
    "drift_then_infra_advisory": (
        replace(DRIFTED, harness_error=L2_CRASHED, mode="advisory"),
        2,
        "fail",
    ),
    "first_adoption_then_infra": (
        replace(DRIFTED, first_adoption=True, harness_error=L2_CRASHED),
        2,
        "advisory",
    ),
    # The switch.
    "inconclusive_with_the_switch": (
        replace(UNDECIDED, inconclusive_blocks=True),
        1,
        "needs_human",
    ),
    # Advisory mode.
    "advisory_block": (replace(BLOCKED, mode="advisory"), 0, "fail"),
    "advisory_inconclusive": (
        replace(UNDECIDED, mode="advisory", inconclusive_blocks=True),
        0,
        "needs_human",
    ),
    "advisory_ship": (replace(SHIPPED, mode="advisory"), 0, "pass"),
    "advisory_infra": (
        CiResult("root-drift", harness_error=LOADING, mode="advisory"),
        2,
        "error",
    ),
    # Human approval and other CI.
    "approved_inconclusive": (replace(UNDECIDED, approved_by=("alice",)), 0, "needs_human"),
    "approved_block": (replace(BLOCKED, approved_by=("alice",)), 1, "fail"),
    "other_ci_failed_ship": (replace(SHIPPED, ci_status="failure"), 0, "pass"),
}


def test_every_row_has_a_snapshot_and_no_snapshot_is_left_over() -> None:
    assert set(ROWS) <= set(COMMENTS)
    assert {p.stem for p in SNAPSHOTS.glob("*.md")} == set(COMMENTS)


@pytest.mark.parametrize("state", COMMENTS)
def test_the_pr_comment_for_each_state(state: str) -> None:
    result, code, verdict = COMMENTS[state]
    assert (result.action.exit_code, result.action.verdict) == (code, verdict)
    if state in ROWS:
        assert result.action.state == state
    want = (SNAPSHOTS / f"{state}.md").read_text(encoding="utf-8")
    assert render_ci_report(result) == want


TONE = KPI.binary("tone", "the tone is right", "Is the tone right?")
POLITE = KPI.binary("polite", "the answer is polite", "Is the answer polite?")
INFRA = Sample(passed=None, confidence=0.0, ms=1.0)


def _judged(score: float, confidence: float = 0.9) -> Sample:
    return Sample(passed=score >= 0.5, confidence=confidence, ms=1.0, score=score)


def _side(n: int, *samples: Sample) -> dict[str, list[Sample]]:
    return {f"case-{i}": list(samples) for i in range(n)}


def _dropping(n: int, drops: int, confidence: float = 0.9) -> dict[str, list[Sample]]:
    return {f"case-{i}": [_judged(0.0 if i < drops else 1.0, confidence)] for i in range(n)}


def test_an_advisory_kpi_that_drops_is_not_a_green_pass() -> None:
    stats = kpi_stats(TONE, 1, _side(40, _judged(1.0)), _dropping(40, 40))
    found = gate_findings(gate_kpis([stats], advisory={"tone"}))
    assert found == GateFindings(None, 0, advisory_drops=("tone",))
    report = render_ci_report(CiResult("root-drift", gate=found))
    assert report.startswith(f"{MARKER}\n## {NO_DECISION}\n")


def test_an_advisory_kpi_that_only_lacks_cases_is_no_finding() -> None:
    stats = kpi_stats(TONE, 1, _side(4, _judged(1.0)), _side(4, _judged(1.0)))
    found = gate_findings(gate_kpis([stats], advisory={"tone"}))
    assert found == GateFindings(None, 0)


def test_a_ship_with_most_judgments_lost_is_an_abstention_not_a_green_pass() -> None:
    both = _side(120, _judged(1.0), INFRA, INFRA)
    stats = kpi_stats(TONE, 3, both, both)
    assert (stats.decision, stats.judge_unreliable) == ("SHIP", True)
    found = gate_findings([stats])
    assert found == GateFindings("SHIP", 1, abstained=("tone",))
    action = CiResult("root-drift", gate=found).action
    assert (action.state, action.headline, action.exit_code) == ("abstained", NO_DECISION, 0)
    assert "Statistical verdict: SHIP" in action.details[0]


def test_a_block_the_judge_cannot_stand_behind_is_an_abstention() -> None:
    unsure = kpi_stats(TONE, 1, _side(40, _judged(1.0, 0.5)), _dropping(40, 40, 0.5))
    assert (unsure.decision, unsure.outcome) == ("BLOCK", "needs_human")
    assert gate_findings([unsure]) == GateFindings("INCONCLUSIVE", 1, abstained=("tone",))
    sure = kpi_stats(TONE, 1, _side(40, _judged(1.0)), _dropping(40, 40))
    flagged = gate_kpis([sure], flagged={"tone"})
    assert gate_findings(flagged) == GateFindings("INCONCLUSIVE", 1, abstained=("tone",))
    assert gate_findings([sure]) == GateFindings("BLOCK", 1)


def test_a_blocking_group_that_does_not_ship_is_inconclusive_even_when_each_kpi_ships() -> None:
    wide = kpi_stats(TONE, 1, _side(120, _judged(1.0)), _dropping(120, 6), margin=0.15)
    tight = kpi_stats(POLITE, 1, _side(120, _judged(1.0)), _side(120, _judged(1.0)))
    stats = gate_kpis([wide, tight])
    assert [s.outcome for s in stats] == ["pass", "pass"]
    groups = group_kpis(stats, {"tone": ("n", ""), "polite": ("n", "")}, ("node",))
    assert [g.outcome for g in groups] == ["needs_human"]
    assert gate_findings(stats).statistical == "SHIP"
    assert gate_findings(stats, groups).statistical == "INCONCLUSIVE"


def test_a_blocking_group_that_blocks_is_a_block() -> None:
    stats = kpi_stats(TONE, 1, _side(40, _judged(1.0)), _dropping(40, 40))
    groups = group_kpis([stats], {"tone": ("n", "")}, ("node",))
    assert [g.outcome for g in groups] == ["fail"]
    assert gate_findings([stats], groups).statistical == "BLOCK"


def test_only_a_blocking_kpi_with_no_valid_judgment_is_an_infra_failure() -> None:
    lost = kpi_stats(TONE, 1, _side(4, INFRA), _side(4, INFRA))
    assert lost.outcome == "infra_error"
    assert gate_findings([lost]) == GateFindings("INCONCLUSIVE", 1, infra=True)
    assert gate_findings(gate_kpis([lost], advisory={"tone"})) == GateFindings(None, 0)


def test_a_ship_on_a_served_only_run_says_nothing_re_ran() -> None:
    both = _side(120, _judged(1.0))
    found = gate_findings([kpi_stats(TONE, 1, both, both)])
    assert found.statistical == "SHIP"  # the recording, compared with itself
    served = Timing(1.0, 0.5, live=0, served=5, budget=90)
    report = render_ci_report(CiResult("root-drift", gate=found, timing=served))
    assert report.startswith(f"{MARKER}\n## 🟢 lgcov: every node served, nothing re-ran\n")
    assert "Statistical verdict: SHIP" in report
    # T152: serving can be stale when a change reaches only a masked value, so never say that
    # nothing changed.
    assert "no node changed" not in report
    assert "A change that reaches only a masked value" in report


def test_singular_and_plural_blocking_kpis() -> None:
    assert decide_action(Evidence(statistical="SHIP", k_gate=1)).headline.endswith(
        "(1 blocking KPI)"
    )
    assert decide_action(Evidence(statistical="SHIP", k_gate=3)).headline.endswith(
        "(3 blocking KPIs)"
    )
