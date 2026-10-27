from fixtures.graphs import S, _noop, fan_out_send, linear
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

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
