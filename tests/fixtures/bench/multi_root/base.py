"""T162 fixture: five nodes in a line; the PR changes b and c, and refactors d."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

PROMPT = "Answer briefly."  # head.py changes this line


class State(TypedDict, total=False):
    question: str
    a: str
    b: str
    c: str
    d: str
    e: str


def a(state: State) -> State:
    return {"a": state["question"].lower()}


def b(state: State) -> State:
    return {"b": f"{PROMPT} {state['a']}"}


def c(state: State) -> State:
    return {"c": state["b"].upper()}


def d(state: State) -> State:
    text = state["c"]
    return {"d": f"[{text}]"}


def e(state: State) -> State:
    return {"e": state["d"] + "!"}


builder = StateGraph(State)
for name, fn in (("a", a), ("b", b), ("c", c), ("d", d), ("e", e)):
    builder.add_node(name, fn)
builder.add_edge(START, "a")
builder.add_edge("a", "b")
builder.add_edge("b", "c")
builder.add_edge("c", "d")
builder.add_edge("d", "e")
builder.add_edge("e", END)
graph = builder.compile()
