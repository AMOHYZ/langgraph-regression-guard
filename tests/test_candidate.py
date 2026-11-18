"""T179: the file the job that runs a PR's code hands the job that decides. That code can write
anything in it, so it says what ran, never what that means, and one that isn't exactly this shape
is refused."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lgcov.attribution import NEEDS_LIVE
from lgcov.candidate import (
    CandidateRun,
    FreshRun,
    candidate_to_json,
    fresh_cassettes,
    parse_candidate,
)
from lgcov.cassette import Call, Cassette, Halt, ProbeStep, Step, runs_from_json
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
            (Step("a", {"x": 1}, {"q": "hi"}, "code", router="r1"), Step("b", None, goto=ROUTED)),
            (Call("a", "served"), Call("b", "live", 0.5, rebind_skipped=True, raised=True)),
            Halt("b", "budget"),
        ),
        FreshRun((), ()),
    ),
    probes=(ProbeStep(0, 0, "a", {"x": 2}, ROUTED), ProbeStep(0, 1, "b", raised=NEEDS_LIVE)),
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
        "probes",
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
GOOD_PROBE = {"run": 0, "step": 0, "node": "a", "output": None, "goto": None, "raised": ""}
# T193, PR 1 part 2: read candidate schema 3 before anything writes it, as schema 2 was (#114
# before #113). Its steps are units, here the schema-2 recording fixture's. A probe names its unit
# by its position down the tree, and a router's probe carries `routed`. Head's parts and its
# repeats come along. Nothing acts on them yet.
RECORDING_2 = (Path(__file__).parent / "fixtures" / "schema2_recording.json").read_text("utf-8")
PROBE_3 = {"run": 0, "unit": [1, 0], "node": "search", "output": 1, "goto": None, "raised": ""}
ROUTER_PROBE = {**PROBE_3, "unit": [0], "node": "plan", "output": None, "routed": ["tools"]}
SCHEMA_3 = {
    **BODY,
    "schema": 3,
    "runs": [{"steps": json.loads(RECORDING_2)["runs"][0]["steps"], "calls": [], "halt": None}],
    "probes": [PROBE_3, ROUTER_PROBE],
    "parts": {"plan": "a1", "prompt": None},
    "repeats": [{**PROBE_3, "unit": [0], "node": "plan", "output": {"x": 2}}],
}


def _with_unit(**changes: object) -> dict[str, object]:
    step = {"node": "a", "output": 1, **changes}
    return {**SCHEMA_3, "runs": [{"steps": [step], "calls": [], "halt": None}]}


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
    "probes that aren't a list": {**BODY, "probes": {}},
    "a probe with a verdict": {**BODY, "probes": [{**GOOD_PROBE, "drifted": True}]},
    "a probe with text for its run": {**BODY, "probes": [{**GOOD_PROBE, "run": "0"}]},
    "a probe with no node": {**BODY, "probes": [{**GOOD_PROBE, "node": None}]},
    "a probe that raised a number": {**BODY, "probes": [{**GOOD_PROBE, "raised": 1}]},
    "steps that aren't a list": _with_run(steps={}),
    "a step with no node": _with_run(steps=[{"output": 1}]),
    "a node that isn't text": _with_run(steps=[{"node": 1, "output": 1}]),
    "a step with no output": _with_run(steps=[{"node": "a"}]),
    "a step with an extra field": _with_run(steps=[{"node": "a", "output": 1, "drifted": False}]),
    "a schema 2 step with a unit's field": _with_run(steps=[{"node": "a", "output": 1, "kind": 1}]),
    "a code that isn't text": _with_run(steps=[{"node": "a", "output": 1, "code": 1}]),
    "a goto that isn't a list": _with_run(steps=[{"node": "a", "output": 1, "goto": "b"}]),
    "a router that isn't text": _with_run(steps=[{"node": "a", "output": 1, "router": 1}]),
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
    "schema 3's fields in schema 2": {**BODY, "parts": {}},
    "a schema 3 probe by step index": {**SCHEMA_3, "probes": [GOOD_PROBE]},
    "a schema 3 probe at no position": {**SCHEMA_3, "probes": [{**PROBE_3, "unit": []}]},
    "a position that isn't numbers": {**SCHEMA_3, "probes": [{**PROBE_3, "unit": ["1"]}]},
    "routes that aren't a list": {**SCHEMA_3, "probes": [{**ROUTER_PROBE, "routed": "tools"}]},
    "repeats that aren't probes": {**SCHEMA_3, "repeats": [1]},
    "parts that aren't fingerprints": {**SCHEMA_3, "parts": {"plan": 1}},
    "a unit of no known kind": _with_unit(kind="model"),
    "units that aren't a list": _with_unit(units={}),
    "a unit inside with an extra field": _with_unit(units=[{"node": "b", "output": 1, "x": 1}]),
    "a status that isn't text": _with_unit(status=1),
    "a call id that isn't text": _with_unit(call_id=1),
    "a unit's parts that aren't names": _with_unit(parts=[1]),
    "a sample with no output": _with_unit(samples=[{"goto": None}]),
}


@pytest.mark.parametrize("body", REFUSED.values(), ids=REFUSED.keys())
def test_what_is_not_exactly_a_run_is_refused(body: object) -> None:
    with pytest.raises(ValueError):
        parse_candidate(json.dumps(body))


def test_what_is_not_json_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_candidate("not json")


def test_a_file_from_another_lgcov_version_says_so() -> None:
    # T162: schema 2 carries the probes; both jobs must run the same lgcov.
    with pytest.raises(
        ValueError, match="^candidate file schema 1, evaluator needs 2: run the same"
    ):
        parse_candidate(json.dumps({**BODY, "schema": 1}))


def test_a_schema_3_file_with_every_field_loads() -> None:
    run = parse_candidate(json.dumps(SCHEMA_3))
    assert run.fresh[0].steps == tuple(runs_from_json(RECORDING_2)[0].steps)  # read as recorded
    search, plan = run.probes
    assert (search.step, search.inner, search.node, search.routed) == (1, [0], "search", None)
    assert (plan.step, plan.inner, plan.output, plan.routed) == (0, None, None, ["tools"])
    assert run.parts == {"plan": "a1", "prompt": None}
    assert run.repeats == (ProbeStep(0, 0, "plan", {"x": 2}),)


def test_a_unit_s_status_may_be_any_text() -> None:
    # T248 hasn't settled its values.
    run = parse_candidate(json.dumps(_with_unit(status="a value T248 never named")))
    assert run.fresh[0].steps[0].status == "a value T248 never named"


def test_a_file_from_a_newer_lgcov_is_refused_by_its_number() -> None:
    with pytest.raises(ValueError, match="^candidate file schema 4; this lgcov reads 2 and 3$"):
        parse_candidate(json.dumps({**BODY, "schema": 4}))


def test_this_lgcov_still_writes_schema_2_with_probes_by_step_index() -> None:
    body = json.loads(candidate_to_json(RUN))
    assert body["schema"] == 2
    by_index = {"run", "step", "node", "output", "goto", "raised"}
    assert all(set(p) == by_index for p in body["probes"])


# T193, 3b-1: a run with units, and a probe of a unit, go in schema 3: steps with their units,
# and each probe by its unit's position down the tree (`unit`: the step's index, then the units').
BOX = Step("box", {"a": "A2"}, {}, None, units=[Step("inside", {"a": "A2"}, {}, "c2")])
RUN_3 = replace(
    RUN, fresh=(FreshRun((BOX,), ()),), probes=(ProbeStep(0, 0, "inside", {"a": "A2"}, inner=[0]),)
)


def test_a_run_with_units_is_written_as_schema_3_with_probes_by_unit() -> None:
    body = json.loads(candidate_to_json(RUN_3))
    assert body["schema"] == 3
    assert [(p["node"], p["unit"]) for p in body["probes"]] == [("inside", [0, 0])]
    assert parse_candidate(candidate_to_json(RUN_3)) == RUN_3


# T186: `cassette accept --candidate-in` takes its fresh runs from the file, so the review commands
# can accept without running the PR's code (docs/review.md).
RECORDED = [
    Cassette({"q": "one"}, [Step("a", 1, {"q": "one"}, "c1"), Step("b", 2, {"a": 1}, "c1")]),
    Cassette({"q": "two"}, [Step("a", 3, {"q": "two"}, "c1"), Step("b", 4, {"a": 3}, "c1")]),
]
RAN = (
    FreshRun((Step("a", 10, {"q": "one"}, "c2"), Step("b", 20, {"a": 10}, "c1")), ()),
    FreshRun((Step("a", 30, {"q": "two"}, "c2"), Step("b", 40, {"a": 30}, "c1")), ()),
)


def test_each_run_in_the_file_stands_in_for_a_fresh_run_of_the_recorded_input() -> None:
    fresh = fresh_cassettes(CandidateRun(head=HEAD, fresh=RAN), RECORDED)
    assert fresh == [
        Cassette({"q": "one"}, list(RAN[0].steps)),
        Cassette({"q": "two"}, list(RAN[1].steps)),
    ]


@pytest.mark.parametrize(
    ("run", "why"),
    [
        (
            CandidateRun(harness_error="Installing your project failed"),
            "^the candidate job hit a harness error, so its file holds no runs to accept$",
        ),
        (
            CandidateRun(head=HEAD, crash="Node `b` raised ValueError: boom", crash_node="b"),
            "^the candidate job's run crashed at node 'b', so nothing after it ran$",
        ),
        (
            CandidateRun(head=HEAD, crash="the graph raised"),
            "^the candidate job's run crashed, so nothing after it ran$",
        ),
        (
            CandidateRun(head=HEAD, fresh=RAN[:1]),
            "^the candidate file holds 1 run and the baseline 2 runs: they must be the same runs$",
        ),
        (
            CandidateRun(head=HEAD, fresh=(*RAN, RAN[0])),
            "holds 3 runs and the baseline 2 runs",
        ),
        (
            CandidateRun(head=HEAD, fresh=(RAN[0], replace(RAN[1], halt=Halt("b", "diverged")))),
            "^run 1 stopped at node 'b' as its input no longer matched the recording, so nothing "
            "from there on ran$",
        ),
        (
            CandidateRun(head=HEAD, fresh=(replace(RAN[0], halt=Halt("a", "budget")), RAN[1])),
            "^run 0 stopped at node 'a' as the time budget ran out, so nothing from there on ran$",
        ),
    ],
    ids=[
        "harness error",
        "crash",
        "crash, no node",
        "fewer runs",
        "more runs",
        "diverged",
        "budget",
    ],
)
def test_a_file_that_can_t_stand_in_for_fresh_runs_is_refused(run: CandidateRun, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        fresh_cassettes(run, RECORDED)
