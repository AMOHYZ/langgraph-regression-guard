from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from pydantic import BaseModel


class S(TypedDict, total=False):
    x: int
    route: str


def _noop(state: S) -> S:
    return state


def _tools(state: S) -> S:
    return {"route": "answer"}


def _worker(state: S) -> S:
    # Send() invocations run in the same superstep; writing a shared key back
    # here would need a reducer, so parallel workers emit no update.
    return {}


def linear() -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def conditional() -> CompiledStateGraph[S]:
    def router(state: S) -> Literal["tools", "answer"]:
        return "tools" if state.get("route") == "tools" else "answer"

    g = StateGraph(S)
    g.add_node("agent", _noop)
    g.add_node("tools", _tools)
    g.add_node("answer", _noop)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", router, ["tools", "answer"])
    g.add_edge("tools", "agent")
    g.add_edge("answer", END)
    return g.compile()


def with_subgraph() -> CompiledStateGraph[S]:
    g = StateGraph(S)
    g.add_node("prep", _noop)
    g.add_node("inner", linear())
    g.add_edge(START, "prep")
    g.add_edge("prep", "inner")
    g.add_edge("inner", END)
    return g.compile()


def fan_out_send() -> CompiledStateGraph[S]:
    def fan(state: S) -> list[Send]:
        return [Send("worker", {"x": i}) for i in range(3)]

    g = StateGraph(S)
    g.add_node("plan", _noop)
    g.add_node("worker", _worker)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan, ["worker"])
    g.add_edge("worker", END)
    return g.compile()


# A state that is a class: LangGraph hands each node an instance, not a dict. The subclasses
# are a PR that edits a default, which no node's code holds.
class AskModel(BaseModel):
    question: str = ""
    max_chars: int = 60
    answer: str = ""


class AskModelShorter(AskModel):
    max_chars: int = 12


@dataclass
class AskData:
    question: str = ""
    max_chars: int = 60
    answer: str = ""


@dataclass
class AskDataShorter(AskData):
    max_chars: int = 12


Ask = AskModel | AskData  # not a class, so LangGraph gives each node the graph's state class
ASK = {"question": "Where is my order? It was due on Monday."}


def _prep(state: Ask) -> dict[str, str]:
    return {"answer": ""}


def _cut(state: Ask) -> dict[str, str]:
    return {"answer": state.question[: state.max_chars]}


def _cut_half(state: Ask) -> dict[str, str]:  # a PR that edits node code
    return {"answer": state.question[: state.max_chars // 2]}


def ask(state: type[Ask], half: bool = False) -> CompiledStateGraph[Ask]:
    g = StateGraph(state)
    g.add_node("prep", _prep)
    g.add_node("cut", _cut_half if half else _cut)
    g.add_edge(START, "prep")
    g.add_edge("prep", "cut")
    g.add_edge("cut", END)
    return g.compile()
