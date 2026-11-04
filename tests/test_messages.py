"""T165: how a LangChain message is saved, loaded and shown to a judge."""

import json
from datetime import UTC, datetime

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from lgcov.messages import decode, encode, plain


def _round_trip(value: object) -> object:
    return json.loads(json.dumps(value, default=encode), object_hook=decode)


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(
            "hi",
            id="run-1",
            name="bot",
            tool_calls=[{"name": "search", "args": {"id": 7}, "id": "call_1", "type": "tool_call"}],
            invalid_tool_calls=[
                {
                    "name": "s",
                    "args": "{",
                    "id": "call_2",
                    "error": "bad",
                    "type": "invalid_tool_call",
                }
            ],
            additional_kwargs={"refusal": None},
            response_metadata={"model_name": "fake"},
            usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        ),
        AIMessage([{"type": "tool_use", "id": "toolu_1", "name": "s", "input": {"q": 1}}]),
        HumanMessage("q", id="u1", name="ann"),
        SystemMessage("be brief"),
        ToolMessage("r", tool_call_id="call_1", name="s", status="error", artifact={"rows": 1}),
        RemoveMessage(id="gone"),
        AIMessageChunk(content="hi", id="c1"),
    ],
    ids=["ai", "ai-blocks", "human", "system", "tool", "remove", "chunk"],
)
def test_a_saved_message_loads_back_whole(message: BaseMessage) -> None:
    back = _round_trip(message)
    assert back == message
    assert type(back) is type(message)


def test_messages_inside_lists_and_dicts_load_back_too() -> None:
    value = {"a": [{"b": HumanMessage("x", id="1")}], "c": {"d": AIMessage("y", id="2")}, "e": 1}
    assert _round_trip(value) == value


def test_any_other_object_is_still_refused() -> None:
    with pytest.raises(TypeError, match="Object of type object is not JSON serializable"):
        json.dumps(object(), default=encode)


@pytest.mark.parametrize(
    "lookalike",
    [
        {"langchain_message": 1},
        {"langchain_message": {"a": 1}},
        {"langchain_message": {"type": "ai"}},
        {"langchain_message": {"type": "ai", "data": {"content": "x"}}, "more": 1},
    ],
)
def test_a_dict_that_only_looks_saved_stays_a_dict(lookalike: dict[str, object]) -> None:
    assert _round_trip(lookalike) == lookalike


def test_a_saved_message_of_an_unknown_type_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="unexpected message type"):
        _round_trip({"langchain_message": {"type": "os.system", "data": {"content": "x"}}})


def test_what_json_can_t_hold_inside_a_message_is_saved_as_its_repr() -> None:
    when = datetime(2026, 11, 4, tzinfo=UTC)
    back = _round_trip(ToolMessage("r", tool_call_id="c", artifact=object()))
    assert isinstance(back, ToolMessage) and "object object at" in back.artifact
    back = _round_trip(AIMessage("r", additional_kwargs={"when": when}))
    assert isinstance(back, AIMessage) and back.additional_kwargs == {"when": repr(when)}


def test_response_headers_are_left_out_of_a_saved_message() -> None:
    metadata = {"model_name": "fake", "headers": {"set-cookie": "a-session-cookie"}}
    message = AIMessage("hi", response_metadata=metadata)
    assert "a-session-cookie" not in json.dumps(message, default=encode)
    assert message.response_metadata == metadata  # the live message keeps them
    back = _round_trip(message)
    assert isinstance(back, AIMessage) and back.response_metadata == {"model_name": "fake"}


def test_plain_shows_a_judge_what_each_message_says() -> None:
    value = {
        "messages": [
            HumanMessage("q", id="u1"),
            AIMessage(
                "a",
                id="run-1",
                tool_calls=[{"name": "s", "args": {"id": 7}, "id": "call_1", "type": "tool_call"}],
                usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            ),
            ToolMessage("r", tool_call_id="call_1"),
        ],
        "nested": {"m": [SystemMessage("s")]},
        "x": 1,
    }
    shown = plain(value)
    assert shown == {
        "messages": [
            {"type": "human", "content": "q"},
            {"type": "ai", "content": "a", "tool_calls": [{"name": "s", "args": {"id": 7}}]},
            {"type": "tool", "content": "r", "tool_call_id": "call_1", "status": "success"},
        ],
        "nested": {"m": [{"type": "system", "content": "s"}]},
        "x": 1,
    }
    assert json.loads(json.dumps(shown)) == shown
