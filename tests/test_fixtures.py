from collections.abc import Callable

import pytest
from fixtures.graphs import S, conditional, fan_out_send, linear, with_subgraph
from langgraph.graph.state import CompiledStateGraph


@pytest.mark.parametrize(
    ("build", "expected_nodes"),
    [
        (linear, {"a", "b"}),
        (conditional, {"agent", "tools", "answer"}),
        (with_subgraph, {"prep", "inner"}),
        (fan_out_send, {"plan", "worker"}),
    ],
)
def test_fixture_compiles_with_expected_nodes(
    build: Callable[[], CompiledStateGraph[S]], expected_nodes: set[str]
) -> None:
    nodes = set(build().get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == expected_nodes


def test_conditional_run_with_tools_route_terminates() -> None:
    graph = conditional()
    result = graph.invoke({"route": "tools"})
    assert result["route"] == "answer"
