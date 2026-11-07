"""T121: scoring the synthetic suite: pure rates on small hand-made votes, then the real suite."""

import json
from dataclasses import replace
from functools import cache

from examples.synthetic_agents import AGENTS, DEMO, FANOUT
from lgcov.cassette import Cassette, Step, record
from lgcov.synthetic import Case, GroundTruth, aa_arms, every_case, generate
from lgcov.synthetic_score import (
    METHODS,
    Rate,
    Report,
    Vote,
    build_report,
    by_edit,
    end_to_end_flags,
    first_divergence,
    rates,
    render_section,
    replace_section,
    report_json,
    votes_for,
)


def _vote(*, regressed: bool, activated: bool, named: bool, hit: bool) -> Vote:
    return Vote(regressed, activated, named, hit)


def _case(steps_base: list[Step], steps_pr: list[Step], regressed: bool = True) -> Case:
    base = Cassette({"question": "q"}, steps_base)
    pr = Cassette({"question": "q"}, steps_pr)
    truth = GroundTruth("edited", "node_body", steps_base != steps_pr, regressed)
    return Case("op", 0, "edit", base, pr, truth, None, "agent")


def test_the_4_rates_on_hand_made_votes() -> None:
    votes = [
        _vote(regressed=True, activated=True, named=True, hit=True),  # caught
        _vote(regressed=True, activated=True, named=True, hit=False),  # named the wrong node
        _vote(regressed=True, activated=True, named=False, hit=False),  # missed
        _vote(regressed=False, activated=True, named=True, hit=True),  # a harmless change flagged
        _vote(regressed=False, activated=True, named=False, hit=False),  # left alone
        _vote(regressed=False, activated=False, named=True, hit=True),  # nothing changed, flagged
    ]
    found = rates(votes)
    assert (found.precision.hits, found.precision.of) == (1, 4)
    assert (found.recall.hits, found.recall.of) == (1, 3)
    assert (found.coverage.hits, found.coverage.of) == (2, 5)
    assert (found.false_flag.hits, found.false_flag.of) == (1, 2)


def test_a_rate_with_nothing_to_count_has_no_value_rather_than_a_zero() -> None:
    assert Rate(0, 0).value is None
    assert Rate(0, 0).interval is None
    assert Rate(1, 4).value == 0.25
    interval = Rate(1, 4).interval
    assert interval is not None and interval[0] < 0.25 < interval[1]


def test_an_edit_gets_one_vote_by_majority_across_its_questions() -> None:
    yes = _vote(regressed=True, activated=True, named=True, hit=True)
    no = _vote(regressed=False, activated=False, named=False, hit=False)
    assert by_edit({"a": [yes, yes, no], "b": [yes, no, no]}) == [yes, no]


def test_a_tied_vote_does_not_count_as_a_yes() -> None:
    yes = _vote(regressed=True, activated=True, named=True, hit=True)
    no = _vote(regressed=False, activated=False, named=False, hit=False)
    assert by_edit({"a": [yes, no]}) == [no]


def test_first_divergence_names_the_first_step_that_differs() -> None:
    same, changed = Step("a", {"x": 1}), Step("b", {"x": 1})
    case = _case([same, changed, Step("c", {"x": 1})], [same, Step("b", {"x": 2}), Step("c", {})])
    assert first_divergence(case) == {"b"}


def test_first_divergence_names_nothing_on_an_unchanged_run() -> None:
    steps = [Step("a", {"x": 1}), Step("b", {"x": 1})]
    assert first_divergence(_case(steps, steps)) == set()


def test_first_divergence_names_a_recorded_step_the_run_no_longer_reaches() -> None:
    steps = [Step("a", {"x": 1}), Step("b", {"x": 1})]
    assert first_divergence(_case(steps, steps[:1])) == {"b"}


def test_first_divergence_ignores_the_order_parallel_workers_finish_in() -> None:
    a, b, c = Step("a", {"x": 1}), Step("b", {"x": 1}), Step("c", {"x": 1})
    assert first_divergence(_case([a, b, c], [b, a, c])) == set()


def test_end_to_end_flags_a_changed_last_output_and_nothing_else() -> None:
    a, last = Step("a", {"x": 1}), Step("z", {"answer": "old"})
    assert end_to_end_flags(_case([a, last], [a, Step("z", {"answer": "new"})]))
    assert not end_to_end_flags(_case([a, last], [Step("a", {"x": 2}), last]))


def _lgcov_on_nothing(case: Case) -> set[str]:
    return set()


def test_the_edited_node_method_always_names_the_edited_node() -> None:
    case = _case([Step("a", {})], [Step("a", {})], regressed=False)
    vote = votes_for(METHODS["edited node from the diff"], case)
    assert (vote.named, vote.hit, vote.activated, vote.regressed) == (True, True, False, False)


def test_a_method_that_names_nothing_gets_no_credit() -> None:
    vote = votes_for(_lgcov_on_nothing, _case([Step("a", {})], [Step("a", {"x": 1})]))
    assert (vote.named, vote.hit, vote.regressed) == (False, False, True)


def test_end_to_end_names_no_node() -> None:
    case = _case([Step("a", {})], [Step("a", {"x": 1})])
    assert METHODS["end-to-end"](case) == set()


@cache
def _suite() -> tuple[Case, ...]:
    return tuple(case for agent in AGENTS for case in every_case(agent))


def test_a_dropped_fan_out_branch_diverges_at_the_reducer_not_the_edge() -> None:
    base = record(FANOUT.graph, {"question": "Where is my order?"})
    mutations = FANOUT.mutations["send_fan_out_drop"]
    case = generate("send_fan_out_drop", mutations[:1], (base,), FANOUT.outcome_ok, 0)
    assert first_divergence(case) == {"reducer"}
    assert case.truth.edited_node == "plan"


def test_the_edited_node_method_on_the_suite_has_full_recall_and_coverage() -> None:
    found = rates([votes_for(METHODS["edited node from the diff"], c) for c in _suite()])
    assert found.recall.value == 1.0 and found.coverage.value == 1.0


def test_end_to_end_ignores_a_changed_fingerprint_when_the_answer_is_the_same() -> None:
    a, last = Step("a", {"x": 1}), Step("z", {"answer": "same"}, code="old")
    assert not end_to_end_flags(_case([a, last], [Step("a", {"x": 2}), replace(last, code="new")]))


def _lgcov(case: Case) -> set[str]:
    return first_divergence(case)  # a stand-in: the real root needs the CLI's replay


def _report() -> Report:
    return build_report(
        _suite(), _lgcov, [("demo", r) for r in aa_arms(DEMO, reruns=1)], "2026-01-02"
    )


def test_a_report_counts_cases_and_distinct_edits() -> None:
    report = _report()
    assert (report.cases, report.edits, report.agents) == (72, 24, 3)
    assert report.labels == {"regressed": 31, "benign": 24, "inert": 17}


def test_the_narrow_rows_leave_out_only_the_router_and_fan_out_edits() -> None:
    narrow = [c for c in _suite() if c.truth.edit_kind == "node_body"]
    assert {c.operator for c in _suite()} - {c.operator for c in narrow} == {
        "router_flip",
        "send_fan_out_drop",
    }
    expected = rates([votes_for(METHODS["first divergence"], c) for c in narrow])
    row = next(r for r in _report().single_node if r.method == "first divergence")
    assert row.cases == expected


def test_the_section_shows_every_method_the_false_flags_and_both_counts() -> None:
    section = render_section(_report())
    assert section.startswith("## Synthetic\n")
    assert "72 cases from 24 distinct edits" in section
    for method in ("lgcov root", *METHODS):
        assert f"| {method} |" in section
    assert "False flag" in section
    assert "One vote per edit" in section


def test_the_section_says_what_the_user_needs_to_know_plainly() -> None:
    section = render_section(_report())
    assert "edited node from the diff matches or beats lgcov" in section.lower()
    assert "T182" in section
    assert "parent-only naming counts as a miss" in section
    assert "wiring test, noise rate set by us, not a real-world flip estimate" in section


def test_a_rate_with_nothing_to_count_shows_a_dash() -> None:
    row = next(r for r in _report().all_edits if r.method == "end-to-end")
    assert row.cases.precision.value is None
    assert "| end-to-end | — |" in render_section(_report())


def test_the_json_holds_the_numbers() -> None:
    raw = json.loads(report_json(_report()))
    assert (raw["cases"], raw["edits"]) == (72, 24)
    first = raw["all_edits"][0]
    assert first["method"] == "lgcov root"
    assert set(first["cases"]) == {"precision", "recall", "coverage", "false_flag"}
    assert raw["aa"][0]["agent"] == "demo"


REAL = "# T\n\n## Demo\n\nx\n\n## Real PRs\n\nkeep\n"
HELD = "# T\n\n## Demo\n\nx\n\n## Synthetic\n\nTODO(T121)\n\n## Real PRs\n\nkeep\n"


def test_a_new_section_goes_before_real_prs() -> None:
    assert replace_section(REAL, "## Synthetic\n\nnew\n") == (
        "# T\n\n## Demo\n\nx\n\n## Synthetic\n\nnew\n\n## Real PRs\n\nkeep\n"
    )


def test_a_section_already_there_is_replaced_and_nothing_else_moves() -> None:
    assert replace_section(HELD, "## Synthetic\n\nnew\n") == (
        "# T\n\n## Demo\n\nx\n\n## Synthetic\n\nnew\n\n## Real PRs\n\nkeep\n"
    )


def test_writing_the_section_twice_changes_nothing() -> None:
    once = replace_section(REAL, "## Synthetic\n\nnew\n")
    assert replace_section(once, "## Synthetic\n\nnew\n") == once


def test_a_doc_with_no_real_prs_gets_the_section_at_its_end() -> None:
    assert replace_section("# T\n", "## Synthetic\n\nnew\n") == "# T\n\n## Synthetic\n\nnew\n"
