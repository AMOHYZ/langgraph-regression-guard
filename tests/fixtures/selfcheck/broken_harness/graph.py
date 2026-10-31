"""T074 self-check fixture `broken_harness`: a fine graph, but no cassette."""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

SYSTEM_PROMPT = "You are a concise support agent."


class State(TypedDict, total=False):
    question: str
    needs_tool: bool
    tool_result: str
    answer: str


def fake_llm(prompt: str) -> str:
    return f"[{len(prompt)}] " + prompt[-40:]


def classify(state: State) -> State:
    return {"needs_tool": "order" in state["question"].lower()}


def lookup_order(state: State) -> State:  # the 1 tool
    return {"tool_result": "order #42: shipped"}


def respond(state: State) -> State:
    ctx = state.get("tool_result", "")
    return {"answer": fake_llm(f"{SYSTEM_PROMPT}\n{ctx}\n{state['question']}")}


def route(state: State) -> Literal["lookup_order", "respond"]:  # the 1 conditional edge
    return "lookup_order" if state["needs_tool"] else "respond"


b = StateGraph(State)
b.add_node("classify", classify)
b.add_node("lookup_order", lookup_order)
b.add_node("respond", respond)
b.add_edge(START, "classify")
b.add_conditional_edges("classify", route)
b.add_edge("lookup_order", "respond")
b.add_edge("respond", END)
graph = b.compile()
