"""T133 case 7: parallel Send writers that update the same channel.

With a reducer the run finishes; without one LangGraph raises `InvalidUpdateError`.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send


def _last(left: int, right: int) -> int:
    return right


class Reduced(TypedDict):
    x: Annotated[int, _last]


class Plain(TypedDict, total=False):
    x: int


def build_reduced(left: int = 1, right: int = 10) -> CompiledStateGraph[Reduced]:
    def left_node(state: Reduced) -> Reduced:
        return {"x": left}

    def right_node(state: Reduced) -> Reduced:
        return {"x": right}

    def fan(state: Reduced) -> list[Send]:
        return [Send("left", state), Send("right", state)]

    g = StateGraph(Reduced)
    g.add_node("left", left_node)
    g.add_node("right", right_node)
    g.add_conditional_edges(START, fan, ["left", "right"])
    g.add_edge("left", END)
    g.add_edge("right", END)
    return g.compile()


def build_conflict(left: int = 1, right: int = 10) -> CompiledStateGraph[Plain]:
    def left_node(state: Plain) -> Plain:
        return {"x": left}

    def right_node(state: Plain) -> Plain:
        return {"x": right}

    def fan(state: Plain) -> list[Send]:
        return [Send("left", state), Send("right", state)]

    g = StateGraph(Plain)
    g.add_node("left", left_node)
    g.add_node("right", right_node)
    g.add_conditional_edges(START, fan, ["left", "right"])
    g.add_edge("left", END)
    g.add_edge("right", END)
    return g.compile()
