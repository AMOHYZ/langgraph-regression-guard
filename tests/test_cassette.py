import contextlib
import dataclasses
import random
import socket
import time
from collections.abc import Callable, Hashable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fixtures.graphs import S, _noop, conditional, fan_out_send, linear
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from lgcov.attribution import NEEDS_LIVE, Probe
from lgcov.cassette import (
    Cassette,
    ProbeStep,
    Step,
    _plain,
    _router_prints,
    changed_nodes,
    judge_probes,
    judge_repeats,
    probe_nodes,
    probe_steps,
    record,
    repeat_steps,
    replay,
    replay_run,
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


def test_replay_run_tells_served_live_and_diverged_calls_apart() -> None:
    from lgcov.cassette import replay_run

    cassette = record(_chain(1), {"x": 0})
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

    cassette = record(_chain(1), {"x": 0})
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="fail")
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("b", "diverged")
    assert r.not_checked == ("b", "c")
    assert [(c.node, c.how) for c in r.calls] == [("a", "live")]
    assert [d.node for d in r.drifts] == ["a"]  # b and c weren't checked: not drift


def test_compared_run_says_what_replay_run_says_from_the_steps_alone() -> None:
    # T179: so a job that can't run the graph can do this with steps another job reports.
    from lgcov.cassette import ON_DIVERGENCE, compared_run, replay_run

    cassette = record(_chain(1), {"x": 0})
    for on_divergence in ON_DIVERGENCE:
        r = replay_run(_chain(2), cassette, serve=True, on_divergence=on_divergence)
        assert compared_run(cassette, r.steps, r.calls, r.halt) == r


def test_on_divergence_live_runs_the_rest_of_the_run_live() -> None:
    from lgcov.cassette import replay_run

    cassette = record(_chain(1), {"x": 0})
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="live")
    assert [(c.node, c.how) for c in r.calls] == [
        ("a", "live"),
        ("b", "diverged"),
        ("c", "after_divergence"),  # its input matched, but the run went live at b
    ]


def test_the_budget_stops_new_live_calls_once_the_projection_runs_over() -> None:
    from lgcov.cassette import Budget, replay_run

    cassette = record(_chain(1), {"x": 0})
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

    cassette = record(_chain(1), {"x": 0})
    budget = Budget(0, CLOCK)
    budget.hit = ("a", 0.0)
    r = replay_run(_chain(1), cassette, serve=True, budget=budget)
    assert r.halt is None and [c.how for c in r.calls] == ["served"] * 3


def test_unservable_counts_recorded_steps_whose_code_changed() -> None:
    from lgcov.cassette import unservable

    cassette = record(_chain(1), {"x": 0})
    assert unservable(_chain(1), [cassette]) == 0
    assert unservable(_chain(2), [cassette, cassette]) == 2  # a, in each run


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
