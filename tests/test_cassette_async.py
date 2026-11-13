import asyncio
import operator
import os
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from functools import partial
from typing import Annotated, Any, Literal, TypedDict, cast
from unittest.mock import Mock

import pytest
from fixtures.graphs import S, _noop, linear
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from lgcov.cassette import (
    Budget,
    Cassette,
    NodeError,
    RunningLoopError,
    Step,
    record,
    replay,
    replay_run,
    unservable,
)


def _toy(spy: Mock, prompt: str = "v1") -> CompiledStateGraph[S]:
    async def a(state: S) -> S:
        return cast(S, spy(state))

    async def b(state: S) -> S:
        return {"route": f"{prompt}:{state.get('x')}"}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def test_async_graph_records_then_replays_the_same_node_outputs() -> None:
    cassette = record(_toy(Mock(return_value={"x": 2})), {"x": 1})
    outputs = [("a", {"x": 2}), ("b", {"route": "v1:2"})]
    assert [(s.node, s.output) for s in cassette.steps] == outputs
    assert [s.input for s in cassette.steps] == [{"x": 1}, {"x": 2}]
    fresh = replay_run(_toy(Mock(return_value={"x": 2})), Cassette.from_json(cassette.to_json()))
    assert fresh.drifts == ()
    assert [(s.node, s.output) for s in fresh.steps] == outputs


def test_replay_serves_an_async_node_without_running_it() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_toy(spy), {"x": 1})
    assert spy.call_count == 1
    served = replay_run(_toy(spy), cassette, serve=True)
    assert served.drifts == ()
    assert [(c.node, c.how) for c in served.calls] == [("a", "served"), ("b", "served")]
    assert spy.call_count == 1  # the recorded output came back with no real call
    assert replay(_toy(spy), cassette) == []  # without serve, every node re-runs
    assert spy.call_count == 2


def test_replay_reruns_an_async_node_whose_code_changed_and_reports_drift() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_toy(spy, "v1"), {"x": 1})
    drifts = replay(_toy(spy, "v2"), cassette, serve=True)
    assert [(d.node, d.actual) for d in drifts] == [("b", ['{"route": "v2:2"}'])]
    assert spy.call_count == 1  # a is unchanged: served


# Under astream a sync node runs through LangGraph's `afunc`, which calls the original
# function past any wrapper on `func`: it would run without being recorded or served.
def _mixed(first_spy: Mock, second_spy: Mock, first_out: int = 2) -> CompiledStateGraph[S]:
    def first(state: S) -> S:
        first_spy(state)
        return {"x": first_out}

    async def second(state: S) -> S:
        second_spy(state)
        return {"route": f"r{state.get('x')}"}

    g = StateGraph(S)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    return g.compile()


def test_mixed_graph_records_both_nodes_and_serves_both_without_running_them() -> None:
    first, second = Mock(), Mock()
    cassette = record(_mixed(first, second), {"x": 1})
    assert [(s.node, s.input, s.output) for s in cassette.steps] == [
        ("first", {"x": 1}, {"x": 2}),
        ("second", {"x": 2}, {"route": "r2"}),
    ]
    assert all(s.code is not None for s in cassette.steps)
    served = replay_run(_mixed(first, second), cassette, serve=True)
    assert served.drifts == ()
    assert [(c.node, c.how) for c in served.calls] == [("first", "served"), ("second", "served")]
    assert (first.call_count, second.call_count) == (1, 1)  # the recording's calls only


def test_mixed_graph_reruns_a_changed_sync_node_and_what_it_feeds() -> None:
    first, second = Mock(), Mock()
    cassette = record(_mixed(first, second), {"x": 1})
    changed = replay_run(_mixed(first, second, first_out=3), cassette, serve=True)
    assert [(c.node, c.how) for c in changed.calls] == [("first", "live"), ("second", "diverged")]
    assert [d.node for d in changed.drifts] == ["first", "second"]
    assert (first.call_count, second.call_count) == (2, 2)


# Edge cases: what the sync path does for nodes, routers and subgraphs, under `.astream`.
# Any: a node here is a sync or async function, a RunnableLambda or a compiled subgraph.
def _pair(a: Any, b: Any) -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


async def _anoop(state: S) -> S:
    return state


def _boom(state: S) -> S:
    raise ValueError("bad state")


async def _aboom(state: S) -> S:
    raise ValueError("bad state")


def _takes_config(spy: Mock) -> CompiledStateGraph[S]:
    async def a(state: S, config: RunnableConfig) -> S:  # open_deep_research's node shape
        return cast(S, spy(state, config))

    return _pair(a, _anoop)


def test_an_async_node_gets_its_config_and_is_served_on_replay() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_takes_config(spy), {"x": 1})
    assert "configurable" in spy.call_args.args[1]
    assert replay(_takes_config(spy), cassette, serve=True) == []
    assert spy.call_count == 1


def test_an_async_runnable_lambda_gets_its_config_and_is_served_on_replay() -> None:
    spy = Mock(return_value={"x": 2})

    async def a(state: S, config: RunnableConfig) -> S:
        return cast(S, spy(state, config))

    cassette = record(_pair(RunnableLambda(a), _anoop), {"x": 1})
    assert "configurable" in spy.call_args.args[1]
    assert replay(_pair(RunnableLambda(a), _anoop), cassette, serve=True) == []
    assert spy.call_count == 1


@pytest.mark.parametrize("boom", [_boom, _aboom])
def test_a_node_that_raises_under_astream_is_a_node_error_naming_it(boom: Any) -> None:
    with pytest.raises(NodeError) as info:
        replay(_pair(_anoop, boom), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("b", False)
    assert isinstance(info.value.original, ValueError)


def test_a_timeout_in_an_async_node_is_flagged_as_transport() -> None:
    async def slow(state: S) -> S:
        raise TimeoutError("provider timed out")

    with pytest.raises(NodeError) as info:
        replay(_pair(slow, _anoop), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("a", True)


def test_langgraph_control_flow_from_an_async_node_is_not_a_node_error() -> None:
    async def a(state: S) -> S:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        replay(_pair(a, _anoop), Cassette({"x": 1}, []))


def _with_subgraph(first: Any, inner: Any) -> CompiledStateGraph[S]:
    sub = StateGraph(S)
    sub.add_node("inner", inner)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)
    g = StateGraph(S)
    g.add_node("a", first)
    g.add_node("sub", sub.compile())
    g.add_edge(START, "a")
    g.add_edge("a", "sub")
    g.add_edge("sub", END)
    return g.compile()


def test_a_sync_graph_with_an_async_subgraph_records_and_replays() -> None:
    cassette = record(_with_subgraph(_noop, _anoop), {"x": 1})
    assert [s.node for s in cassette.steps] == ["a", "sub"]
    assert replay(_with_subgraph(_noop, _anoop), cassette) == []


@pytest.mark.parametrize(("first", "inner"), [(_noop, _aboom), (_anoop, _boom), (_anoop, _aboom)])
def test_a_node_in_a_subgraph_that_raises_under_astream_is_named(first: Any, inner: Any) -> None:
    with pytest.raises(NodeError) as info:
        replay(_with_subgraph(first, inner), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == ("sub.inner", False)


def test_an_async_node_two_subgraphs_down_is_found_and_named() -> None:
    nested = _with_subgraph(_noop, _with_subgraph(_noop, _aboom))
    with pytest.raises(NodeError) as info:
        replay(nested, Cassette({"x": 1}, []))
    assert info.value.node == "sub.inner.sub.inner"


def _routed(node: Any, router: Any) -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", node)
    g.add_node("b", node)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", router, ["b"])
    g.add_edge("b", END)
    return g.compile()


async def _to_b(state: S) -> str:
    return "b"


def _raises(state: S) -> str:
    raise KeyError("route")


async def _araises(state: S) -> str:
    raise KeyError("route")


def _unmapped(state: S) -> str:
    return "c"  # not in its mapping ["b"]: LangGraph raises KeyError after it returns


async def _aunmapped(state: S) -> str:
    return "c"


def test_a_graph_whose_only_async_part_is_its_router_records_and_replays() -> None:
    cassette = record(_routed(_noop, _to_b), {"x": 1})
    assert [s.node for s in cassette.steps] == ["a", "b"]
    assert replay(_routed(_noop, _to_b), cassette) == []


@pytest.mark.parametrize(
    ("node", "router"),
    [
        (_anoop, _raises),
        (_noop, _araises),
        (_anoop, _araises),
        (_anoop, _unmapped),
        (_noop, _aunmapped),
        (_anoop, _aunmapped),
    ],
)
def test_a_router_that_fails_under_astream_is_a_node_error_naming_it(
    node: Any, router: Any
) -> None:
    with pytest.raises(NodeError) as info:
        replay(_routed(node, router), Cassette({"x": 1}, []))
    assert (info.value.node, info.value.transport) == (router.__name__, False)
    assert isinstance(info.value.original, KeyError)


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
    """a (async) -> b (sync) -> c (async). A new a_out changes a's code (its closure) and
    so b's input; b resets x, so c's input matches the recording again."""

    async def a(state: S) -> S:
        _work()
        return {"x": a_out}

    def b(state: S) -> S:
        _work()
        return {"x": 0, "route": "b"}

    async def c(state: S) -> S:
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


def test_on_divergence_fail_stops_an_async_run_and_leaves_the_rest_not_checked() -> None:
    cassette = record(_chain(1), {"x": 0})
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="fail")
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("b", "diverged")
    assert r.not_checked == ("b", "c")
    assert [(c.node, c.how) for c in r.calls] == [("a", "live")]
    assert [d.node for d in r.drifts] == ["a"]  # b and c weren't checked: not drift


def test_on_divergence_live_runs_the_rest_of_an_async_run_live() -> None:
    cassette = record(_chain(1), {"x": 0})
    r = replay_run(_chain(2), cassette, serve=True, on_divergence="live")
    assert [(c.node, c.how) for c in r.calls] == [
        ("a", "live"),
        ("b", "diverged"),
        ("c", "after_divergence"),  # its input matched, but the run went live at b
    ]


def test_the_budget_stops_new_live_calls_in_an_async_run() -> None:
    cassette = record(_chain(1), {"x": 0})
    budget = Budget(90, CLOCK, expected_live=3)
    r = replay_run(_chain(2), cassette, serve=True, budget=budget)
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("b", "budget")
    assert budget.hit == ("b", 50.0)
    assert [c.node for c in r.calls] == ["a"]
    assert r.not_checked == ("b", "c")


def test_unservable_counts_async_nodes_whose_code_changed() -> None:
    spy = Mock(return_value={"x": 2})
    cassette = record(_toy(spy, "v1"), {"x": 1})
    assert unservable(_toy(spy, "v1"), [cassette]) == 0
    assert unservable(_toy(spy, "v2"), [cassette]) == 1  # b, whose prompt changed


class F(TypedDict, total=False):
    x: int
    out: Annotated[list[int], operator.add]


def _fan_out(spy: Mock) -> CompiledStateGraph[F]:
    def fan(state: F) -> list[Send]:
        return [Send("worker", {"x": i}) for i in range(3)]

    async def worker(state: F) -> F:
        await asyncio.sleep(0.01 * (3 - state["x"]))  # the last one sent finishes first
        spy(state)
        return {"out": [state["x"] * 10]}

    g = StateGraph(F)
    g.add_node("worker", worker)
    g.add_conditional_edges(START, fan, ["worker"])
    g.add_edge("worker", END)
    return g.compile()


def test_parallel_async_workers_are_recorded_and_served_by_their_payload() -> None:
    spy = Mock()
    cassette = record(_fan_out(spy), {"x": 0})
    assert sorted((s.input["x"], s.output["out"]) for s in cassette.steps) == [
        (0, [0]),
        (1, [10]),
        (2, [20]),
    ]
    r = replay_run(_fan_out(spy), cassette, serve=True)
    assert r.drifts == ()
    assert [c.how for c in r.calls] == ["served"] * 3
    assert spy.call_count == 3  # the recording's calls only


def test_record_streams_an_object_with_only_astream() -> None:
    class Streams:  # e.g. an async-only remote graph: lgcov only needs .astream()
        async def astream(
            self, inputs: dict[str, int], stream_mode: str
        ) -> AsyncIterator[dict[str, S]]:
            yield {"a": {"x": inputs["x"] + 1}}

    assert record(Streams(), {"x": 1}).steps == [Step("a", {"x": 2})]


def test_a_sync_graph_never_starts_an_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_loop() -> None:
        pytest.fail("a sync graph must stay on .stream")

    monkeypatch.setattr("lgcov.cassette._thread_loop", no_loop)
    assert [s.node for s in record(linear(), {"x": 1}).steps] == ["a", "b"]


# RunnableLambda(sync, afunc=async): `func`'s fingerprint says nothing about the async body.
def _two_bodies(
    sync_spy: Mock, async_spy: Mock, second: Any, out: int = 2
) -> CompiledStateGraph[S]:
    def body(state: S) -> S:
        sync_spy(state)
        return {"x": 2}

    async def abody(state: S) -> S:
        async_spy(state)
        return {"x": out}

    return _pair(RunnableLambda(body, afunc=abody), second)


def test_a_second_async_body_runs_live_and_is_never_served() -> None:
    sync_spy, async_spy = Mock(), Mock()
    cassette = record(_two_bodies(sync_spy, async_spy, _anoop), {"x": 1})
    assert cassette.steps[0].code is None  # nothing vouches for the async body
    # A PR edits only the async body: `body`, and so its fingerprint, are unchanged.
    drifts = replay(_two_bodies(sync_spy, async_spy, _anoop, out=3), cassette, serve=True)
    assert [d.node for d in drifts] == ["a", "b"]
    assert (sync_spy.call_count, async_spy.call_count) == (0, 2)


def test_a_sync_recording_is_not_served_for_a_second_async_body_under_astream() -> None:
    sync_spy, async_spy = Mock(), Mock()
    cassette = record(_two_bodies(sync_spy, async_spy, _noop), {"x": 1})
    assert cassette.steps[0].code is not None  # `.stream` ran `body`, as it always has
    assert replay(_two_bodies(sync_spy, async_spy, _noop), cassette, serve=True) == []
    assert (sync_spy.call_count, async_spy.call_count) == (1, 0)  # served on the sync path
    # A PR makes the graph async and edits the async body: the recorded fingerprint matches.
    drifts = replay(_two_bodies(sync_spy, async_spy, _anoop, out=3), cassette, serve=True)
    assert "a" in [d.node for d in drifts]
    assert (sync_spy.call_count, async_spy.call_count) == (1, 1)


def test_a_sync_runnable_lambda_beside_an_async_node_is_recorded_and_served() -> None:
    spy = Mock(return_value={"x": 2})

    def a(state: S) -> S:
        return cast(S, spy(state))

    cassette = record(_pair(RunnableLambda(a), _anoop), {"x": 1})
    assert cassette.steps[0].code is not None
    assert replay(_pair(RunnableLambda(a), _anoop), cassette, serve=True) == []
    assert spy.call_count == 1


def test_a_halt_cancels_a_parallel_async_call_and_leaves_it_not_checked() -> None:
    spy = Mock()

    async def slow(state: S) -> S:
        await asyncio.sleep(0.05)
        spy(state)
        return {}

    def build(first_out: int) -> CompiledStateGraph[S]:
        async def first(state: S) -> S:
            return {"x": first_out}

        async def diverges(state: S) -> S:  # unchanged, but its input follows first's output
            return {"route": "done"}

        g = StateGraph(S)
        g.add_node("first", first)
        g.add_node("diverges", diverges)
        g.add_node("slow", partial(slow))  # not a plain function, so it can't be what halts
        g.add_edge(START, "first")
        g.add_edge("first", "diverges")
        g.add_edge("first", "slow")
        g.add_edge("diverges", END)
        g.add_edge("slow", END)
        return g.compile()

    cassette = record(build(1), {"x": 0})
    assert spy.call_count == 1
    r = replay_run(build(2), cassette, serve=True, on_divergence="fail")
    assert r.halt is not None and (r.halt.node, r.halt.why) == ("diverges", "diverged")
    assert r.not_checked == ("diverges", "slow")
    assert spy.call_count == 1  # cancelled in its sleep; on `.stream` it would have finished


def test_an_async_generator_node_runs_live_and_is_never_served() -> None:
    spy = Mock()

    def build() -> CompiledStateGraph[S]:
        async def a(state: S) -> AsyncIterator[S]:
            spy(state)
            yield {"x": 2}

        return _pair(a, _anoop)

    cassette = record(build(), {"x": 1})
    assert cassette.steps[0] == Step("a", {"x": 2})  # no input or fingerprint kept
    assert replay(build(), cassette, serve=True) == []
    assert spy.call_count == 2


def test_an_async_graph_in_a_running_loop_raises_an_lgcov_error_naming_the_fix() -> None:
    async def inside() -> None:
        record(_toy(Mock(return_value={"x": 2})), {"x": 1})

    with pytest.raises(RunningLoopError, match=r"lgcov CLI.*outside"):
        asyncio.run(inside())


def test_a_sync_graph_still_records_in_a_running_loop() -> None:
    async def inside() -> list[str]:
        return [s.node for s in record(linear(), {"x": 1}).steps]

    assert asyncio.run(inside()) == ["a", "b"]


# Async runs share one event loop per thread, open between runs. A client kept across runs,
# like a cached model client, holds connections that belong to the loop that opened them.
class _PooledClient:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None

    async def get(self) -> int:
        loop = asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            raise RuntimeError("Event loop is closed")
        self.loop = loop
        return 200


def test_a_client_kept_across_runs_works_on_every_run() -> None:
    client = _PooledClient()

    async def call(state: S) -> S:
        return {"x": await client.get()}

    graph = _pair(call, _anoop)
    cassette = record(graph, {"x": 1})
    assert replay(graph, cassette) == []
    assert [s.output for s in record(graph, {"x": 1}).steps] == [{"x": 200}, {"x": 200}]


def test_work_a_failed_node_left_running_does_not_carry_into_the_next_run() -> None:
    spy = Mock()  # a real call, still running and billing after its node failed
    left: list[asyncio.Future[None]] = []

    async def slow() -> None:
        await asyncio.sleep(60)  # far longer than the test: only a cancel ends it
        spy()

    async def fail() -> None:
        raise ValueError("bad draft")

    async def fans_out(state: S) -> S:
        left.append(asyncio.ensure_future(slow()))
        await asyncio.gather(left[0], fail())  # open_deep_research fans out this way
        return state

    with pytest.raises(NodeError):
        record(_pair(fans_out, _anoop), {"x": 1})
    # No clock in this check: a 50 ms timer racing the cancel failed on a slow CI runner.
    assert left[0].cancelled()
    record(_pair(_anoop, _anoop), {"x": 1})  # the next run, on the same loop
    assert spy.call_count == 0


_CALLER: ContextVar[int] = ContextVar("caller")


def test_each_run_sees_the_callers_context() -> None:
    seen = Mock()

    async def a(state: S) -> S:
        seen(_CALLER.get())
        return state

    for value in (1, 2):
        token = _CALLER.set(value)
        record(_pair(a, _anoop), {"x": 1})
        _CALLER.reset(token)
    assert [c.args[0] for c in seen.call_args_list] == [1, 2]


def test_async_runs_in_two_threads_at_once_each_get_a_loop() -> None:
    barrier = threading.Barrier(2, timeout=5)

    async def a(state: S) -> S:
        barrier.wait()  # both runs are inside their loops here
        return state

    graph = _pair(a, _anoop)
    with ThreadPoolExecutor(2) as pool:
        runs = list(pool.map(lambda x: record(graph, {"x": x}), [1, 2]))
    assert [r.steps[0].output for r in runs] == [{"x": 1}, {"x": 2}]


_RUNS_THEN_EXITS = """
import asyncio
import atexit
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from lgcov.cassette import record

loops = []
atexit.register(lambda: print(loops[0].is_closed()))  # registered first, so it runs last


class S(TypedDict, total=False):
    x: int


def first(state: S) -> S:  # a sync node: the loop's default executor runs it
    return {"x": 2}


async def second(state: S) -> S:
    loops.append(asyncio.get_running_loop())
    return state


g = StateGraph(S)
g.add_node("first", first)
g.add_node("second", second)
g.add_edge(START, "first")
g.add_edge("first", "second")
g.add_edge("second", END)
record(g.compile(), {"x": 1})
"""


def test_the_event_loop_closes_cleanly_at_exit() -> None:
    done = subprocess.run(
        [sys.executable, "-W", "error::ResourceWarning", "-c", _RUNS_THEN_EXITS],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "True\n", "")


def test_replay_serves_an_async_node_that_returns_command() -> None:
    spy = Mock(return_value={"x": 2})

    def build() -> CompiledStateGraph[S]:
        async def a(state: S) -> Command[Literal["b"]]:
            return Command(update=spy(state), goto="b")

        g = StateGraph(S)
        g.add_node("a", a)
        g.add_node("b", _anoop)
        g.add_edge(START, "a")
        g.add_edge("b", END)
        return g.compile()

    cassette = record(build(), {"x": 1})
    assert replay(build(), cassette, serve=True) == []
    assert spy.call_count == 1  # served: the recorded output, with no real call
