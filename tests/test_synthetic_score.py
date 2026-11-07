"""T121: scoring the synthetic suite: pure rates on small hand-made votes, then the real suite."""

from dataclasses import replace
from functools import cache

from examples.synthetic_agents import AGENTS, FANOUT
from lgcov.cassette import Cassette, Step, record
from lgcov.synthetic import Case, GroundTruth, every_case, generate
from lgcov.synthetic_score import (
    METHODS,
    Rate,
    Vote,
    by_edit,
    end_to_end_flags,
    first_divergence,
    rates,
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
