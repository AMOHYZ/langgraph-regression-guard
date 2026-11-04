"""T165: node input and output that hold LangChain messages, as a MessagesState agent's
do, are compared, served and saved like any other state."""

import time
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypedDict, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from lgcov.cassette import (
    Budget,
    Cassette,
    Halt,
    Step,
    accept_nodes,
    record,
    replay_run,
    unservable,
)

PROMPT = {"messages": [{"role": "user", "content": "hi"}]}
Node = Callable[..., MessagesState]
Agent = CompiledStateGraph[MessagesState]


def _call(args: dict[str, Any]) -> ToolCall:
    return {"name": "search", "args": args, "id": f"call_{uuid4().hex[:8]}", "type": "tool_call"}


def _reply(text: str, *calls: ToolCall, **fields: Any) -> AIMessage:
    # As a real chat model answers: new ids and new metadata on every call.
    now = time.perf_counter_ns()
    defaults = {
        "id": f"run-{uuid4()}",
        "response_metadata": {"model_name": "fake", "created": now},
        "usage_metadata": {"input_tokens": now, "output_tokens": 5, "total_tokens": now + 5},
    }
    return AIMessage(text, tool_calls=list(calls), **{**defaults, **fields})


def _answers(text: str = "thinking", args: dict[str, Any] | None = None, **fields: Any) -> Node:
    def model(state: MessagesState) -> MessagesState:
        return {"messages": [_reply(text, _call(args or {"q": "x"}), **fields)]}

    return model


_model = _answers()


def _model_refactored(state: MessagesState) -> MessagesState:
    reply = _reply("thinking", _call({"q": "x"}))  # new code, the same answer
    return {"messages": [reply]}


def _asked(state: MessagesState) -> ToolCall:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)  # a served message must still be a message
    return last.tool_calls[0]


def _returns(content: str = "found", **fields: Any) -> Node:
    def tools(state: MessagesState) -> MessagesState:
        # As open_deep_research's tool nodes do: each result is built from the call's id.
        call = _asked(state)
        return {
            "messages": [ToolMessage(content, tool_call_id=call["id"], name=call["name"], **fields)]
        }

    return tools


_tools = _returns()


def _tools_refactored(state: MessagesState) -> MessagesState:
    call = _asked(state)  # new code, the same result: a live node right after a served one
    reply = ToolMessage("found", tool_call_id=call["id"], name=call["name"])
    return {"messages": [reply]}


def _answer(state: MessagesState) -> MessagesState:
    return {"messages": [_reply(f"final: {state['messages'][-1].content}")]}


def _counted(name: str, fn: Node, spy: Mock) -> Node:
    def node(state: MessagesState) -> MessagesState:
        spy(name)
        return fn(state)

    return node


def _agent(spy: Mock, model: Node = _model, tools: Node = _tools) -> Agent:
    """model -> tools -> answer. `spy` hears each real call."""
    g = StateGraph(MessagesState)
    g.add_node("model", _counted("model", model, spy))
    g.add_node("tools", _counted("tools", tools, spy))
    g.add_node("answer", _counted("answer", _answer, spy))
    g.add_edge(START, "model")
    g.add_edge("model", "tools")
    g.add_edge("tools", "answer")
    g.add_edge("answer", END)
    return g.compile()


def _async_agent(spy: Mock) -> Agent:
    """The same agent with every node `async def`, like open_deep_research's."""

    def counted(name: str, fn: Node) -> Callable[..., Any]:
        async def node(state: MessagesState) -> MessagesState:
            spy(name)
            return fn(state)

        return node

    g = StateGraph(MessagesState)
    g.add_node("model", counted("model", _model))
    g.add_node("tools", counted("tools", _tools))
    g.add_node("answer", counted("answer", _answer))
    g.add_edge(START, "model")
    g.add_edge("model", "tools")
    g.add_edge("tools", "answer")
    g.add_edge("answer", END)
    return g.compile()


def _ran(spy: Mock) -> list[str]:
    return [c.args[0] for c in spy.call_args_list]


def _one(fn: Callable[..., Any]) -> Agent:
    g = StateGraph(MessagesState)
    g.add_node("n", fn)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


def _drifted(before: Node, after: Node) -> list[str]:
    spy = Mock()
    cassette = record(_agent(spy, before), PROMPT)
    return [d.node for d in replay_run(_agent(spy, after), cassette, serve=True).drifts]


def test_a_node_returning_messages_replays_without_a_type_error() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    fresh = replay_run(_agent(spy), cassette, serve=True)
    assert fresh.drifts == ()
    assert [s.node for s in fresh.steps] == ["model", "tools", "answer"]


def test_human_and_tool_messages_replay_too() -> None:
    def node(state: MessagesState) -> MessagesState:
        return {
            "messages": [
                HumanMessage("a question", id=str(uuid4())),
                ToolMessage("a result", tool_call_id=f"call_{uuid4().hex}", name="search"),
            ]
        }

    cassette = Cassette.from_json(record(_one(node), PROMPT).to_json())
    assert replay_run(_one(node), cassette).drifts == ()


def test_runs_that_differ_only_in_masked_fields_compare_equal() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    spy.reset_mock()
    fresh = replay_run(_agent(spy), cassette)  # all live: new ids, new metadata, new call ids
    assert (fresh.drifts, _ran(spy)) == ((), ["model", "tools", "answer"])
    was, now = cassette.steps[0].output["messages"][0], fresh.steps[0].output["messages"][0]
    assert was.id != now.id and was.tool_calls[0]["id"] != now.tool_calls[0]["id"]
    assert was.usage_metadata != now.usage_metadata
    assert was.response_metadata != now.response_metadata


def test_a_content_change_is_drift_on_that_node() -> None:
    assert _drifted(_model, _answers(text="thinking harder")) == ["model"]


def test_a_tool_call_args_change_is_drift_on_that_node() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    fresh = replay_run(_agent(spy, _answers(args={"q": "y"})), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["model"]
    assert '"q": "y"' in (fresh.drifts[0].actual or [""])[0]


@pytest.mark.parametrize(
    "key", ["id", "message_id", "request_id", "run_id", "span_id", "tool_call_id", "trace_id"]
)
def test_a_tool_argument_named_like_an_id_is_compared(key: str) -> None:
    # normalize masks these names in plain state, but here they are the tool's own data.
    assert _drifted(_answers(args={key: "42"}), _answers(args={key: "43"})) == ["model"]


def test_a_tool_result_change_is_drift_on_the_tool_node() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    fresh = replay_run(_agent(spy, tools=_returns("nothing")), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["answer", "tools"]


def test_a_tool_that_starts_failing_is_drift() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    fresh = replay_run(_agent(spy, tools=_returns(status="error")), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["tools"]


def test_a_malformed_tool_call_is_drift() -> None:
    def broken(args: str) -> dict[str, str]:
        return {"name": "search", "args": args, "id": str(uuid4()), "error": "bad JSON"}

    before = _answers(invalid_tool_calls=[broken('{"q": 1')])
    assert _drifted(before, _answers(invalid_tool_calls=[broken('{"q": 2')])) == ["model"]


def test_a_provider_s_numbers_in_additional_kwargs_are_no_drift() -> None:
    # Some providers report usage here, like ChatBedrock's additional_kwargs["usage"].
    before = _answers(additional_kwargs={"usage": {"prompt_tokens": 11}})
    assert _drifted(before, _answers(additional_kwargs={"usage": {"prompt_tokens": 12}})) == []


def test_a_streamed_answer_compares_like_the_whole_message() -> None:
    def whole(state: MessagesState) -> MessagesState:
        return {"messages": [AIMessage("hi", id=f"run-{uuid4()}")]}

    def streamed(state: MessagesState) -> MessagesState:
        return {"messages": [AIMessageChunk(content="hi", id=f"run-{uuid4()}")]}

    assert replay_run(_one(streamed), record(_one(whole), PROMPT)).drifts == ()


def test_a_node_whose_input_holds_messages_is_served() -> None:
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    assert all(s.input is not None and s.code is not None for s in cassette.steps)
    assert unservable(_agent(spy), [cassette]) == 0
    spy.reset_mock()
    fresh = replay_run(_agent(spy, _model_refactored), cassette, serve=True)
    # the later inputs hold model's new message, with new ids: they still match once masked
    assert (fresh.drifts, _ran(spy)) == ((), ["model"])


def test_a_saved_cassette_gives_its_messages_back_as_messages() -> None:
    cassette = record(_agent(Mock()), PROMPT)
    loaded = Cassette.from_json(cassette.to_json())
    assert loaded == cassette  # not dicts: a served output must stay a message
    types = [type(m) for s in loaded.steps for m in s.output["messages"]]
    assert types == [AIMessage, ToolMessage, AIMessage]
    assert isinstance(loaded.steps[0].input["messages"][0], HumanMessage)


@pytest.mark.parametrize("agent", [_agent, _async_agent])
def test_a_saved_cassette_serves_every_node_without_a_real_call(
    agent: Callable[[Mock], Agent],
) -> None:
    spy = Mock()
    cassette = Cassette.from_json(record(agent(spy), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(agent(spy), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    assert [c.how for c in fresh.calls] == ["served", "served", "served"]


def test_a_live_node_after_a_served_one_gets_the_tool_call_s_id() -> None:
    # What saving only the compared fields would break: the id is what `tools` builds from.
    spy = Mock()
    cassette = Cassette.from_json(record(_agent(spy), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(_agent(spy, tools=_tools_refactored), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), ["tools"])
    asked = fresh.steps[0].output["messages"][0].tool_calls[0]["id"]
    assert fresh.steps[1].output["messages"][0].tool_call_id == asked


class _NoReducer(TypedDict, total=False):
    messages: list[BaseMessage]


def test_a_served_message_reaches_a_channel_with_no_reducer_as_a_message() -> None:
    # No add_messages here to turn a served dict back into a message.
    def graph(suffix: str) -> CompiledStateGraph[_NoReducer]:
        def first(state: _NoReducer) -> _NoReducer:
            return {"messages": [AIMessage("hi", id=str(uuid4()))]}

        def second(state: _NoReducer) -> _NoReducer:
            text = f"{str(state['messages'][-1].content).upper()}{suffix}"
            return {"messages": [*state["messages"], AIMessage(text)]}

        g = StateGraph(_NoReducer)
        g.add_node("first", first)
        g.add_node("second", second)
        g.add_edge(START, "first")
        g.add_edge("first", "second")
        g.add_edge("second", END)
        return g.compile()

    cassette = Cassette.from_json(record(graph("!"), {"messages": []}).to_json())
    assert [d.node for d in replay_run(graph("?"), cassette, serve=True).drifts] == ["second"]


def test_ignore_paths_reach_into_a_message() -> None:
    spy = Mock()
    cassette = record(_agent(spy, _answers(args={"q": "x", "nonce": "0"})), PROMPT)
    changed = _agent(spy, _answers(text="other", args={"q": "x", "nonce": "1"}))
    assert [d.node for d in replay_run(changed, cassette).drifts] == ["model"]
    paths = ["/messages/*/content", "/messages/*/tool_calls/*/args/nonce"]
    assert replay_run(changed, cassette, paths).drifts == ()


def test_to_json_still_refuses_any_other_object() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        Cassette({}, [Step("a", object())]).to_json()


def test_accept_nodes_ignores_what_replay_masks() -> None:
    spy = Mock()
    nodes = {"model", "tools", "answer"}
    base, same = record(_agent(spy), PROMPT), record(_agent(spy), PROMPT)
    assert accept_nodes(base, same, nodes)[1] == set()
    other = record(_agent(spy, _answers(text="other")), PROMPT)
    assert accept_nodes(base, other, nodes)[1] == {"model"}
    assert accept_nodes(base, other, nodes, ["/messages/*/content"])[1] == set()


def test_accept_nodes_still_sees_a_real_change_in_plain_state() -> None:
    base = Cassette({}, [Step("a", {"id": "1", "x": 1})])
    assert accept_nodes(base, Cassette({}, [Step("a", {"id": "2", "x": 1})]), {"a"})[1] == set()
    assert accept_nodes(base, Cassette({}, [Step("a", {"id": "1", "x": 2})]), {"a"})[1] == {"a"}


def test_an_artifact_json_can_t_hold_is_saved_as_its_repr() -> None:
    # A paid recording must not be lost to a value replay never compares.
    tools = _returns(artifact=object())
    spy = Mock()
    loaded = Cassette.from_json(record(_agent(spy, tools=tools), PROMPT).to_json())
    assert "object object at" in loaded.steps[1].output["messages"][0].artifact
    spy.reset_mock()
    fresh = replay_run(_agent(spy, tools=tools), loaded, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])


def test_response_headers_never_reach_a_saved_cassette() -> None:
    # langchain-openai's include_response_headers=True puts them in response_metadata.
    metadata = {"model_name": "fake", "headers": {"set-cookie": "a-session-cookie"}}
    saved = record(_agent(Mock(), _answers(response_metadata=metadata)), PROMPT).to_json()
    assert "a-session-cookie" not in saved
    loaded = Cassette.from_json(saved)
    assert loaded.steps[0].output["messages"][0].response_metadata == {"model_name": "fake"}


def test_a_command_s_update_holding_messages_is_saved_and_compared() -> None:
    # open_deep_research's nodes all return Command. Serving them is T168; comparing is here.
    def graph(text: str) -> Agent:
        def node(state: MessagesState) -> Command[Any]:
            return Command(goto=END, update={"messages": [_reply(text)]})

        return _one(node)

    cassette = Cassette.from_json(record(graph("hi"), PROMPT).to_json())
    assert replay_run(graph("hi"), cassette, serve=True).drifts == ()
    assert [d.node for d in replay_run(graph("bye"), cassette, serve=True).drifts] == ["n"]


def _override(current: list[BaseMessage], new: Any) -> list[BaseMessage]:
    # open_deep_research's override_reducer: messages nested in a dict in a node's output.
    if isinstance(new, dict) and new.get("type") == "override":
        return list(new["value"])
    return cast(list[BaseMessage], add_messages(cast(Any, current), new))


class _Supervised(TypedDict, total=False):
    supervisor_messages: Annotated[list[BaseMessage], _override]


def _supervised(spy: Mock, brief: str) -> CompiledStateGraph[_Supervised]:
    def write_brief(state: _Supervised) -> dict[str, Any]:
        spy("write_brief")
        value = [SystemMessage("be brief", id=str(uuid4())), HumanMessage(brief, id=str(uuid4()))]
        return {"supervisor_messages": {"type": "override", "value": value}}

    def supervisor(state: _Supervised) -> dict[str, Any]:
        spy("supervisor")
        return {"supervisor_messages": [_reply(str(state["supervisor_messages"][-1].content))]}

    g = StateGraph(_Supervised)
    g.add_node("write_brief", write_brief)
    g.add_node("supervisor", supervisor)
    g.add_edge(START, "write_brief")
    g.add_edge("write_brief", "supervisor")
    g.add_edge("supervisor", END)
    return g.compile()


def test_messages_nested_in_a_dict_are_compared_saved_and_served() -> None:
    spy = Mock()
    cassette = Cassette.from_json(record(_supervised(spy, "a brief"), {}).to_json())
    assert replay_run(_supervised(spy, "a brief"), cassette).drifts == ()  # all new ids
    spy.reset_mock()
    fresh = replay_run(_supervised(spy, "a brief"), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    changed = replay_run(_supervised(spy, "a new brief"), cassette, serve=True)
    assert [d.node for d in changed.drifts] == ["supervisor", "write_brief"]


def test_a_single_message_without_a_list_is_compared_and_saved() -> None:
    def graph(text: str) -> Agent:
        def node(state: MessagesState) -> dict[str, BaseMessage]:
            return {"messages": _reply(text)}

        return _one(node)

    cassette = Cassette.from_json(record(graph("hi"), PROMPT).to_json())
    assert replay_run(graph("hi"), cassette, serve=True).drifts == ()
    assert [d.node for d in replay_run(graph("bye"), cassette, serve=True).drifts] == ["n"]


def test_parallel_workers_with_message_payloads_are_served_by_their_payload() -> None:
    spy = Mock()

    def graph() -> Agent:
        def fan(state: MessagesState) -> list[Send]:
            tasks = [HumanMessage(f"task {i}", id=str(uuid4())) for i in range(3)]
            return [Send("worker", {"messages": [t]}) for t in tasks]

        def worker(state: MessagesState) -> MessagesState:
            spy("worker")
            return {"messages": [_reply(f"did {state['messages'][-1].content}")]}

        g = StateGraph(MessagesState)
        g.add_node("worker", worker)
        g.add_conditional_edges(START, fan, ["worker"])
        g.add_edge("worker", END)
        return g.compile()

    cassette = Cassette.from_json(record(graph(), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(graph(), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    done = sorted(s.output["messages"][0].content for s in fresh.steps)
    assert done == ["did task 0", "did task 1", "did task 2"]


def test_a_subgraph_holding_messages_is_saved_and_compared() -> None:
    def graph(text: str) -> Agent:
        def inner(state: MessagesState) -> MessagesState:
            return {"messages": [_reply(text)]}

        g = StateGraph(MessagesState)
        g.add_node("sub", _one(inner))
        g.add_edge(START, "sub")
        g.add_edge("sub", END)
        return g.compile()

    cassette = Cassette.from_json(record(graph("hi"), PROMPT).to_json())
    assert replay_run(graph("hi"), cassette, serve=True).drifts == ()
    assert [d.node for d in replay_run(graph("bye"), cassette, serve=True).drifts] == ["sub"]


def test_a_changed_message_input_is_a_divergence() -> None:
    # T089 needs a node's input kept: with messages in it, main kept none and never diverged.
    spy = Mock()
    cassette = record(_agent(spy), PROMPT)
    changed = _agent(spy, _answers(text="other"))
    fresh = replay_run(changed, cassette, serve=True, on_divergence="fail")
    assert fresh.halt == Halt("tools", "diverged")
    assert fresh.not_checked == ("tools", "answer")


# A served output fits the ids this run made (lgcov.rebind).
def _provider_check(messages: Sequence[BaseMessage]) -> None:
    # What a chat API does with the history it's sent: a tool result must answer an earlier call.
    asked: set[str | None] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            asked.update(c["id"] for c in m.tool_calls)
        if isinstance(m, ToolMessage) and m.tool_call_id not in asked:
            raise ValueError(f"400: tool_call_id {m.tool_call_id} answers no call")


def _respond(state: MessagesState) -> MessagesState:
    _provider_check(state["messages"])
    if isinstance(state["messages"][-1], ToolMessage):
        return {"messages": [_reply("done")]}
    return {"messages": [_reply("", _call({"q": "x"}))]}


def _respond_reworded(state: MessagesState) -> MessagesState:
    reply = _respond(state)  # a new prompt: new code, the same answer, new ids
    return reply


def _route(state: MessagesState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def _loop(spy: Mock, model: Node = _respond) -> Agent:
    """model -> tools -> model, the shape of open_deep_research's supervisor and researcher."""
    g = StateGraph(MessagesState)
    g.add_node("model", _counted("model", model, spy))
    g.add_node("tools", _counted("tools", _tools, spy))
    g.add_edge(START, "model")
    g.add_conditional_edges("model", _route, ["tools", END])
    g.add_edge("tools", "model")
    return g.compile()


def test_a_served_tool_result_answers_the_call_this_run_made() -> None:
    spy = Mock()
    cassette = Cassette.from_json(record(_loop(spy), PROMPT).to_json())
    fresh = replay_run(_loop(spy, _respond_reworded), cassette, serve=True)
    assert [(c.node, c.how) for c in fresh.calls] == [
        ("model", "live"),
        ("tools", "served"),
        ("model", "live"),  # its check passed: the history it was sent is valid
    ]
    assert fresh.drifts == ()
    asked = fresh.steps[0].output["messages"][0].tool_calls[0]["id"]
    assert fresh.steps[1].output["messages"][0].tool_call_id == asked
    assert asked != cassette.steps[0].output["messages"][0].tool_calls[0]["id"]


def test_an_unchanged_loop_is_served_with_its_recorded_ids() -> None:
    spy = Mock()
    cassette = Cassette.from_json(record(_loop(spy), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(_loop(spy), cassette, serve=True)
    assert (fresh.drifts, _ran(spy), fresh.rebind_skipped) == ((), [], ())
    assert [s.output for s in fresh.steps] == [s.output for s in cassette.steps]


def _trimming(spy: Mock) -> Agent:
    def seed(state: MessagesState) -> MessagesState:
        spy("seed")
        return {"messages": [AIMessage("a"), AIMessage("b")]}

    def trim(state: MessagesState) -> dict[str, list[RemoveMessage]]:
        # LangGraph's documented way to shorten history. The ids here are this run's.
        spy("trim")
        return {"messages": [RemoveMessage(id=m.id) for m in state["messages"][:-1] if m.id]}

    def after(state: MessagesState) -> MessagesState:
        spy("after")
        return {"messages": [AIMessage(f"saw {len(state['messages'])}")]}

    g = StateGraph(MessagesState)
    g.add_node("seed", seed)
    g.add_node("trim", trim)
    g.add_node("after", after)
    g.add_edge(START, "seed")
    g.add_edge("seed", "trim")
    g.add_edge("trim", "after")
    g.add_edge("after", END)
    return g.compile()


def test_an_agent_that_trims_its_history_replays_with_no_real_call() -> None:
    spy = Mock()
    cassette = Cassette.from_json(record(_trimming(spy), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(_trimming(spy), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])
    assert fresh.steps[-1].output["messages"][0].content == "saw 1"


def test_a_worker_given_a_plain_tool_call_answers_this_run_s_id() -> None:
    def graph(spy: Mock, model: Node) -> Agent:
        def fan(state: MessagesState) -> list[Send] | str:
            last = state["messages"][-1]
            if not (isinstance(last, AIMessage) and last.tool_calls):
                return END
            return [Send("worker", {"call": c}) for c in last.tool_calls]

        def worker(state: dict[str, ToolCall]) -> MessagesState:
            spy("worker")
            call = state["call"]
            return {"messages": [ToolMessage("found", tool_call_id=call["id"], name=call["name"])]}

        g = StateGraph(MessagesState)
        g.add_node("model", model)
        g.add_node("worker", worker)
        g.add_edge(START, "model")
        g.add_conditional_edges("model", fan, ["worker", END])
        g.add_edge("worker", "model")
        return g.compile()

    spy = Mock()
    cassette = Cassette.from_json(record(graph(spy, _respond), PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(graph(spy, _respond_reworded), cassette, serve=True)
    assert (fresh.drifts, _ran(spy)) == ((), [])  # served, and model's check on it passed


def _two_calls(state: MessagesState) -> MessagesState:
    return {"messages": [_reply("", _call({"q": "x"}), _call({"q": "x"}))]}


def _two_calls_reworded(state: MessagesState) -> MessagesState:
    reply = _reply("", _call({"q": "x"}), _call({"q": "x"}))  # new code, the same answer
    return {"messages": [reply]}


def _answers_in_order(state: MessagesState) -> MessagesState:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    return {
        "messages": [
            ToolMessage("found", tool_call_id=c["id"], name=c["name"]) for c in last.tool_calls
        ]
    }


def _answers_in_the_other_order(state: MessagesState) -> MessagesState:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    return {
        "messages": [
            ToolMessage("found", tool_call_id=c["id"], name=c["name"])
            for c in reversed(last.tool_calls)
        ]
    }


def _late(state: MessagesState) -> MessagesState:
    # Its output names a recorded call, which is what makes its fit matter.
    first = state["messages"][-2]
    assert isinstance(first, ToolMessage)
    return {"messages": [ToolMessage("late", tool_call_id=first.tool_call_id, name="search")]}


def _ambiguous(spy: Mock, model: Node, tools: Node) -> Agent:
    g = StateGraph(MessagesState)
    g.add_node("model", _counted("model", model, spy))
    g.add_node("tools", _counted("tools", tools, spy))
    g.add_node("late", _counted("late", _late, spy))
    g.add_edge(START, "model")
    g.add_edge("model", "tools")
    g.add_edge("tools", "late")
    g.add_edge("late", END)
    return g.compile()


def test_an_ambiguous_fit_runs_the_step_live_and_says_so() -> None:
    spy = Mock()
    built = _ambiguous(spy, _two_calls, _answers_in_order)
    cassette = Cassette.from_json(record(built, PROMPT).to_json())
    spy.reset_mock()
    budget = Budget(None)
    # This run's tools answer in the other order, and the inputs still match once masked, so
    # the recorded first call pairs with both of this run's: `late` can't be fitted.
    changed = _ambiguous(spy, _two_calls_reworded, _answers_in_the_other_order)
    fresh = replay_run(changed, cassette, serve=True, budget=budget)
    assert fresh.rebind_skipped == ("late",)
    assert [(c.node, c.how) for c in fresh.calls] == [
        ("model", "live"),
        ("tools", "live"),
        ("late", "live"),
    ]
    assert _ran(spy) == ["model", "tools", "late"]
    assert len(budget.live) == 3  # a skipped serve is a live call: it counts
    assert fresh.drifts == ()
    last = fresh.steps[-1].output["messages"][0]
    assert last.tool_call_id == fresh.steps[1].output["messages"][0].tool_call_id


def test_a_fit_that_can_be_made_is_not_reported_as_skipped() -> None:
    spy = Mock()
    built = _ambiguous(spy, _two_calls, _answers_in_order)
    cassette = Cassette.from_json(record(built, PROMPT).to_json())
    spy.reset_mock()
    fresh = replay_run(
        _ambiguous(spy, _two_calls_reworded, _answers_in_order), cassette, serve=True
    )
    assert fresh.rebind_skipped == ()
    assert _ran(spy) == ["model"]  # tools and late served, fitted to the new ids
    first = fresh.steps[1].output["messages"][0].tool_call_id
    assert first == fresh.steps[0].output["messages"][0].tool_calls[0]["id"]
    assert fresh.steps[2].output["messages"][0].tool_call_id == first
