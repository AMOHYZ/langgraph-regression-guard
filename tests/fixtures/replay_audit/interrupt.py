"""T152 fixture 6: before -> gate -> after, where gate asks a human with `interrupt`."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, interrupt


class State(TypedDict, total=False):
    question: str
    approved: str
    answer: str


def no_spy() -> None:
    pass


def build(
    spy: Callable[[], object] = no_spy, checkpointer: Checkpointer = None
) -> CompiledStateGraph[State]:
    def before(state: State) -> State:
        return {"question": state["question"]}

    def gate(state: State) -> State:
        spy()
        return {"approved": interrupt({"ask": state["question"]})}

    def after(state: State) -> State:
        return {"answer": f"done: {state['approved']}"}

    g = StateGraph(State)
    g.add_node("before", before)
    g.add_node("gate", gate)
    g.add_node("after", after)
    g.add_edge(START, "before")
    g.add_edge("before", "gate")
    g.add_edge("gate", "after")
    g.add_edge("after", END)
    return g.compile(checkpointer=checkpointer)
