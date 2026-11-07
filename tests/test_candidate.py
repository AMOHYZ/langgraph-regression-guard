"""T179: the file the job that runs a PR's code hands the job that decides. That code can write
anything in it, so it says what ran, never what that means, and one that isn't exactly this shape
is refused."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lgcov.candidate import CandidateRun, FreshRun, candidate_to_json, parse_candidate
from lgcov.cassette import Call, Halt, Step
from lgcov.snapshot import Edge, GraphSnapshot

HEAD = GraphSnapshot(frozenset({"a", "b"}), frozenset({Edge("a", "b")}))
ROUTED = ["c", {"send": "d", "arg": {"y": 2}}]
RUN = CandidateRun(
    commit="a" * 40,
    tier="pr",
    head=HEAD,
    graph_lines=("No graph changes.",),
    graph_note="a note",
    lint_summary="🧹 Lint: 1 warning",
    lint_lines=("warning `a`: why. Fix: how.",),
    fresh=(
        FreshRun(
            (Step("a", {"x": 1}, {"q": "hi"}, "code"), Step("b", None, goto=ROUTED)),
            (Call("a", "served"), Call("b", "live", 0.5, rebind_skipped=True, raised=True)),
            Halt("b", "budget"),
        ),
        FreshRun((), ()),
    ),
    agent_seconds=1.5,
    budget_hit=("b", 1.25),
    seconds=2.5,
    harness_node="b",
    crash="Node `b` raised ValueError: boom",
    crash_node="b",
)
BODY: dict[str, object] = json.loads(candidate_to_json(RUN))


def test_a_run_survives_the_round_trip() -> None:
    assert parse_candidate(candidate_to_json(RUN)) == RUN


def test_a_run_that_never_loaded_its_graph_survives_the_round_trip() -> None:
    run = CandidateRun(commit="a" * 40, harness_error="cannot load graph: boom")
    assert parse_candidate(candidate_to_json(run)) == run


def test_the_file_says_what_ran_and_never_what_it_means() -> None:
    assert set(BODY) == {
        "agent_seconds",
        "budget_hit",
        "commit",
        "crash",
        "crash_node",
        "graph_lines",
        "graph_note",
        "harness_error",
        "harness_node",
        "head",
        "lint_lines",
        "lint_summary",
        "runs",
        "schema",
        "seconds",
        "tier",
    }


def _one_step(step: Step) -> Step:
    run = CandidateRun(head=HEAD, fresh=(FreshRun((step,), ()),))
    return parse_candidate(candidate_to_json(run)).fresh[0].steps[0]


def test_messages_come_back_as_messages() -> None:
    call = {"name": "search", "args": {"q": "x"}, "id": "call_1"}
    said = [AIMessage("", tool_calls=[call]), ToolMessage("found", tool_call_id="call_1")]
    assert _one_step(Step("a", {"messages": said}, {"messages": said})).output == {"messages": said}


def test_a_message_s_response_headers_stay_out_of_the_file() -> None:
    # They can hold cookies, and this file goes up as an artifact, as a cassette gets committed.
    said = AIMessage("hi", response_metadata={"headers": {"set-cookie": "session=abc"}})
    run = CandidateRun(head=HEAD, fresh=(FreshRun((Step("a", {"messages": [said]}),), ()),))
    assert "session=abc" not in candidate_to_json(run)


def test_what_json_cannot_hold_is_saved_as_its_repr() -> None:
    # It only gets this far under a path lgcov.yaml masks, so nothing compares it.
    assert _one_step(Step("a", {"masked": {1}})).output == {"masked": "{1}"}


def _with_run(**changes: object) -> dict[str, object]:
    return {**BODY, "runs": [{"steps": [], "calls": [], "halt": None, **changes}]}


GOOD_CALL = {"node": "a", "how": "live", "seconds": 0.5, "rebind_skipped": False, "raised": False}
REFUSED = {
    "a list": [],
    "empty": {},
    "another schema": {**BODY, "schema": 99},
    "a verdict": {**BODY, "verdict": "pass"},
    "drift": {**BODY, "drifts": []},
    "a field missing": {k: v for k, v in BODY.items() if k != "runs"},
    "no graph and no error": {**BODY, "head": None, "harness_error": ""},
    "a snapshot of another version": {**BODY, "head": {"version": 99, "nodes": [], "edges": []}},
    "a snapshot that isn't one": {**BODY, "head": "a graph"},
    "a number for text": {**BODY, "commit": 1},
    "a number in the lines": {**BODY, "graph_lines": [1]},
    "text for seconds": {**BODY, "agent_seconds": "1"},
    "true for seconds": {**BODY, "seconds": True},
    "a budget hit with no seconds": {**BODY, "budget_hit": ["a"]},
    "a budget hit with no node": {**BODY, "budget_hit": [1, 1.0]},
    "runs that aren't a list": {**BODY, "runs": {}},
    "a run that isn't one": {**BODY, "runs": [1]},
    "a run with a verdict": _with_run(verdict="pass"),
    "steps that aren't a list": _with_run(steps={}),
    "a step with no node": _with_run(steps=[{"output": 1}]),
    "a node that isn't text": _with_run(steps=[{"node": 1, "output": 1}]),
    "a step with no output": _with_run(steps=[{"node": "a"}]),
    "a step with an extra field": _with_run(steps=[{"node": "a", "output": 1, "drifted": False}]),
    "a code that isn't text": _with_run(steps=[{"node": "a", "output": 1, "code": 1}]),
    "a goto that isn't a list": _with_run(steps=[{"node": "a", "output": 1, "goto": "b"}]),
    "calls that aren't a list": _with_run(calls={}),
    "a call of another kind": _with_run(calls=[{**GOOD_CALL, "how": "passed"}]),
    "a call with text for seconds": _with_run(calls=[{**GOOD_CALL, "seconds": "1"}]),
    "a call with an extra field": _with_run(calls=[{**GOOD_CALL, "drifted": False}]),
    "a call with text for raised": _with_run(calls=[{**GOOD_CALL, "raised": "no"}]),
    "a halt for another reason": _with_run(halt={"node": "a", "why": "passed"}),
    "a halt with no node": _with_run(halt={"why": "budget"}),
    "a message of no known type": _with_run(
        steps=[{"node": "a", "output": {"langchain_message": {"type": "evil", "data": {}}}}]
    ),
    "a message with no content": _with_run(
        steps=[{"node": "a", "output": {"langchain_message": {"type": "ai", "data": 1}}}]
    ),
}


@pytest.mark.parametrize("body", REFUSED.values(), ids=REFUSED.keys())
def test_what_is_not_exactly_a_run_is_refused(body: object) -> None:
    with pytest.raises(ValueError):
        parse_candidate(json.dumps(body))


def test_what_is_not_json_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_candidate("not json")
