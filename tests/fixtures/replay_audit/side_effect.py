"""T152 fixture 7: a tool that writes. `build`: prep -> write, with a retry policy if given;
`abuild` is the same with `async def` nodes, and `in_a_subgraph` puts either inside a parent.
`build_with_store`: X writes to a Store and Y reads it back, so Y's state never changes."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import RetryPolicy


class State(TypedDict, total=False):
    payload: str
    written: bool
    handled: str


def build(
    tag: str,
    sink: Callable[[str], object],
    fail: Callable[[], object] | None = None,
    retry: RetryPolicy | None = None,
    on_error: Callable[[str], object] | None = None,
) -> CompiledStateGraph[State]:
    """`sink` is the write. `fail` runs after it, so a failed attempt has already written.
    `on_error`: `write` gets an error handler, which tells it the type of error it was given."""

    def prep(state: State) -> State:
        return {"payload": f"{tag}:{state['payload']}"}

    def write(state: State) -> State:
        sink(state["payload"])
        if fail is not None:
            fail()
        return {"written": True}

    def handler(state: State, error: NodeError) -> State:
        name = type(error.error).__name__
        if on_error is not None:
            on_error(name)
        return {"handled": name}

    g = StateGraph(State)
    g.add_node("prep", prep)
    g.add_node("write", write, retry_policy=retry, error_handler=handler if on_error else None)
    g.add_edge(START, "prep")
    g.add_edge("prep", "write")
    g.add_edge("write", END)
    return g.compile()


def abuild(
    tag: str,
    sink: Callable[[str], object],
    fail: Callable[[], object] | None = None,
    retry: RetryPolicy | None = None,
) -> CompiledStateGraph[State]:
    async def prep(state: State) -> State:
        return {"payload": f"{tag}:{state['payload']}"}

    async def write(state: State) -> State:
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


def in_a_subgraph(inner: CompiledStateGraph[State]) -> CompiledStateGraph[State]:
    g = StateGraph(State)
    g.add_node("sub", inner)
    g.add_edge(START, "sub")
    g.add_edge("sub", END)
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
