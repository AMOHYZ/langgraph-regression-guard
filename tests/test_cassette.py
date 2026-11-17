import contextlib
import dataclasses
import json
import operator
import random
import socket
import time
from collections.abc import Callable, Hashable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, TypedDict, cast
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fixtures.graphs import (
    ASK,
    Ask,
    AskData,
    AskModel,
    S,
    _noop,
    ask,
    async_command_subgraph,
    conditional,
    fan_out_send,
    fan_out_to_subgraph,
    graph_inside_node,
    linear,
    subgraph_in_loop,
    subgraph_in_subgraph,
    with_subgraph,
    with_tools,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from lgcov.attribution import NEEDS_LIVE, Probe
from lgcov.cassette import (
    SCHEME,
    Cassette,
    ProbeStep,
    Recording,
    Step,
    _plain,
    _router_prints,
    _Units,
    changed_nodes,
    compared_run,
    graph_shape,
    judge_probes,
    judge_repeats,
    probe_nodes,
    probe_steps,
    record,
    recording_from_json,
    recording_to_json,
    repeat_steps,
    replay,
    replay_run,
    runs_from_json,
    runs_to_json,
    same_input,
    unservable,
)
from lgcov.cli import _import_file
from lgcov.fingerprint import fingerprint


def _b_changed(state: S) -> S:
    # the "PR" changes node b
    return {"x": 99}


def _linear_changed() -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", _b_changed)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def test_unchanged_graph_replays_with_zero_drift() -> None:
    cassette = record(linear(), {"x": 1})
    assert replay(linear(), cassette) == []


def test_changed_node_is_the_only_drift() -> None:
    cassette = record(linear(), {"x": 1})
    drifts = replay(_linear_changed(), cassette)
    assert [d.node for d in drifts] == ["b"]


def test_cassette_json_round_trip() -> None:
    cassette = record(linear(), {"x": 1})
    assert Cassette.from_json(cassette.to_json()) == cassette


def test_send_fan_out_is_stable() -> None:
    cassette = record(fan_out_send(), {"x": 0})
    assert replay(fan_out_send(), cassette) == []


def test_accept_nodes_copies_only_the_named_node() -> None:
    from lgcov.cassette import Step, accept_nodes

    base = Cassette({"x": 1}, [Step("a", {"x": 1}), Step("b", {"x": 2})])
    fresh = Cassette({"x": 1}, [Step("a", {"x": 9}), Step("b", {"x": 8})])
    accepted, changed = accept_nodes(base, fresh, {"b"})
    assert accepted == Cassette({"x": 1}, [Step("a", {"x": 1}), Step("b", {"x": 8})])
    assert changed == {"b"}


def test_accept_nodes_refuses_when_the_node_ran_a_different_number_of_times() -> None:
    import pytest

    from lgcov.cassette import Step, accept_nodes

    base = Cassette({}, [Step("w", 1), Step("w", 2)])
    with pytest.raises(ValueError, match="re-record"):
        accept_nodes(base, Cassette({}, [Step("w", 1)]), {"w"})


def test_accept_nodes_counts_only_an_output_change_as_changed() -> None:
    from lgcov.cassette import Step, accept_nodes

    base = Cassette({}, [Step("a", {"x": 1}, input={}, code="old")])
    refactored = Cassette({}, [Step("a", {"x": 1}, input={}, code="new")])
    assert accept_nodes(base, refactored, {"a"})[1] == set()


# T085: volatile fields never count as drift; real changes still do.
class V(TypedDict, total=False):
    text: str
    meta: dict[str, object]


def _volatile(text: str, meta: Callable[[], dict[str, object]]) -> CompiledStateGraph[V]:
    def node(state: V) -> V:
        return {"text": text, "meta": meta()}

    g = StateGraph(V)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


def _now() -> dict[str, object]:
    return {"at": datetime.now(UTC).isoformat()}


def _uuid() -> dict[str, object]:
    return {"trace": f"run-{uuid4()}"}


def test_timestamp_only_difference_is_no_drift() -> None:
    cassette = record(_volatile("hi", _now), {})
    time.sleep(0.001)  # make sure the clock moved
    assert replay(_volatile("hi", _now), cassette) == []


def test_uuid_only_difference_is_no_drift() -> None:
    cassette = record(_volatile("hi", _uuid), {})
    assert replay(_volatile("hi", _uuid), cassette) == []


def test_real_text_change_is_still_drift() -> None:
    cassette = record(_volatile("hi", _uuid), {})
    assert [d.node for d in replay(_volatile("bye", _uuid), cassette)] == ["n"]


def test_ignore_paths_mask_other_volatile_fields() -> None:
    def nonce() -> dict[str, object]:
        return {"nonce": random.random()}

    cassette = record(_volatile("hi", nonce), {})
    assert [d.node for d in replay(_volatile("hi", nonce), cassette)] == ["n"]
    assert replay(_volatile("hi", nonce), cassette, ignore_paths=["/meta/nonce"]) == []


def test_diff_cassettes_ignores_volatile_inputs() -> None:
    from lgcov.cassette import NodeIO, diff_cassettes

    base = {"n": NodeIO(input={"at": "2026-10-27T07:00:00Z"}, output={"text": "hi"})}
    new = {"n": NodeIO(input={"at": "2026-10-28T09:30:00Z"}, output={"text": "hi"})}
    assert diff_cassettes(base, new) == {}
    new["n"] = NodeIO(input={"at": "2026-10-28T09:30:00Z"}, output={"text": "bye"})
    assert diff_cassettes(base, new) == {"n": "root"}


# Serving: with serve=True a node whose code fingerprint and input match the cassette
# returns its recorded output without running.
def _spied(spy: Mock, prompt: str) -> CompiledStateGraph[S]:
    def a(state: S) -> S:
        return cast(S, spy(state))

    def b(state: S) -> S:
        return {"route": f"{prompt}:{state.get('x')}"}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def test_replay_serves_an_unchanged_node_without_running_it() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_spied(spy, "v1"), {"x": 1})
    assert spy.call_count == 1
    assert replay(_spied(spy, "v1"), cassette, serve=True) == []
    assert spy.call_count == 1  # a's code and input matched: served, not run
    assert replay(_spied(spy, "v1"), cassette) == []  # without serve, every node re-runs
    assert spy.call_count == 2


def test_replay_reruns_a_node_whose_code_changed_and_reports_drift() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_spied(spy, "v1"), {"x": 1})
    drifts = replay(_spied(spy, "v2"), cassette, serve=True)
    # b got its recorded input, but its code changed, so it ran and drifted.
    assert [(d.node, d.actual) for d in drifts] == [("b", ['{"route": "v2:2"}'])]
    assert spy.call_count == 1  # a is unchanged: served


def test_cassette_recorded_before_node_inputs_loads_and_replays_live() -> None:
    legacy = Cassette.from_json(
        '{"inputs": {"x": 1}, "steps": [{"node": "a", "output": {"x": 2}},'
        ' {"node": "b", "output": {"route": "v1:2"}}]}'
    )
    assert (legacy.steps[0].input, legacy.steps[0].code) == (None, None)
    spy = Mock(return_value={"x": 2})
    assert replay(_spied(spy, "v1"), legacy, serve=True) == []
    assert spy.call_count == 1  # no recorded input to match, so a ran live


@pytest.mark.parametrize("state", [AskModel, AskData])
def test_a_state_class_is_served_from_a_saved_cassette(state: type[Ask]) -> None:
    # Each node gets an instance of the class, keyed by its fields as a TypedDict state is by its
    # keys. Saved and loaded, as `lgcov ci` reads the baseline.
    saved = Cassette.from_json(record(ask(state), ASK).to_json())
    calls = replay_run(ask(state), saved, serve=True).calls
    assert [(c.node, c.how) for c in calls] == [("prep", "served"), ("cut", "served")]


def test_replay_serves_parallel_send_workers_by_their_payload() -> None:
    spy = Mock(side_effect=lambda state: {})

    def fan(state: S) -> list[Send]:
        return [Send("worker", {"x": i}) for i in range(3)]

    def worker(state: S) -> S:
        return cast(S, spy(state))

    g = StateGraph(S)
    g.add_node("plan", _noop)
    g.add_node("worker", worker)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan, ["worker"])
    g.add_edge("worker", END)
    cassette = record(g.compile(), {"x": 0})
    recorded = sorted(s.input["x"] for s in cassette.steps if s.node == "worker")
    assert recorded == [0, 1, 2]
    assert replay(g.compile(), cassette, serve=True) == []
    assert spy.call_count == 3  # the 3 recorded calls only: every payload was served


def test_record_streams_an_object_without_graph_nodes_as_before() -> None:
    from lgcov.cassette import Step

    class Streams:  # e.g. a remote graph: lgcov only needs .stream()
        def stream(self, inputs: dict[str, int], stream_mode: str) -> list[dict[str, S]]:
            return [{"a": {"x": inputs["x"] + 1}}]

    assert record(Streams(), {"x": 1}).steps == [Step("a", {"x": 2})]


def test_replay_steps_returns_the_fresh_run_in_order() -> None:
    # T083: coverage reuses the run replay already did (served steps included).
    from lgcov.cassette import replay_steps

    cassette = record(linear(), {"x": 1})
    drifts, steps = replay_steps(linear(), cassette, serve=True)
    assert drifts == []
    assert [s.node for s in steps] == ["a", "b"]


# T074: an exception from a node's own code names the node; network errors are flagged.
def _crashing(exc: Exception) -> CompiledStateGraph[S]:
    def boom(state: S) -> S:
        raise exc

    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", boom)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def test_a_node_that_raises_is_a_node_error_naming_the_node() -> None:
    from lgcov.cassette import NodeError

    cassette = record(linear(), {"x": 1})
    with pytest.raises(NodeError) as info:
        replay(_crashing(ValueError("bad state")), cassette)
    assert (info.value.node, info.value.transport) == ("b", False)
    assert isinstance(info.value.original, ValueError)


@pytest.mark.parametrize(
    "exc", [TimeoutError("slow"), ConnectionError("reset"), httpx.ConnectError("refused")]
)
def test_network_errors_in_a_node_are_flagged_as_transport(exc: Exception) -> None:
    from lgcov.cassette import NodeError

    cassette = record(linear(), {"x": 1})
    with pytest.raises(NodeError) as info:
        replay(_crashing(exc), cassette)
    assert (info.value.node, info.value.transport) == ("b", True)


def test_langgraph_control_flow_is_not_turned_into_a_node_error() -> None:
    from langgraph.errors import GraphBubbleUp

    from lgcov.cassette import NodeError

    cassette = record(linear(), {"x": 1})
    try:
        replay(_crashing(GraphBubbleUp()), cassette)
    except NodeError:
        pytest.fail("GraphBubbleUp (interrupt, Command) must pass through unwrapped")
    except GraphBubbleUp:
        pass


# T089: how each call ran, the divergence policy, and the time budget.
class _Clock:
    """Test wall-clock: it moves only when a node does its work."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


CLOCK = _Clock()


def _work() -> None:
    CLOCK.now += 50.0  # each call that runs takes 50 s


def _chain(a_out: int) -> CompiledStateGraph[S]:
    """a -> b -> c. A new a_out changes a's code (its closure) and so b's input; b resets
    x, so c's input matches the recording again."""

    def a(state: S) -> S:
        _work()
        return {"x": a_out}

    def b(state: S) -> S:
        _work()
        return {"x": 0, "route": "b"}

    def c(state: S) -> S:
        _work()
        return {"route": "c"}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("c", c)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g.compile()


def _recorded(a_out: int) -> Cassette:
    """`_chain(a_out)` recorded on a clock set back to 0 before and after, as a fresh process
    finds it. T250: a node's fingerprint counts what CLOCK holds, so the time a recording left
    on it would read as a code change."""
    CLOCK.now = 0.0
    cassette = record(_chain(a_out), {"x": 0})
    CLOCK.now = 0.0
    return cassette


def test_replay_run_tells_served_live_and_diverged_calls_apart() -> None:
    from lgcov.cassette import replay_run

    cassette = _recorded(1)
    r = replay_run(_chain(2), cassette, serve=True)  # on_divergence="flag" by default
    # a changed; b's code didn't, but its input did: diverged, so it ran live.
    assert [(c.node, c.how) for c in r.calls] == [
        ("a", "live"),
        ("b", "diverged"),
        ("c", "served"),
    ]
    assert (r.halt, r.not_checked) == (None, ())
    assert [d.node for d in r.drifts] == ["a"]


def test_on_divergence_fail_stops_the_run_and_leaves_the_rest_not_checked() -> None:
    from lgcov.cassette import replay_run

    cassette = _recorded(1)
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="fail")
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("b", "diverged")
    assert r.not_checked == ("b", "c")
    assert [(c.node, c.how) for c in r.calls] == [("a", "live")]
    assert [d.node for d in r.drifts] == ["a"]  # b and c weren't checked: not drift


def test_compared_run_says_what_replay_run_says_from_the_steps_alone() -> None:
    # T179: so a job that can't run the graph can do this with steps another job reports.
    from lgcov.cassette import ON_DIVERGENCE, compared_run, replay_run

    cassette = _recorded(1)
    for on_divergence in ON_DIVERGENCE:
        r = replay_run(_chain(2), cassette, serve=True, on_divergence=on_divergence)
        assert compared_run(cassette, r.steps, r.calls, r.halt) == r


def test_on_divergence_live_runs_the_rest_of_the_run_live() -> None:
    from lgcov.cassette import replay_run

    cassette = _recorded(1)
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="live")
    assert [(c.node, c.how) for c in r.calls] == [
        ("a", "live"),
        ("b", "diverged"),
        ("c", "after_divergence"),  # its input matched, but the run went live at b
    ]


def test_the_budget_stops_new_live_calls_once_the_projection_runs_over() -> None:
    from lgcov.cassette import Budget, replay_run

    cassette = _recorded(1)
    budget = Budget(90, CLOCK, expected_live=3)
    r = replay_run(_chain(2), cassette, serve=True, budget=budget)
    # a ran: nothing was measured before it. Then 50 s + 50 s x 2 to come > 90 s, so b
    # never started; a call that has started always finishes.
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("b", "budget")
    assert budget.hit == ("b", 50.0)
    assert [c.node for c in r.calls] == ["a"]
    assert r.not_checked == ("b", "c")


def test_served_calls_still_run_after_the_budget_is_hit() -> None:
    from lgcov.cassette import Budget, replay_run

    cassette = _recorded(1)
    budget = Budget(0, CLOCK)
    budget.hit = ("a", 0.0)
    r = replay_run(_chain(1), cassette, serve=True, budget=budget)
    assert r.halt is None and [c.how for c in r.calls] == ["served"] * 3


def test_unservable_counts_recorded_steps_whose_code_changed() -> None:
    from lgcov.cassette import unservable

    cassette = _recorded(1)
    assert unservable(_chain(1), [cassette]) == 0
    assert unservable(_chain(2), [cassette, cassette]) == 2  # a, in each run


def test_one_run_takes_each_node_s_fingerprint_once() -> None:
    # Within one lgcov run, a value a case moves, as the clock here, reaches no later case's
    # fingerprint: the run takes each node's once, before its first case. A second run in the
    # same process takes them again, so it sees what changed since. Outside a run every call
    # does, as before.
    from lgcov.cassette import changed_nodes, fingerprint_session, replay_run, unservable

    graph = _chain(1)
    CLOCK.now = 0.0
    with fingerprint_session():
        one, two = record(graph, {"x": 0}), record(graph, {"x": 0})
        assert [s.code for s in one.steps] == [s.code for s in two.steps]
        assert unservable(graph, [one, two]) == 0
        assert [c.how for c in replay_run(graph, two, serve=True).calls] == ["served"] * 3
    with fingerprint_session():
        assert changed_nodes(graph, [one]) == {"a", "b", "c"}  # the clock moved since
    three, four = record(graph, {"x": 0}), record(graph, {"x": 0})
    assert [s.code for s in three.steps] != [s.code for s in four.steps]


# T109: an error in a router or inside a subgraph names it, like an error in a node.
def _routed(router: Callable[[S], str]) -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", router, ["b"])
    g.add_edge("b", END)
    return g.compile()


def test_a_router_that_raises_is_a_node_error_naming_the_router() -> None:
    from lgcov.cassette import NodeError

    def pick(state: S) -> str:
        raise KeyError("route")

    with pytest.raises(NodeError) as info:
        replay(_routed(pick), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("pick", False)
    assert isinstance(info.value.original, KeyError)


def test_a_router_returning_an_unmapped_key_is_a_node_error_naming_the_router() -> None:
    from lgcov.cassette import NodeError

    def pick(state: S) -> str:
        return "c"  # not in its mapping ["b"]: LangGraph raises KeyError after pick returns

    with pytest.raises(NodeError) as info:
        replay(_routed(pick), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("pick", False)
    assert isinstance(info.value.original, KeyError)


def test_naming_a_router_never_reads_the_agents_own_objects() -> None:
    from lgcov.cassette import NodeError

    class Client:
        @property
        def path(self) -> str:
            raise RuntimeError("no base URL set")  # reading it would be a second crash

        def ask(self) -> str:
            raise ValueError("bad reply")

    def pick(state: S) -> str:
        return Client().ask()

    with pytest.raises(NodeError) as info:
        replay(_routed(pick), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.original.args) == ("pick", ("bad reply",))


def test_a_router_that_times_out_is_still_a_transport_error() -> None:
    from lgcov.cassette import NodeError

    def pick(state: S) -> str:
        raise TimeoutError("provider timed out")

    with pytest.raises(NodeError) as info:
        replay(_routed(pick), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("pick", True)


def test_a_node_inside_a_subgraph_that_raises_is_named_subgraph_dot_node() -> None:
    from lgcov.cassette import NodeError

    def inner(state: S) -> S:
        raise ValueError("bad draft")

    sub = StateGraph(S)
    sub.add_node("inner", inner)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("sub", sub.compile())
    g.add_edge(START, "a")
    g.add_edge("a", "sub")
    g.add_edge("sub", END)
    with pytest.raises(NodeError) as info:
        replay(g.compile(), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("sub.inner", False)


# T162: the probe runs head's node on base's recorded input, with the network blocked.
def _one_node(node: Callable[[S], S]) -> CompiledStateGraph[S]:
    def n(state: S) -> S:
        return node(state)

    builder = StateGraph(S)
    builder.add_node("n", n)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    return builder.compile()


def test_probe_runs_a_changed_plain_node_on_base_input_and_says_whether_it_drifts() -> None:
    base = record(_one_node(lambda s: {"x": s["x"] + 1}), {"x": 1})
    refactored = _one_node(lambda s: {"x": sum([s["x"], 1])})
    changed = _one_node(lambda s: {"x": s["x"] + 2})
    assert probe_nodes(refactored, [base], ["n"]) == {"n": Probe(False)}
    assert probe_nodes(changed, [base], ["n"]) == {"n": Probe(True)}


def test_probe_a_node_that_reaches_for_the_network_needs_a_live_probe() -> None:
    def calls_out(s: S) -> S:
        socket.create_connection(("example.invalid", 443), timeout=1)
        return s

    base = record(_one_node(lambda s: s), {"x": 1})
    assert probe_nodes(_one_node(calls_out), [base], ["n"]) == {"n": Probe(None, NEEDS_LIVE)}


def test_probe_steps_say_what_ran_and_judge_probes_says_what_it_means() -> None:
    # T162: the job that runs the PR's code reports raw output; the one that decides compares it.
    base = record(_one_node(lambda s: {"x": s["x"] + 1}), {"x": 1})
    [ran] = probe_steps(_one_node(lambda s: {"x": s["x"] + 2}), [base], ["n"])
    assert ran == ProbeStep(0, 0, "n", {"x": 3})
    assert judge_probes([base], [ran]) == {"n": Probe(True)}
    assert judge_probes([base], [ProbeStep(0, 0, "n", {"x": 2})]) == {"n": Probe(False)}


class _Fan(BaseModel):
    topics: list[str] = Field(default_factory=list)
    notes: Annotated[list[str], operator.add] = Field(default_factory=list)


def _fan(shout: bool) -> CompiledStateGraph[_Fan]:
    def plan(state: _Fan) -> dict[str, list[str]]:
        return {"topics": ["a", "b"]}

    def send(state: _Fan) -> list[Send]:
        return [Send("worker", {"topic": t}) for t in state.topics]

    def worker(state: object) -> dict[str, list[str]]:  # no class hint: no schema of its own
        assert isinstance(state, dict)  # the Send's payload as sent, not the state class
        topic = str(state["topic"])
        return {"notes": [topic.upper() if shout else topic]}

    g = StateGraph(_Fan)
    g.add_node("plan", plan)
    g.add_node("worker", worker)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", send, ["worker"])
    g.add_edge("worker", END)
    return g.compile()


def test_a_probe_hands_a_send_payload_over_as_langgraph_does() -> None:
    # In a pydantic-state graph LangGraph builds the class for a node it pulls from the state,
    # but passes a Send's payload as it was sent. The worker's node holds the state's mapper.
    saved = Cassette.from_json(record(_fan(shout=False), {}).to_json())
    head = _fan(shout=True)
    assert changed_nodes(head, [saved]) == {"worker"}
    assert probe_nodes(head, [saved], {"worker"}) == {"worker": Probe(True)}


def test_a_probe_that_reaches_for_the_network_is_recorded_as_raised_not_a_crash() -> None:
    def swallows(s: S) -> S:
        with contextlib.suppress(Exception):  # an SDK's retry loop must not catch the refusal
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        return s

    base = record(_one_node(lambda s: s), {"x": 1})
    [ran] = probe_steps(_one_node(swallows), [base], ["n"])
    assert ran == ProbeStep(0, 0, "n", raised=NEEDS_LIVE)
    assert judge_probes([base], [ran]) == {"n": Probe(None, NEEDS_LIVE)}


@pytest.mark.parametrize(
    "forged", [ProbeStep(0, 1, "n"), ProbeStep(-1, 0, "n"), ProbeStep(0, 0, "m")]
)
def test_a_probe_that_matches_no_recorded_step_is_refused(forged: ProbeStep) -> None:
    base = record(_one_node(lambda s: s), {"x": 1})
    with pytest.raises(ValueError, match="matches no recorded step"):
        judge_probes([base], [forged])


def test_judge_probes_leaves_a_probe_inside_a_step_or_of_a_router_to_a_later_lgcov() -> None:
    # T193, T221: candidate schema 3 can hold either. No label reads one yet, so neither decides.
    base = record(_one_node(lambda s: s), {"x": 1})
    inner, router = ProbeStep(0, 0, "inner", 1, inner=[0]), ProbeStep(0, 0, "n", routed=["b"])
    assert judge_probes([base], [inner, router]) == {}


# T175: a conditional edge's router is in no node's fingerprint, so its source node's steps keep
# one for it.
def _branching(threshold: int, ends: dict[Hashable, str] | None = None) -> CompiledStateGraph[S]:
    def route(state: S) -> str:
        return "b" if state["x"] > threshold else "c"

    g = StateGraph(S)
    for name in ("a", "b", "c"):
        g.add_node(name, lambda s: {"x": s["x"]})
    g.add_edge(START, "a")
    g.add_conditional_edges("a", route, ends)
    return g.compile()


def test_a_step_keeps_its_node_s_router_fingerprint_and_other_steps_keep_none() -> None:
    a, b = record(_branching(0), {"x": 1}).steps
    assert (a.node, b.node, b.router) == ("a", "b", None)
    assert a.router is not None and len(a.router) == 16
    assert record(_branching(0), {"x": 1}).steps[0].router == a.router


def test_a_router_edit_or_a_new_path_map_changes_the_router_fingerprint_only() -> None:
    base = record(_branching(0), {"x": 1}).steps[0]
    edited = record(_branching(5), {"x": 1}).steps[0]
    remapped = record(_branching(0, {"b": "c", "c": "b"}), {"x": 1}).steps[0]
    assert len({base.router, edited.router, remapped.router}) == 3
    assert base.code == edited.code == remapped.code


def test_a_cassette_with_no_router_saves_and_loads_as_it_did_before_routers_were_kept() -> None:
    old = record(linear(), {"x": 1})
    assert "router" not in old.to_json()
    assert Cassette.from_json(old.to_json()) == old
    routed = record(_branching(0), {"x": 1})
    assert Cassette.from_json(routed.to_json()) == routed


# T175: read before write (step 1 of 2), so the base's lgcov can read what a PR's lgcov records.
def test_a_step_s_router_fingerprint_survives_the_round_trip() -> None:
    routed = Cassette({"x": 1}, [Step("a", {"x": 1}, router="r1"), Step("b", {"x": 1})])
    assert routed.body()["steps"] == [
        {"node": "a", "output": {"x": 1}, "input": None, "code": None, "router": "r1"},
        {"node": "b", "output": {"x": 1}, "input": None, "code": None},
    ]
    assert Cassette.from_json(routed.to_json()) == routed


def test_a_cassette_with_no_router_saves_as_it_did_before_routers_were_kept() -> None:
    assert "router" not in record(linear(), {"x": 1}).to_json()


# T193, PR 1: read recording schema 2 before anything writes it, as T175 did above. The fixture
# holds every field docs/design/cassette-schema.md defines (section 2), with part `prompt` missing
# its `source` (ruling 4 is open) and tool call `search` holding a status T248 hasn't settled.
# Each is read and kept, and nothing acts on one.
_HERE = Path(__file__).parent
_SCHEMA_2_FILE = _HERE / "fixtures" / "schema2_recording.json"
_SCHEMA_2 = json.loads(_SCHEMA_2_FILE.read_text(encoding="utf-8"))
SCHEMA_2 = json.dumps(_SCHEMA_2, indent=2, sort_keys=True) + "\n"  # as lgcov saves a file


def test_a_schema_2_recording_with_every_field_loads_and_saves_as_it_was() -> None:
    recording = recording_from_json(SCHEMA_2)
    assert (recording.schema, recording.scheme) == (2, SCHEME)
    assert (recording.graph, recording.parts) == (_SCHEMA_2["graph"], _SCHEMA_2["parts"])
    plan, tools = recording.runs[0].steps
    assert (plan.kind, plan.routed, plan.router_state) == ("node", ["tools"], {"x": 2})
    assert plan.parts == ["plan", "prompt"]
    assert plan.samples == [{"output": {"x": 2}, "goto": ["tools"]}, {"output": {"x": 3}}]
    assert tools.units is not None
    search, agent = tools.units
    assert (search.kind, search.call_id, search.status) == ("tool", "call_1", "rate_limited")
    assert (agent.kind, agent.units) == ("agent", [Step("inner", {"x": 4}, {"x": 2}, "1a")])
    assert recording_to_json(recording) == SCHEMA_2
    assert runs_from_json(SCHEMA_2) == recording.runs  # what every command reads


@pytest.mark.parametrize(("schema", "named"), [(3, "3"), ("2", "'2'")])
def test_a_recording_schema_this_lgcov_does_not_read_is_refused_by_its_number(
    schema: object, named: str
) -> None:
    step = {"node": "a", "output": 1, "a_later_field": 1}  # the number is read first
    text = json.dumps({"schema": schema, "runs": [{"inputs": {}, "steps": [step]}]})
    with pytest.raises(ValueError, match=f"^schema {named}; this lgcov reads 1 and 2$"):
        runs_from_json(text)


@pytest.mark.parametrize("step", [1, {"node": "a", "output": 1, "units": [1]}])
def test_a_step_that_is_not_an_object_is_still_a_type_error(step: object) -> None:
    # The job that decides reads a TypeError as "cannot read baseline", not as a crash.
    with pytest.raises(TypeError):
        runs_from_json(json.dumps({"runs": [{"inputs": {}, "steps": [step]}]}))


TODAYS_RECORDINGS = {
    "demo_fixture": _HERE / "fixtures" / "demo_baseline.json",
    "selfcheck": _HERE / "fixtures" / "selfcheck" / "cassettes" / "baseline.json",
    "demo_agent": _HERE.parent / "examples" / "demo_agent" / "cassettes" / "baseline.json",
}


@pytest.mark.parametrize("path", TODAYS_RECORDINGS.values(), ids=TODAYS_RECORDINGS.keys())
def test_today_s_recordings_read_as_schema_1_and_save_byte_for_byte(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    recording = recording_from_json(text)
    assert recording == Recording(runs_from_json(text))  # schema 1, with no recording fields
    assert recording_to_json(recording) == runs_to_json(recording.runs) == text


# T193, PR 2: `lgcov record --schema 2` writes the graph's shape as `graph`: each node by name, a
# subgraph's own nodes nested, and a tool node's tools.
def test_graph_shape_nests_a_subgraph_and_names_a_tool_node_s_tools() -> None:
    inner: dict[str, object] = {"nodes": {"a": {}, "b": {}}}
    assert graph_shape(with_subgraph()) == {"nodes": {"prep": {}, "inner": inner}}
    assert graph_shape(with_tools()) == {"nodes": {"agent": {}, "tools": {"tools": ["lookup"]}}}
    assert graph_shape(object()) is None  # an object with only .stream() has no nodes to read


# T193, 3a-1: with `units`, `record` puts the nodes that ran inside a subgraph under its step:
# each with its input, output and fingerprint, in the order LangGraph started them.
def _units(step: Step) -> list[Step]:
    assert step.units is not None, step
    return step.units


def test_record_with_units_puts_a_subgraph_s_nodes_under_its_step() -> None:
    graph = with_subgraph()
    twin, cassette = record(graph, {"x": 1}), record(graph, {"x": 1}, units=True)
    assert [dataclasses.replace(s, units=None) for s in cassette.steps] == twin.steps
    prep, inner = cassette.steps
    assert prep.units is None
    a, b = _units(inner)
    assert [(u.node, u.input, u.output) for u in (a, b)] == [
        ("a", {"x": 1}, {"x": 1}),
        ("b", {"x": 1}, {"x": 1}),
    ]
    assert a.code is not None and a.code == b.code  # both run _noop
    text = recording_to_json(Recording([cassette], 2, SCHEME, graph_shape(graph)))
    assert recording_from_json(text).runs == [cassette]


def test_each_visit_to_a_subgraph_keeps_its_own_units() -> None:
    first, second = record(subgraph_in_loop(), {"x": 0}, units=True).steps
    assert [u.input for u in _units(first)] == [{"x": 0}]
    assert [u.input for u in _units(second)] == [{"x": 1}]


def test_a_fan_out_to_a_subgraph_files_each_run_s_units_under_its_own_step() -> None:
    _, *inner = record(fan_out_to_subgraph(), {"items": []}, units=True).steps
    assert len(inner) == 3
    for step in inner:
        (unit,) = _units(step)
        assert step.output == {"items": [unit.input["x"] * 10]}  # this run's own unit


def test_a_unit_gets_the_task_whose_result_holds_its_update() -> None:
    # Parallel tasks' results can arrive out of order: the one holding a unit's update names it.
    units = _Units(with_subgraph())
    units.file((), Step("inner", {"x": 1}))
    units.file((), Step("inner", {"x": 2}))
    units.task((), {"id": "t2", "name": "inner", "result": {"x": 2}})
    units.task((), {"id": "t1", "name": "inner", "result": {"x": 1}})
    assert [u.task for u in units.filed[()]] == ["t1", "t2"]


def test_units_nest_a_subgraph_inside_a_subgraph() -> None:
    (outer,) = record(subgraph_in_subgraph(), {"x": 1}, units=True).steps
    prep, inner = _units(outer)
    assert [u.node for u in _units(inner)] == ["a", "b"]


def test_an_async_subgraph_s_unit_keeps_where_its_command_went() -> None:
    (inner,) = record(async_command_subgraph(), {"x": 1}, units=True).steps
    jump, b = _units(inner)
    assert (jump.goto, jump.output, b.node) == (["b"], {"route": "jumped"}, "b")


def test_a_graph_started_inside_a_node_gets_no_units_yet() -> None:
    (calls,) = record(graph_inside_node(), {"x": 1}, units=True).steps
    assert calls.units is None  # T193's section 6, PR 7


def test_a_recording_with_units_replays_as_its_twin() -> None:
    graph = with_subgraph()
    replays = [
        replay_run(graph, record(graph, {"x": 1}, units=u), serve=True) for u in (False, True)
    ]
    calls = [[(c.node, c.how) for c in r.calls] for r in replays]
    assert calls[0] == calls[1] and replays[0].drifts == replays[1].drifts


# T211: repeats run a node live, K times on each input base recorded; `judge_repeats` compares
# base's samples (the recording and base's repeats) with head's.
BASE_TEXT = "the order ships on friday"
HEAD_TEXT = "refunds take ten working days sorry"


def _says(text: str) -> CompiledStateGraph[S]:
    words = iter(("alpha", "beta", "gamma") * 9)  # a model: another word each call
    return _one_node(lambda s: {"route": f"{text} {next(words)}"})


def test_repeat_steps_runs_a_node_k_times_on_each_recorded_input_with_the_network_open() -> None:
    def looks_up(s: S) -> S:
        socket.getaddrinfo("localhost", 80)  # the offline probe would refuse this
        return {"x": s["x"] + 1}

    base = record(_one_node(looks_up), {"x": 1})
    ran = repeat_steps(_one_node(looks_up), [base], ["n"], 2)
    assert ran == (ProbeStep(0, 0, "n", {"x": 2}), ProbeStep(0, 0, "n", {"x": 2}))


def test_judge_repeats_names_no_drift_when_head_s_samples_sit_inside_base_s_spread() -> None:
    graph = _says(BASE_TEXT)
    base = record(graph, {"x": 1})
    again = repeat_steps(graph, [base], ["n"], 3)
    [check] = judge_repeats(
        [base], again, repeat_steps(_says(BASE_TEXT), [base], ["n"], 3)
    ).values()
    assert (check.k, check.min_head_to_base, check.drifted) == (3, 0.0, False)
    assert check.spread > 0
    assert check.verdict == "no drift detected above noise (K=3)"


def test_judge_repeats_names_drift_when_head_s_samples_sit_outside_it() -> None:
    graph = _says(BASE_TEXT)
    base = record(graph, {"x": 1})
    again = repeat_steps(graph, [base], ["n"], 3)
    found = judge_repeats([base], again, repeat_steps(_says(HEAD_TEXT), [base], ["n"], 3))
    assert found["n"].drifted and found["n"].verdict == "drift above noise (K=3)"


def test_judge_repeats_leaves_out_a_node_whose_repeat_raised() -> None:
    def breaks(s: S) -> S:
        raise RuntimeError("no key")

    base = record(_one_node(lambda s: s), {"x": 1})
    ran = repeat_steps(_one_node(breaks), [base], ["n"], 1)
    assert ran == (ProbeStep(0, 0, "n", raised="raised RuntimeError on base's input"),)
    assert judge_repeats([base], ran, ran) == {}


# T212: a recording made before fingerprints ignored comments holds the old scheme's values. A
# node whose old-scheme fingerprint still matches is byte-identical, so it is unchanged.
def _recorded_before_t212(graph: CompiledStateGraph[S], run: Cassette) -> Cassette:
    routers = _router_prints(graph, raw=True)
    steps = [
        dataclasses.replace(
            s,
            code=fingerprint(_plain(graph.nodes[s.node].bound), raw=True),
            router=routers.get(s.node),
        )
        for s in run.steps
    ]
    return Cassette(run.inputs, steps)


def test_a_new_recording_holds_the_new_scheme_s_fingerprints() -> None:
    run = record(linear(), {"x": 1})
    assert {s.code for s in run.steps} == {fingerprint(_noop)} != {fingerprint(_noop, raw=True)}


def test_a_recording_from_before_t212_still_serves_its_unchanged_nodes() -> None:
    graph = conditional()
    old = _recorded_before_t212(graph, record(graph, {"x": 1, "route": "tools"}))
    assert old != record(graph, {"x": 1, "route": "tools"})  # the two schemes do differ
    assert unservable(graph, [old]) == 0 and changed_nodes(graph, [old]) == set()
    done = replay_run(graph, old, (), serve=True)
    assert {c.how for c in done.calls} == {"served"} and not done.drifts
    # What ran is written in the recording's scheme, so later comparisons are plain equality.
    assert [(s.code, s.router) for s in done.steps] == [(s.code, s.router) for s in old.steps]


def test_a_recording_from_before_t212_still_sees_an_edit() -> None:
    old = _recorded_before_t212(linear(), record(linear(), {"x": 1}))
    assert changed_nodes(_linear_changed(), [old]) == {"b"}
    done = replay_run(_linear_changed(), old, (), serve=True)
    assert [c.how for c in done.calls] == ["served", "live"]
    assert done.steps[1].code == fingerprint(_b_changed)  # a changed node: the new scheme


def test_a_recording_made_to_compare_with_an_older_one_uses_its_fingerprints() -> None:
    # As the synthetic suite does: the PR side is recorded afresh and compared with a baseline.
    old = _recorded_before_t212(linear(), record(linear(), {"x": 1}))
    assert record(linear(), {"x": 1}, like=[old]) == old
    edited = record(_linear_changed(), {"x": 1}, like=[old])
    assert [s.code for s in edited.steps] == [old.steps[0].code, fingerprint(_b_changed)]


def test_a_node_only_another_run_recorded_gets_that_recording_s_scheme() -> None:
    # The PR's run reaches a node this run's recording never did: it must not read as changed.
    graph = conditional()
    tools, answer = (
        _recorded_before_t212(graph, record(graph, {"x": 1, "route": r})) for r in ("tools", "")
    )
    codes = {s.node: s.code for s in (*tools.steps, *answer.steps)}
    rerouted = Cassette({"x": 1, "route": "tools"}, answer.steps)  # as if the route had flipped
    for serve in (True, False):
        done = replay_run(graph, rerouted, (), serve=serve, baseline=[tools, answer])
        assert {s.node: s.code for s in done.steps}.items() <= codes.items()


# T212: a node function's own docstring doesn't count, unless a ToolNode exposes it as a tool.
_DOC_GRAPH = '''\
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode


class S(TypedDict, total=False):
    x: int


def plain(state: S) -> S:
    """Add one to x."""
    return {"x": state["x"] + 1}


def shared(state: S) -> S:
    """Add two to x."""
    return {"x": state["x"] + 2}


g = StateGraph(S)
g.add_node("plain", plain)
g.add_node("shared", shared)
g.add_node("tools", ToolNode([shared]))
g.add_edge(START, "plain")
g.add_edge("plain", "shared")
g.add_edge("shared", END)
g.add_edge("tools", END)  # never reached: the test is who holds `shared`
graph = g.compile()
'''


def _doc_graph(path: Path, source: str) -> CompiledStateGraph[S]:
    path.write_text(source, encoding="utf-8")
    graph: CompiledStateGraph[S] = _import_file(str(path)).graph
    return graph


def test_a_reformat_and_a_node_docstring_edit_leave_every_step_served(tmp_path: Path) -> None:
    base = record(_doc_graph(tmp_path / "base.py", _DOC_GRAPH), {"x": 1})
    edited = _DOC_GRAPH.replace("Add one to x.", "Increment x.\n\n    Twice as long.")
    edited = edited.replace(
        '{"x": state["x"] + 1}', "{\n        'x': (state['x'] + 1),  # one\n    }"
    )
    assert edited.count("Add") == 1 and "# one" in edited
    head = _doc_graph(tmp_path / "head.py", edited)
    assert changed_nodes(head, [base]) == set() and unservable(head, [base]) == 0
    done = replay_run(head, base, (), serve=True)
    assert [c.how for c in done.calls] == ["served", "served"] and not done.drifts


def test_a_docstring_edit_counts_for_a_node_function_a_toolnode_exposes(tmp_path: Path) -> None:
    # ToolNode builds the tool's description from the docstring: a model can see it.
    base = record(_doc_graph(tmp_path / "base.py", _DOC_GRAPH), {"x": 1})
    head = _doc_graph(tmp_path / "head.py", _DOC_GRAPH.replace("Add two to x.", "Double x."))
    assert changed_nodes(head, [base]) == {"shared"}


# T230: which nodes got the same input on both sides, so drift there isn't propagated.
class _Seen(TypedDict, total=False):
    x: int
    seen: Annotated[list[int], operator.add]


def _branches(b_adds: int, c_adds: int) -> CompiledStateGraph[_Seen]:
    def a(state: _Seen) -> _Seen:
        return {"x": state["x"]}

    def b(state: _Seen) -> _Seen:
        return {"seen": [state["x"] + b_adds]}

    def c(state: _Seen) -> _Seen:
        return {"seen": [state["x"] * 10 + c_adds]}

    def d(state: _Seen) -> _Seen:
        return {"x": sum(state["seen"])}

    g = StateGraph(_Seen)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("c", c)
    g.add_node("d", d)
    g.add_edge(START, "a")
    for branch in ("b", "c"):
        g.add_edge("a", branch)
        g.add_edge(branch, "d")
    g.add_edge("d", END)
    return g.compile()


def test_same_input_names_the_nodes_both_sides_gave_the_same_input() -> None:
    base, head = record(_branches(0, 0), {"x": 1}), record(_branches(1, 1), {"x": 1})
    assert {d.node for d in compared_run(base, head.steps, (), None).drifts} == {"b", "c", "d"}
    assert same_input(base, head.steps) == {"a", "b", "c"}  # d's differs: it saw b's and c's
    assert same_input(base, base.steps) == {"a", "b", "c", "d"}
    blank = [dataclasses.replace(s, input=None) for s in head.steps]
    assert same_input(base, blank) == set()  # an old recording: nothing is known
