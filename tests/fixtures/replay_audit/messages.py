"""T152 fixture 5: X -> Y over `add_messages`. The PR changes what X replies with; Y reads one
attribute of the last message."""

from collections.abc import Callable
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    answer: str


def build(
    replies: tuple[AnyMessage, ...], spy: Callable[[], object], read: str = "content"
) -> CompiledStateGraph[State]:
    def x(state: State) -> State:
        return {"messages": list(replies)}

    def y(state: State) -> State:
        spy()
        return {"answer": str(getattr(state["messages"][-1], read))}

    g = StateGraph(State)
    g.add_node("x", x)
    g.add_node("y", y)
    g.add_edge(START, "x")
    g.add_edge("x", "y")
    g.add_edge("y", END)
    return g.compile()
