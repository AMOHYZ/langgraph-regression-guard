"""T152 fixture 8: fixture 1's shape with `async def` nodes, the regression check for T164."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class State(TypedDict, total=False):
    question: str
    reply: str
    decision: str
    outcome: str


def build(prompt: str, y_spy: Callable[[], object]) -> CompiledStateGraph[State]:
    async def x(state: State) -> State:
        return {"reply": f"{prompt}: {state['question']}"}

    async def y(state: State) -> State:
        y_spy()
        return {"decision": f"handle {state['reply']}"}

    async def outcome(state: State) -> State:
        return {"outcome": f"final: {state['decision']}"}

    g = StateGraph(State)
    g.add_node("x", x)
    g.add_node("y", y)
    g.add_node("outcome", outcome)
    g.add_edge(START, "x")
    g.add_edge("x", "y")
    g.add_edge("y", "outcome")
    g.add_edge("outcome", END)
    return g.compile()
