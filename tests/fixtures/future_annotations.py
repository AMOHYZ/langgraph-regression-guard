"""Tests only: states and a tool whose annotations stay strings until someone resolves them.

`from __future__ import annotations` leaves each one a string, which LangGraph and the tool's
schema resolve against `sys.modules[cls.__module__]`. Before #89 lgcov didn't register the
graph file there, so `lgcov record` crashed on these. Each graph's answer comes from a
dataclass, so a test can see the node or the tool really ran. The states hold strings, not
the dataclass: replay can't yet encode a dataclass value in the state, with or without the import.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


@dataclass
class Note:
    text: str


@dataclass
class DataclassState:
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    notes: Annotated[list[str], operator.add] = field(default_factory=list)


class TypedDictState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    notes: Annotated[list[str], operator.add]


@dataclass
class Query:
    city: str
    days: int = 1


@tool
def forecast(query: Query) -> str:
    """The forecast for a city."""
    return f"{query.city}: sunny for {query.days} days"


def note_dataclass(state: DataclassState) -> dict[str, object]:
    note = Note("dataclass state ok")
    return {"messages": [AIMessage(note.text)], "notes": [note.text]}


def note_typed_dict(state: TypedDictState) -> dict[str, object]:
    note = Note("typed dict state ok")
    return {"messages": [AIMessage(note.text)], "notes": [note.text]}


def call_forecast(state: DataclassState) -> dict[str, object]:
    call = {"name": "forecast", "args": {"query": {"city": "Riyadh", "days": 2}}, "id": "c1"}
    return {"messages": [AIMessage("", tool_calls=[call])]}


dataclass_state = (
    StateGraph(DataclassState).add_node(note_dataclass).add_edge(START, "note_dataclass").compile()
)
typed_dict_state = (
    StateGraph(TypedDictState)
    .add_node(note_typed_dict)
    .add_edge(START, "note_typed_dict")
    .compile()
)
tool_node = (
    StateGraph(DataclassState)
    .add_node(call_forecast)
    .add_node("tools", ToolNode([forecast]))
    .add_edge(START, "call_forecast")
    .add_edge("call_forecast", "tools")
    .compile()
)
