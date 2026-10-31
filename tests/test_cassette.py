import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypedDict, cast
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fixtures.graphs import S, _noop, fan_out_send, linear
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from lgcov.cassette import Cassette, record, replay


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
