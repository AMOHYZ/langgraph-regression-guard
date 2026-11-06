"""T152 fixture 1: X -> Y -> outcome. The PR edits X's prompt; Y's code doesn't change."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

# A fake model: what each prompt gets back.
REPLIES: dict[str, dict[str, str]] = {
    "summarise": {"text": "refund"},
    "summarise briefly": {"text": "escalate"},
    "reword v1": {"text": "refund"},
    "reword v2": {"text": "refund"},
    "ticket v1": {"id": "ticket-1"},
    "ticket v2": {"id": "ticket-2"},
    "due v1": {"due": "2026-11-06T10:00:00Z"},
    "due v2": {"due": "2027-01-01T10:00:00Z"},
    "customer v1": {"customer": "cust aaaaaaaa-1111-4111-8111-111111111111"},
    "customer v2": {"customer": "cust bbbbbbbb-1111-4111-8111-111111111111"},
}


class State(TypedDict, total=False):
    question: str
    reply: dict[str, str]
    decision: str
    outcome: str


def decide(reply: dict[str, str]) -> str:
    """What Y does with X's reply: an id, a date-time or a UUID in it can decide the answer."""
    if "due" in reply:
        return "late" if reply["due"] > "2026-12-01" else "on time"
    if "customer" in reply:
        return "vip" if "aaaaaaaa" in reply["customer"] else "standard"
    return f"handle {reply.get('id', reply.get('text'))}"


def build(prompt: str, y_spy: Callable[[], object]) -> CompiledStateGraph[State]:
    def x(state: State) -> State:
        return {"reply": REPLIES[prompt]}

    def y(state: State) -> State:
        y_spy()
        return {"decision": decide(state["reply"])}

    def outcome(state: State) -> State:
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
