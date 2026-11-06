"""T165: how a LangChain message is saved, loaded and shown to a judge."""

import json
from copy import deepcopy
from dataclasses import dataclass
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
from pydantic import BaseModel

from lgcov.messages import decode, encode, key_fields, plain


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


class _Model(BaseModel):
    text: str


@dataclass
class _Row:
    text: str


class _Bare:
    pass


class _Shown:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"<Shown {self.text} at 0x10>"


def test_key_fields_add_what_a_node_can_act_on_and_nothing_else() -> None:
    metadata = {
        "finish_reason": "length",
        "stop_reason": "max_tokens",
        "model_name": "fake",
        "headers": {"set-cookie": "a-session-cookie"},
    }
    reply = AIMessage(
        "a",
        id="run-1",
        additional_kwargs={"refusal": "no"},
        response_metadata=metadata,
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    )
    assert key_fields(reply) == {
        "type": "ai",
        "content": "a",
        "artifact": None,
        "additional_kwargs": {"refusal": "no"},
        "finish_reason": "length",
        "stop_reason": "max_tokens",
    }
    tool = ToolMessage("r", tool_call_id="c", artifact={"rows": 1})
    assert key_fields(tool)["artifact"] == {"rows": 1}


@pytest.mark.parametrize(
    "artifact",
    [{"rows": 1}, (1, 2), _Model(text="x"), _Row("x"), _Bare(), object(), {1, 2}],
    ids=["dict", "tuple", "pydantic", "dataclass", "plain-object", "object", "set"],
)
def test_a_live_message_and_its_recording_give_the_same_key_fields(artifact: object) -> None:
    # What's recorded is a deep copy, saved as JSON: what JSON can't hold becomes its repr, and a
    # default repr holds a memory address. Either way the step must still be served.
    live = ToolMessage("r", tool_call_id="c", artifact=artifact)
    recorded = _round_trip(deepcopy(live))
    assert isinstance(recorded, ToolMessage)
    assert key_fields(recorded) == key_fields(live)


def test_key_fields_cut_only_the_address_from_a_repr() -> None:
    def tool(text: str) -> ToolMessage:
        return ToolMessage("r", tool_call_id="c", artifact=_Shown(text))

    assert key_fields(tool("one")) != key_fields(tool("two"))
    assert key_fields(tool("one"))["artifact"] == "<Shown one at 0x>"


def test_key_fields_read_a_chunk_as_the_message_it_becomes() -> None:
    kwargs, metadata = {"k": 1}, {"finish_reason": "stop"}
    chunk = AIMessageChunk(
        content="hi", id="c1", additional_kwargs=kwargs, response_metadata=metadata
    )
    message = AIMessage("hi", id="m1", additional_kwargs=kwargs, response_metadata=metadata)
    assert key_fields(chunk) == key_fields(message)
