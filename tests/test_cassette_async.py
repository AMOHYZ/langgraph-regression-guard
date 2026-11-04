from typing import cast
from unittest.mock import Mock

from fixtures.graphs import S
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from lgcov.cassette import Cassette, record, replay, replay_run


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
