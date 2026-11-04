"""T168: a node that returns Command is recorded, served and compared, including where it sent
the run. Every open_deep_research node at the top level but the last returns one."""

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypedDict
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from lgcov.cassette import Cassette, Halt, Step, accept_nodes, record, replay_run, unservable


class S(TypedDict, total=False):
    x: int
    route: str
    other: str


Goto = str | Send | Sequence[str | Send]


def _routed(spy: Mock, goto: Goto = "b", x: int = 2, tail: str = "") -> CompiledStateGraph[S]:
    """a -> b or c, by the Command a returns; there are no other edges out of a."""

    def a(state: S) -> Command[Any]:
        spy("a")
        return Command(goto=goto, update={"x": x})

    def b(state: S) -> S:
        spy("b")
        return {"route": f"b{tail}"}

    def c(state: S) -> S:
        spy("c")
        return {"other": f"c{tail}"}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("c", c)
    g.add_edge(START, "a")
    g.add_edge("b", END)
    g.add_edge("c", END)
    return g.compile()


def _ran(spy: Mock) -> list[str]:
    return [c.args[0] for c in spy.call_args_list]


def _saved(cassette: Cassette) -> Cassette:
    return Cassette.from_json(cassette.to_json())


def _goto(step: Step) -> list[Any]:
    assert step.goto is not None
    return step.goto


def test_a_command_node_keeps_its_input_code_and_goto() -> None:
    step = record(_routed(Mock()), {"x": 1}).steps[0]
    assert (step.node, step.output, step.input, step.goto) == ("a", {"x": 2}, {"x": 1}, ["b"])
    assert step.code is not None


def test_a_saved_command_node_is_served_and_still_sends_the_run_on() -> None:
    spy = Mock()
    cassette = _saved(record(_routed(spy), {"x": 1}))
    assert unservable(_routed(spy), [cassette]) == 0
    spy.reset_mock()
    fresh = replay_run(_routed(spy), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    assert [(c.node, c.how) for c in fresh.calls] == [("a", "served"), ("b", "served")]


def test_a_served_command_routes_the_run_to_the_node_it_recorded() -> None:
    # `b` is live, so the only way it runs is a's served Command sending the run there.
    spy = Mock()
    cassette = _saved(record(_routed(spy, "c"), {"x": 1}))
    spy.reset_mock()
    fresh = replay_run(_routed(spy, "c", tail="!"), cassette, serve=True)
    assert _ran(spy) == ["c"]
    assert [(c.node, c.how) for c in fresh.calls] == [("a", "served"), ("c", "live")]
    assert [d.node for d in fresh.drifts] == ["c"]


def test_a_changed_update_is_drift_on_that_node() -> None:
    spy = Mock()
    cassette = record(_routed(spy), {"x": 1})
    fresh = replay_run(_routed(spy, x=3), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["a"]


def test_a_changed_goto_is_drift_on_that_node() -> None:
    spy = Mock()
    cassette = record(_routed(spy, "b"), {"x": 1})
    fresh = replay_run(_routed(spy, "c"), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["a", "b", "c"]
    drift = fresh.drifts[0]
    assert '"goto": ["b"]' in (drift.expected or [""])[0]
    assert '"goto": ["c"]' in (drift.actual or [""])[0]


def test_a_changed_goto_is_drift_even_when_every_output_matches() -> None:
    # The same node runs and returns the same, reached another way: only the Command differs.
    spy = Mock()
    cassette = record(_routed(spy, "b"), {"x": 1})
    fresh = replay_run(_routed(spy, [Send("b", {"x": 2})]), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["a"]
    assert '"send": "b"' in (fresh.drifts[0].actual or [""])[0]


@pytest.mark.parametrize(
    ("before", "after"),
    [("b", ["b"]), (["b", "c"], ["c", "b"])],
    ids=["one-or-a-list", "order"],
)
def test_the_same_goto_written_another_way_is_no_drift(before: Goto, after: Goto) -> None:
    spy = Mock()
    cassette = record(_routed(spy, before), {"x": 1})
    assert replay_run(_routed(spy, after), cassette, serve=True).drifts == ()


def _single(returns: Command[Any]) -> CompiledStateGraph[S]:
    def a(state: S) -> Command[Any]:
        return returns

    def b(state: S) -> S:
        return {"route": f"b:{state.get('x')}"}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("b", END)
    return g.compile()


@pytest.mark.parametrize(
    ("returns", "update", "goto", "nodes"),
    [
        (Command(goto="b"), None, ["b"], ["a", "b"]),  # open_deep_research's clarify_with_user
        (Command(goto=END, update={"x": 2}), {"x": 2}, [END], ["a"]),
        (Command(update={"x": 2}), {"x": 2}, [], ["a"]),
        (Command(goto="b", update=[("x", 2)]), {"x": 2}, ["b"], ["a", "b"]),
    ],
    ids=["no-update", "to-end", "no-goto", "update-as-pairs"],
)
def test_each_shape_of_command_is_recorded_and_served(
    returns: Command[Any], update: Any, goto: list[str], nodes: list[str]
) -> None:
    cassette = _saved(record(_single(returns), {"x": 1}))
    assert [(s.output, s.goto) for s in cassette.steps[:1]] == [(update, goto)]
    fresh = replay_run(_single(returns), cassette, serve=True)
    assert fresh.drifts == ()
    assert [c.how for c in fresh.calls] == ["served"] * len(nodes)
    assert [s.node for s in fresh.steps] == nodes


def test_a_step_that_returned_no_command_saves_no_goto() -> None:
    # So every cassette with no Command in it saves byte for byte as it did.
    def a(state: S) -> S:
        return {"x": 2}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    cassette = record(g.compile(), {"x": 1})
    assert cassette.steps[0].goto is None
    assert "goto" not in cassette.to_json()
    assert "goto" in record(_routed(Mock()), {"x": 1}).to_json()


def test_a_cassette_from_before_goto_was_kept_is_not_drift() -> None:
    # Saved before T168: a Command node's step has its update and nothing else.
    legacy = Cassette.from_json(
        '{"inputs": {"x": 1}, "steps": [{"node": "a", "output": {"x": 2}},'
        ' {"node": "b", "output": {"route": "b"}}]}'
    )
    spy = Mock()
    fresh = replay_run(_routed(spy), legacy, serve=True)
    assert fresh.drifts == ()
    assert _ran(spy) == ["a", "b"]  # no input or code recorded, so nothing to serve from


def test_accept_nodes_sees_a_goto_change() -> None:
    spy = Mock()
    base = record(_routed(spy, "b"), {"x": 1})
    same = record(_routed(spy, "b"), {"x": 1})
    assert accept_nodes(base, same, {"a"})[1] == set()
    other = record(_routed(spy, [Send("b", {"x": 2})]), {"x": 1})
    accepted, changed = accept_nodes(base, other, {"a"})
    assert changed == {"a"}
    assert accepted.steps[0].goto == [{"send": "b", "arg": {"x": 2}}]


def test_a_changed_update_is_a_divergence_downstream() -> None:
    spy = Mock()
    cassette = record(_routed(spy, x=2), {"x": 1})
    fresh = replay_run(_routed(spy, x=3), cassette, serve=True, on_divergence="fail")
    assert fresh.halt == Halt("b", "diverged")


# A Command holding LangChain messages, as open_deep_research's supervisor loop does.
def _call(args: dict[str, Any] | None = None) -> ToolCall:
    return {
        "name": "search",
        "args": args or {"q": "x"},
        "id": f"call_{uuid4().hex[:8]}",
        "type": "tool_call",
    }


def _provider_check(messages: Sequence[BaseMessage]) -> None:
    # What a chat API does with the history it's sent: a tool result must answer an earlier call.
    asked: set[str | None] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            asked.update(c["id"] for c in m.tool_calls)
        if isinstance(m, ToolMessage) and m.tool_call_id not in asked:
            raise ValueError(f"400: tool_call_id {m.tool_call_id} answers no call")


def _answer(state: MessagesState) -> Command[Any]:
    _provider_check(state["messages"])
    if isinstance(state["messages"][-1], ToolMessage):
        return Command(goto=END, update={"messages": [AIMessage("done", id=str(uuid4()))]})
    reply = AIMessage("", id=str(uuid4()), tool_calls=[_call()])
    return Command(goto="tools", update={"messages": [reply]})


def _answer_reworded(state: MessagesState) -> Command[Any]:
    returned = _answer(state)  # a new prompt: new code, the same answer, new ids
    return returned


def _asked(state: MessagesState) -> ToolCall:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)  # a served message must still be a message
    return last.tool_calls[0]


def _tools(state: MessagesState) -> Command[Literal["model"]]:
    call = _asked(state)
    result = ToolMessage("found", tool_call_id=call["id"], name=call["name"])
    return Command(goto="model", update={"messages": [result]})


def _counted(name: str, fn: Callable[..., Any], spy: Mock) -> Callable[..., Any]:
    def node(state: MessagesState) -> Any:
        spy(name)
        return fn(state)

    return node


def _acounted(name: str, fn: Callable[..., Any], spy: Mock) -> Callable[..., Any]:
    async def node(state: MessagesState) -> Any:
        spy(name)
        return fn(state)

    return node


Wrap = Callable[[str, Callable[..., Any], Mock], Callable[..., Any]]
PROMPT = {"messages": [{"role": "user", "content": "hi"}]}


def _loop(
    spy: Mock, model: Callable[..., Any] = _answer, wrap: Wrap = _counted
) -> CompiledStateGraph[MessagesState]:
    """model -> tools -> model, all by Command: open_deep_research's supervisor and researcher."""
    g = StateGraph(MessagesState)
    g.add_node("model", wrap("model", model, spy))
    g.add_node("tools", wrap("tools", _tools, spy))
    g.add_edge(START, "model")
    return g.compile()


@pytest.mark.parametrize("wrap", [_counted, _acounted], ids=["sync", "async"])
def test_a_loop_of_command_nodes_holding_messages_is_served(wrap: Wrap) -> None:
    spy = Mock()
    cassette = _saved(record(_loop(spy, wrap=wrap), PROMPT))
    assert isinstance(cassette.steps[0].output["messages"][0], AIMessage)
    spy.reset_mock()
    fresh = replay_run(_loop(spy, wrap=wrap), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    assert [c.how for c in fresh.calls] == ["served"] * 3


def test_a_served_command_s_update_answers_the_call_this_run_made() -> None:
    # lgcov.rebind on the Command's update. Without it the next live model call gets a 400.
    spy = Mock()
    cassette = _saved(record(_loop(spy), PROMPT))
    fresh = replay_run(_loop(spy, _answer_reworded), cassette, serve=True)
    assert [(c.node, c.how) for c in fresh.calls] == [
        ("model", "live"),
        ("tools", "served"),
        ("model", "live"),  # its check passed: the history it was sent is valid
    ]
    assert fresh.drifts == ()
    asked = fresh.steps[0].output["messages"][0].tool_calls[0]["id"]
    assert fresh.steps[1].output["messages"][0].tool_call_id == asked


def _fans_out(state: MessagesState) -> Command[Any]:
    # Tool calls handed to workers, by a Send in the Command.
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    return Command(goto=[Send("worker", {"call": c}) for c in last.tool_calls])


def _worker(state: dict[str, ToolCall]) -> MessagesState:
    call = state["call"]
    return {"messages": [ToolMessage("found", tool_call_id=call["id"], name=call["name"])]}


def _fanning(spy: Mock, model: Callable[..., Any]) -> CompiledStateGraph[MessagesState]:
    g = StateGraph(MessagesState)
    g.add_node("model", model)
    g.add_node("tools", _fans_out)
    g.add_node("worker", _counted("worker", _worker, spy))
    g.add_edge(START, "model")
    g.add_edge("worker", "model")
    return g.compile()


def test_a_send_in_a_served_command_carries_this_run_s_ids() -> None:
    spy = Mock()
    cassette = _saved(record(_fanning(spy, _answer), PROMPT))
    assert _goto(cassette.steps[1])[0]["send"] == "worker"
    assert isinstance(_goto(cassette.steps[1])[0]["arg"]["call"]["id"], str)
    spy.reset_mock()
    fresh = replay_run(_fanning(spy, _answer_reworded), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])  # served, and model's check passed
    made = fresh.steps[0].output["messages"][0].tool_calls[0]["id"]
    assert _goto(fresh.steps[1])[0]["arg"]["call"]["id"] == made


def _both_calls(state: MessagesState) -> Command[Any]:
    reply = AIMessage("", id=str(uuid4()), tool_calls=[_call(), _call()])
    return Command(goto="tools", update={"messages": [reply]})


def _both_calls_reworded(state: MessagesState) -> Command[Any]:
    returned = _both_calls(state)  # new code, the same answer
    return returned


def _in_order(state: MessagesState) -> Command[Any]:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    results = [ToolMessage("found", tool_call_id=c["id"], name=c["name"]) for c in last.tool_calls]
    return Command(goto="late", update={"messages": results})


def _other_order(state: MessagesState) -> Command[Any]:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    results = [
        ToolMessage("found", tool_call_id=c["id"], name=c["name"])
        for c in reversed(last.tool_calls)
    ]
    return Command(goto="late", update={"messages": results})


def _late(state: MessagesState) -> Command[Any]:
    first = state["messages"][-2]
    assert isinstance(first, ToolMessage)
    result = ToolMessage("late", tool_call_id=first.tool_call_id, name="search")
    return Command(goto=END, update={"messages": [result]})


def _ambiguous(
    spy: Mock, model: Callable[..., Any], tools: Callable[..., Any]
) -> CompiledStateGraph[MessagesState]:
    g = StateGraph(MessagesState)
    g.add_node("model", _counted("model", model, spy))
    g.add_node("tools", _counted("tools", tools, spy))
    g.add_node("late", _counted("late", _late, spy))
    g.add_edge(START, "model")
    return g.compile()


def test_a_command_whose_ids_can_t_be_fitted_runs_live_and_says_so() -> None:
    spy = Mock()
    cassette = _saved(record(_ambiguous(spy, _both_calls, _in_order), PROMPT))
    spy.reset_mock()
    changed = _ambiguous(spy, _both_calls_reworded, _other_order)
    fresh = replay_run(changed, cassette, serve=True)
    assert fresh.rebind_skipped == ("late",)
    assert _ran(spy) == ["model", "tools", "late"]
    assert fresh.drifts == ()


def test_a_message_in_a_command_is_masked_in_its_compared_form() -> None:
    def graph(text: str) -> CompiledStateGraph[MessagesState]:
        def node(state: MessagesState) -> Command[Any]:
            message = HumanMessage(text, id=str(uuid4()))
            return Command(goto=[Send("w", {"messages": [message]})])

        def w(state: MessagesState) -> MessagesState:
            return {"messages": [AIMessage(str(state["messages"][-1].content))]}

        g = StateGraph(MessagesState)
        g.add_node("n", node)
        g.add_node("w", w)
        g.add_edge(START, "n")
        g.add_edge("w", END)
        return g.compile()

    cassette = _saved(record(graph("task"), PROMPT))
    assert replay_run(graph("task"), cassette).drifts == ()  # new ids in the Send's payload
    assert [d.node for d in replay_run(graph("other"), cassette).drifts] == ["n", "w"]


def test_a_saved_goto_is_plain_json() -> None:
    saved = json.loads(record(_routed(Mock(), [Send("b", {"x": 2}), "c"]), {"x": 1}).to_json())
    assert saved["steps"][0]["goto"] == [{"send": "b", "arg": {"x": 2}}, "c"]


def test_a_step_built_by_hand_still_takes_four_fields() -> None:
    assert Step("a", 1, 2, "c") == Step(node="a", output=1, input=2, code="c", goto=None)
