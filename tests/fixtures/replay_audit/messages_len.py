"""T133 case 6: X → Y over add_messages; Y reads len(messages). REPLIES is module-global so
fingerprint follows it; normalize still masks message ids."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

REPLIES: tuple[AnyMessage, ...] = ()


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    answer: str


def x(state: State) -> State:
    return {"messages": list(REPLIES)}


def no_spy() -> None:
    pass


def build(spy: Callable[[], object] = no_spy) -> CompiledStateGraph[State]:
    def y(state: State) -> State:
        spy()
        return {"answer": str(len(state["messages"]))}

    g = StateGraph(State)
    g.add_node("x", x)
    g.add_node("y", y)
    g.add_edge(START, "x")
    g.add_edge("x", "y")
    g.add_edge("y", END)
    return g.compile()
