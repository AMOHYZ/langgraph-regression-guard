"""T152 fixture 4: prep -> sub -> after, with a checkpointer on the subgraph, the parent, or
neither. The PR edits a node inside the subgraph."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer


class State(TypedDict, total=False):
    value: int
    after: int


def no_spy() -> None:
    pass


def build(
    factor: int,
    spy: Callable[[], object] = no_spy,
    sub_checkpointer: Checkpointer = None,
    parent_checkpointer: Checkpointer = None,
) -> CompiledStateGraph[State]:
    def inner(state: State) -> State:
        spy()
        return {"value": state["value"] * factor}

    sub = StateGraph(State)
    sub.add_node("inner", inner)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)

    def prep(state: State) -> State:
        return {"value": state["value"] + 1}

    def after(state: State) -> State:
        return {"after": state["value"] * 10}

    g = StateGraph(State)
    g.add_node("prep", prep)
    g.add_node("sub", sub.compile(checkpointer=sub_checkpointer))
    g.add_node("after", after)
    g.add_edge(START, "prep")
    g.add_edge("prep", "sub")
    g.add_edge("sub", "after")
    g.add_edge("after", END)
    return g.compile(checkpointer=parent_checkpointer)
