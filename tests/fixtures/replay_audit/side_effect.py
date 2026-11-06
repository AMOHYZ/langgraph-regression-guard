"""T152 fixture 7: a tool that writes. `build`: prep -> write, with a retry policy if given.
`build_with_store`: X writes to a Store and Y reads it back, so Y's state never changes."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import RetryPolicy


class State(TypedDict, total=False):
    payload: str
    written: bool


def build(
    tag: str,
    sink: Callable[[str], object],
    fail: Callable[[], object] | None = None,
    retry: RetryPolicy | None = None,
) -> CompiledStateGraph[State]:
    """`sink` is the write. `fail` runs after it, so a failed attempt has already written."""

    def prep(state: State) -> State:
        return {"payload": f"{tag}:{state['payload']}"}

    def write(state: State) -> State:
        sink(state["payload"])
        if fail is not None:
            fail()
        return {"written": True}

    g = StateGraph(State)
    g.add_node("prep", prep)
    g.add_node("write", write, retry_policy=retry)
    g.add_edge(START, "prep")
    g.add_edge("prep", "write")
    g.add_edge("write", END)
    return g.compile()


class StoreState(TypedDict, total=False):
    written: bool
    summary: str


def build_with_store(
    note: str, backend: BaseStore, spy: Callable[[], object]
) -> CompiledStateGraph[StoreState]:
    def x(state: StoreState, *, store: BaseStore) -> StoreState:
        store.put(("audit",), "note", {"text": note})
        return {"written": True}

    def y(state: StoreState, *, store: BaseStore) -> StoreState:
        spy()
        item = store.get(("audit",), "note")
        return {"summary": item.value["text"] if item else ""}

    g = StateGraph(StoreState)
    g.add_node("x", x)
    g.add_node("y", y)
    g.add_edge(START, "x")
    g.add_edge("x", "y")
    g.add_edge("y", END)
    return g.compile(store=backend)
