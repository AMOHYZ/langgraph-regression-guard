"""T109 self-check fixture `broken_subgraph`: respond became a subgraph, and its inner node
raises."""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    question: str
    needs_tool: bool
    tool_result: str
    answer: str


def classify(state: State) -> State:
    return {"needs_tool": "order" in state["question"].lower()}


def lookup_order(state: State) -> State:  # the 1 tool
    return {"tool_result": "order #42: shipped"}


def inner(state: State) -> State:
    raise ValueError("cannot draft the answer")


inner_builder = StateGraph(State)
inner_builder.add_node("inner", inner)
inner_builder.add_edge(START, "inner")
inner_builder.add_edge("inner", END)
sub = inner_builder.compile()


def route(state: State) -> Literal["lookup_order", "sub"]:  # the 1 conditional edge
    return "lookup_order" if state["needs_tool"] else "sub"


b = StateGraph(State)
b.add_node("classify", classify)
b.add_node("lookup_order", lookup_order)
b.add_node("sub", sub)
b.add_edge(START, "classify")
b.add_conditional_edges("classify", route)
b.add_edge("lookup_order", "sub")
b.add_edge("sub", END)
graph = b.compile()
