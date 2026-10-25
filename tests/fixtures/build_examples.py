"""T012: two tiny compiled LangGraph graphs (no LLM) used as fixtures.

Run `uv run python tests/fixtures/build_examples.py` to (re)write
tests/fixtures/linear.json and tests/fixtures/conditional.json from
compiled.get_graph().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

FIXTURES = Path(__file__).parent


class State(TypedDict, total=False):
    question: str
    answer: str
    route: str


def _passthrough(state: State) -> State:
    return {}


# Compiled graphs are typed Any on purpose: LangGraph's compiled type varies
# across versions (same rationale as lgcov.cassette.record).
def build_linear() -> Any:
    """start -> a -> b -> end"""
    g = StateGraph(State)
    g.add_node("a", _passthrough)
    g.add_node("b", _passthrough)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def _route(state: State) -> Literal["tool", "answer"]:
    return "tool" if state.get("route") == "tool" else "answer"


def build_conditional() -> Any:
    """start -> router -> (tool | answer) -> end"""
    g = StateGraph(State)
    g.add_node("router", _passthrough)
    g.add_node("tool", _passthrough)
    g.add_node("answer", _passthrough)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", _route, {"tool": "tool", "answer": "answer"})
    g.add_edge("tool", END)
    g.add_edge("answer", END)
    return g.compile()


def dump_raw(compiled: Any) -> dict[str, Any]:
    """Raw dump of compiled.get_graph(): node ids + (source, target, conditional)."""
    graph = compiled.get_graph()
    return {
        "nodes": sorted(graph.nodes.keys()),
        "edges": sorted(
            (
                {"source": e.source, "target": e.target, "conditional": bool(e.conditional)}
                for e in graph.edges
            ),
            key=lambda d: (d["source"], d["target"]),
        ),
    }


def main() -> None:
    for name, builder in (("linear", build_linear), ("conditional", build_conditional)):
        path = FIXTURES / f"{name}.json"
        path.write_text(json.dumps(dump_raw(builder()), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
