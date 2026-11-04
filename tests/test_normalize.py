"""T085: mask volatile fields (UUIDs, ISO timestamps, ids, ignore_paths) before comparing."""

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)

from lgcov.cassette import NodeIO, diff_cassettes
from lgcov.normalize import normalize, normalize_counted


def test_uuids_and_timestamps_are_masked_inside_text() -> None:
    raw = "run 3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-10-27T07:15:30.123456+00:00 ok"
    assert normalize(raw) == "run <uuid> at <timestamp> ok"


def test_plain_dates_and_other_text_are_kept() -> None:
    assert normalize("shipped 2026-10-27, order #42") == "shipped 2026-10-27, order #42"


def test_volatile_id_keys_are_masked_but_other_keys_are_not() -> None:
    raw = {"id": "msg_01ABC", "tool_call_id": 7, "order_id": "42", "needs_tool": True}
    assert normalize(raw) == {
        "id": "<id>",
        "tool_call_id": "<id>",
        "order_id": "42",
        "needs_tool": True,
    }


def test_ignore_paths_are_json_pointers_with_wildcards() -> None:
    raw = {"meta": {"nonce": 0.42, "a/b": 1}, "items": [{"seed": 1, "x": 2}, {"seed": 3, "x": 4}]}
    out = normalize(raw, ["/meta/nonce", "/meta/a~1b", "/items/*/seed"])
    assert out == {
        "meta": {"nonce": "<ignored>", "a/b": "<ignored>"},
        "items": [{"seed": "<ignored>", "x": 2}, {"seed": "<ignored>", "x": 4}],
    }


def test_timestamp_only_difference_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "hi at 2026-01-01T00:00:00Z"})}
    new = {"n": NodeIO(input={}, output={"msg": "hi at 2026-06-15T12:30:00+00:00"})}
    assert diff_cassettes(base, new) == {}


def test_uuid_only_difference_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"id": "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"})}
    new = {"n": NodeIO(input={}, output={"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})}
    assert diff_cassettes(base, new) == {}


def test_real_content_change_is_still_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "hello"})}
    new = {"n": NodeIO(input={}, output={"msg": "goodbye"})}
    assert diff_cassettes(base, new) == {"n": "root"}


def test_ignore_paths_field_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "ok", "nonce": 0.1})}
    new = {"n": NodeIO(input={}, output={"msg": "ok", "nonce": 0.9})}
    assert diff_cassettes(base, new, ["/nonce"]) == {}
    assert diff_cassettes(base, new) == {"n": "root"}


def test_normalize_counted_reports_replacements() -> None:
    raw = {
        "id": "x",
        "msg": "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-01-01T00:00:00Z",
        "meta": {"nonce": 1},
    }
    out, n = normalize_counted(raw, ["/meta/nonce"])
    assert out == {
        "id": "<id>",
        "msg": "<uuid> at <timestamp>",
        "meta": {"nonce": "<ignored>"},
    }
    assert n == 4  # id + uuid + timestamp + ignored


def _call(args: dict[str, Any], call_id: str = "call_1") -> ToolCall:
    return {"name": "search", "args": args, "id": call_id, "type": "tool_call"}


def _reply(text: str, n: int, **fields: Any) -> AIMessage:
    # T165: as a chat model answers: a new id and new metadata on every call.
    return AIMessage(
        text,
        id=f"run-{n}",
        response_metadata={"model_name": "fake", "created": n},
        usage_metadata={"input_tokens": n, "output_tokens": n, "total_tokens": 2 * n},
        additional_kwargs={"usage": {"prompt_tokens": n}},
        **fields,
    )


def test_a_message_compares_by_what_it_says() -> None:
    assert normalize(_reply("hi", 1, tool_calls=[_call({"q": "x"})])) == {
        "type": "ai",
        "content": "hi",
        "tool_calls": [{"name": "search", "args": {"q": "x"}}],
    }
    assert normalize(HumanMessage("q", id="u1")) == {"type": "human", "content": "q"}
    assert normalize(SystemMessage("s", name="rules")) == {
        "type": "system",
        "content": "s",
        "name": "rules",
    }


def test_a_tool_message_keeps_its_status_and_masks_its_call_id() -> None:
    message = ToolMessage("r", tool_call_id="call_1", name="search", id="t1", status="error")
    assert normalize(message) == {
        "type": "tool",
        "content": "r",
        "name": "search",
        "tool_call_id": "<id>",
        "status": "error",
    }


def test_what_a_model_call_changes_is_not_compared() -> None:
    first = normalize({"messages": [_reply("hi", 1, tool_calls=[_call({"q": "x"}, "call_1")])]})
    second = normalize({"messages": [_reply("hi", 2, tool_calls=[_call({"q": "x"}, "call_2")])]})
    assert first == second
    assert json.loads(json.dumps(first)) == first  # plain JSON, so replay can compare it


def test_a_message_s_content_and_tool_args_are_compared() -> None:
    reply = _reply("hi", 1, tool_calls=[_call({"q": "x"})])
    assert normalize(reply) != normalize(_reply("bye", 1, tool_calls=[_call({"q": "x"})]))
    assert normalize(reply) != normalize(_reply("hi", 1, tool_calls=[_call({"q": "y"})]))


def test_a_tool_s_args_keep_id_like_keys_but_not_uuids() -> None:
    args = {
        "id": "42",
        "run_id": 7,
        "ref": "run 3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b",
        "nested": [{"tool_call_id": "9"}],
    }
    assert normalize(_reply("", 1, tool_calls=[_call(args)])) == {
        "type": "ai",
        "content": "",
        "tool_calls": [
            {
                "name": "search",
                "args": {
                    "id": "42",
                    "run_id": 7,
                    "ref": "run <uuid>",
                    "nested": [{"tool_call_id": "9"}],
                },
            }
        ],
    }


def test_plain_state_still_masks_id_keys_whatever_they_are_under() -> None:
    plain = {"tool_calls": [{"id": "call_1", "args": {"id": "42"}}]}
    assert normalize(plain) == {"tool_calls": [{"id": "<id>", "args": {"id": "<id>"}}]}


def test_invalid_tool_calls_are_compared_without_their_ids() -> None:
    def broken(args: str, call_id: str) -> dict[str, str]:
        return {"name": "search", "args": args, "id": call_id, "error": "bad"}

    first = normalize(_reply("", 1, invalid_tool_calls=[broken('{"q": 1', "a")]))
    assert first == {
        "type": "ai",
        "content": "",
        "invalid_tool_calls": [{"name": "search", "args": '{"q": 1'}],
    }
    assert first == normalize(_reply("", 2, invalid_tool_calls=[broken('{"q": 1', "b")]))
    assert first != normalize(_reply("", 1, invalid_tool_calls=[broken('{"q": 2', "a")]))


def test_a_chunk_compares_like_the_message_it_becomes() -> None:
    assert normalize(AIMessageChunk(content="hi", id="c1")) == normalize(AIMessage("hi", id="m1"))


def test_messages_nested_in_lists_and_dicts_go_through_the_same_encoder() -> None:
    nested = {"type": "override", "value": [{"inner": HumanMessage("x", id="u1")}]}
    assert normalize({"state": nested}) == {
        "state": {"type": "override", "value": [{"inner": {"type": "human", "content": "x"}}]}
    }


def test_ids_inside_a_message_s_content_blocks_are_masked() -> None:
    def block(block_id: str) -> AIMessage:
        return AIMessage([{"type": "tool_use", "id": block_id, "name": "s", "input": {"q": 1}}])

    assert normalize(block("toolu_1")) == normalize(block("toolu_2"))


def test_ignore_paths_reach_a_message_s_fields() -> None:
    reply = _reply("hi", 1, tool_calls=[_call({"q": "x", "nonce": "1"})])
    paths = ["/messages/*/content", "/messages/*/tool_calls/*/args/nonce"]
    assert normalize({"messages": [reply]}, paths) == {
        "messages": [
            {
                "type": "ai",
                "content": "<ignored>",
                "tool_calls": [{"name": "search", "args": {"q": "x", "nonce": "<ignored>"}}],
            }
        ]
    }


def test_diff_cassettes_sees_through_a_message_s_ids() -> None:
    question = HumanMessage("q", id="u1")

    def io(reply: AIMessage) -> dict[str, NodeIO]:
        return {"model": NodeIO(input={"messages": [question]}, output={"messages": [reply]})}

    base = io(_reply("hi", 1))
    assert diff_cassettes(base, io(_reply("hi", 2))) == {}
    assert diff_cassettes(base, io(_reply("bye", 2))) == {"model": "root"}
