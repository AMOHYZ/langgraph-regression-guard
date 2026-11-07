"""T114, tests only: demo_graph.py, except that `respond` commits to the git repo named by
$LGCOV_MOVE_REPO each time it runs, so a gate run sees a locked commit move under it. Its
answers are the demo's. Without the variable it commits nothing. Never an example.
"""

import os
import subprocess
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
    repo = os.environ.get("LGCOV_MOVE_REPO")
    if repo:
        git = ["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "moved mid-run"]
        subprocess.run(git, check=True, capture_output=True)
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
