"""T101a fixture: a subgraph between two parent nodes; the PR edits the node inside it."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

STYLE = "long"  # the PR's change


class State(TypedDict, total=False):
    question: str
    draft: str
    answer: str


def inner(state: State) -> State:
    return {"draft": f"{STYLE}:{state['question']}"}


inner_builder = StateGraph(State)
inner_builder.add_node("inner", inner)
inner_builder.add_edge(START, "inner")
inner_builder.add_edge("inner", END)
sub = inner_builder.compile()


def before(state: State) -> State:
    return {"question": state["question"].strip()}


def after(state: State) -> State:
    return {"answer": state["draft"].upper()}


builder = StateGraph(State)
builder.add_node("before", before)
builder.add_node("sub", sub)
builder.add_node("after", after)
builder.add_edge(START, "before")
builder.add_edge("before", "sub")
builder.add_edge("sub", "after")
builder.add_edge("after", END)
graph = builder.compile()
