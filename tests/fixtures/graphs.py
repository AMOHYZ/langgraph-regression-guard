from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send


class S(TypedDict, total=False):
    x: int
    route: str


def _noop(state: S) -> S:
    return state


def _tools(state: S) -> S:
    return {"route": "answer"}


def _worker(state: S) -> S:
    # Send() invocations run in the same superstep; writing a shared key back
    # here would need a reducer, so parallel workers emit no update.
    return {}


def linear() -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def conditional() -> CompiledStateGraph[S]:
    def router(state: S) -> Literal["tools", "answer"]:
        return "tools" if state.get("route") == "tools" else "answer"

    g = StateGraph(S)
    g.add_node("agent", _noop)
    g.add_node("tools", _tools)
    g.add_node("answer", _noop)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", router, ["tools", "answer"])
    g.add_edge("tools", "agent")
    g.add_edge("answer", END)
    return g.compile()


def with_subgraph() -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("prep", _noop)
    g.add_node("inner", linear())
    g.add_edge(START, "prep")
    g.add_edge("prep", "inner")
    g.add_edge("inner", END)
    return g.compile()


def fan_out_send() -> CompiledStateGraph[S]:
    def fan(state: S) -> list[Send]:
        return [Send("worker", {"x": i}) for i in range(3)]

    g = StateGraph(S)
    g.add_node("plan", _noop)
    g.add_node("worker", _worker)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan, ["worker"])
    g.add_edge("worker", END)
    return g.compile()
