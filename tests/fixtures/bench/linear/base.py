"""T101a fixture: three nodes in a line; the PR edits b's prompt."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

PROMPT = "Answer briefly."  # head.py changes this line


class State(TypedDict, total=False):
    question: str
    a: str
    b: str
    c: str


def a(state: State) -> State:
    return {"a": state["question"].lower()}


def b(state: State) -> State:
    return {"b": f"{PROMPT} {state['a']}"}


def c(state: State) -> State:
    return {"c": state["b"].upper()}


builder = StateGraph(State)
builder.add_node("a", a)
builder.add_node("b", b)
builder.add_node("c", c)
builder.add_edge(START, "a")
builder.add_edge("a", "b")
builder.add_edge("b", "c")
builder.add_edge("c", END)
graph = builder.compile()
