from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from lgcov.cassette import NodeFunc, diff_cassettes, record_io


class S(TypedDict):
    q: str
    a: str
    out: str


def build(prompt: str) -> dict[str, NodeFunc]:
    def plan(s: S) -> dict[str, Any]:
        return {"q": s["q"].strip()}

    def answer(s: S) -> dict[str, Any]:
        return {"a": f"{prompt}:{s['q']}"}

    def fmt(s: S) -> dict[str, Any]:
        return {"out": s["a"].upper()}

    return {"plan": plan, "answer": answer, "fmt": fmt}


def compile_graph(nodes: dict[str, NodeFunc]) -> CompiledStateGraph[S]:
    g = StateGraph(S)
    for n, f in nodes.items():
        g.add_node(n, f)
    g.add_edge(START, "plan")
    g.add_edge("plan", "answer")
    g.add_edge("answer", "fmt")
    g.add_edge("fmt", END)
    return g.compile()


def nodes_with(nodes: dict[str, NodeFunc], drop: str) -> dict[str, NodeFunc]:
    return {n: f for n, f in nodes.items() if n != drop}


def compile_graph_without_fmt(nodes: dict[str, NodeFunc]) -> CompiledStateGraph[S]:
    g = StateGraph(S)
    for n, f in nodes_with(nodes, "fmt").items():
        g.add_node(n, f)
    g.add_edge(START, "plan")
    g.add_edge("plan", "answer")
    g.add_edge("answer", END)
    return g.compile()


def test_unchanged_graph_has_no_drift() -> None:
    base = record_io(compile_graph, build("v1"), {"q": " hi "})
    same = record_io(compile_graph, build("v1"), {"q": " hi "})
    assert diff_cassettes(base, same) == {}


def test_prompt_tweak_flags_only_that_node_as_root() -> None:
    base = record_io(compile_graph, build("v1"), {"q": " hi "})
    new = record_io(compile_graph, build("v2"), {"q": " hi "})
    assert diff_cassettes(base, new) == {"answer": "root", "fmt": "propagated"}


def test_missing_node_is_flagged_as_removed() -> None:
    base = record_io(compile_graph, build("v1"), {"q": " hi "})
    new = record_io(compile_graph_without_fmt, build("v1"), {"q": " hi "})
    assert diff_cassettes(base, new)["fmt"] == "removed"
