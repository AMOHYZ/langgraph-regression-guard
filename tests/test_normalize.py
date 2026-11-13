"""T085: mask volatile fields (UUIDs, ISO timestamps, ids, ignore_paths) before comparing."""

import json
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)

from lgcov.cassette import NodeIO, diff_cassettes
from lgcov.normalize import key_view, normalize, normalize_counted


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


# T177: what a recorded step's input is matched on, which is more than what's compared.
NOTE = "run 3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-10-27T07:15:30Z"


def test_the_key_sees_an_id_a_uuid_and_a_date_time_that_comparing_masks() -> None:
    raw = {"id": "ticket-1", "note": NOTE}
    assert key_view(raw) == raw
    assert normalize(raw) == {"id": "<id>", "note": "run <uuid> at <timestamp>"}


@pytest.mark.parametrize(
    "key", ["message_id", "request_id", "run_id", "span_id", "tool_call_id", "trace_id"]
)
def test_the_key_still_masks_the_clearly_volatile_names(key: str) -> None:
    assert key_view({key: "x", "order_id": "42"}) == {key: "<id>", "order_id": "42"}


def test_the_key_masks_a_tool_call_s_id_and_nothing_else_in_it() -> None:
    # In a Send payload, say. That `id` is a tool_call_id, and rebind fits what's served to it.
    assert key_view({"call": _call({"id": "42"})}) == {
        "call": {"name": "search", "args": {"id": "42"}, "id": "<id>", "type": "tool_call"}
    }


def test_the_key_keeps_an_id_beside_any_other_type() -> None:
    raw = {"type": "order", "name": "search", "args": {}, "id": "42"}
    assert key_view(raw) == raw


def test_the_key_reads_what_a_message_says_and_what_a_node_can_act_on() -> None:
    reply = AIMessage(
        "hi",
        id="run-1",
        additional_kwargs={"refusal": "no", "tool_calls": [{"id": "call_1", "type": "function"}]},
        response_metadata={"finish_reason": "stop", "stop_reason": "end_turn", "model_name": "m"},
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    assert key_view(reply) == {
        "type": "ai",
        "content": "hi",
        "artifact": None,
        "additional_kwargs": {"refusal": "no", "tool_calls": [{"id": "<id>", "type": "function"}]},
        "finish_reason": "stop",
        "stop_reason": "end_turn",
    }


def test_an_id_counts_in_a_tool_s_artifact_but_not_in_additional_kwargs() -> None:
    def tool(value: str) -> ToolMessage:
        return ToolMessage("r", tool_call_id="c", artifact={"id": value})

    def reply(value: str) -> AIMessage:
        return AIMessage("a", additional_kwargs={"reasoning": {"id": value}})

    assert key_view(tool("1")) != key_view(tool("2"))
    assert key_view(reply("1")) == key_view(reply("2"))


def test_mask_volatile_masks_what_comparing_masks_in_plain_state() -> None:
    raw = {"id": "ticket-1", "note": NOTE, "order_id": "42", "run_id": "r1"}
    assert key_view(raw, mask_volatile=True) == normalize(raw)


def test_mask_volatile_still_counts_a_message_s_fields_and_masks_their_text() -> None:
    def reply(reason: str, when: str) -> AIMessage:
        kwargs = {"created": f"at {when}"}
        return AIMessage("a", additional_kwargs=kwargs, response_metadata={"finish_reason": reason})

    def key(message: AIMessage) -> object:
        return key_view(message, mask_volatile=True)

    first = reply("stop", "2026-10-27T07:15:30Z")
    assert key(first) == key(reply("stop", "2027-01-01T10:00:00Z"))
    assert key(first) != key(reply("length", "2026-10-27T07:15:30Z"))


def test_ignore_paths_reach_the_key() -> None:
    raw = {"meta": {"nonce": 1}, "id": "ticket-1"}
    assert key_view(raw, ["/meta/nonce"]) == {"meta": {"nonce": "<ignored>"}, "id": "ticket-1"}
    assert key_view(raw, ["/id"]) == {"meta": {"nonce": 1}, "id": "<ignored>"}
    assert key_view(raw, ["/meta/nonce"], mask_volatile=True) == {
        "meta": {"nonce": "<ignored>"},
        "id": "<id>",
    }


@dataclass
class _Note:
    text: str
    id: str


def test_a_dataclass_compares_and_keys_as_the_cassette_saves_it() -> None:
    # Cassette.body saves a dataclass in a node's output as asdict does; a fresh run still holds
    # the object, so comparing it must read it the same way, masks included.
    value = {"notes": [_Note("at 2026-11-10T09:00:00Z", "n1")]}
    assert normalize(value) == {"notes": [{"text": "at <timestamp>", "id": "<id>"}]}
    assert key_view(value) == {"notes": [{"text": "at 2026-11-10T09:00:00Z", "id": "n1"}]}


# T213: a provider's response ids written into text, as when a message list is turned into a
# string. The ids below are made up.
_HEX = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f607182"
_IDS = [
    f"rs_{_HEX}",
    f"fc_{_HEX}",
    f"resp_{_HEX}",
    "call_Ab3dEf6hIj9kLm2nOp5qRs8t",
    "chatcmpl-Ab3dEf6hIj9kLm2nOp5qRs8tUv",
    "msg_01Ab3dEf6hIj9kLm2nOp5qRs",
    "toolu_01Ab3dEf6hIj9kLm2nOp5qRs",
    "tooluse_Ab3dEf6hIj9kLm2nOp5q",
    "req_011Ab3dEf6hIj9kLm2nOp5qR",
]


def _notes(ids: list[str]) -> str:
    return " ".join(f"[{{'id': '{i}', 'type': 'reasoning'}}]" for i in ids)


def test_provider_ids_inside_text_are_masked_and_counted() -> None:
    other = [i[:-4] + "9Z9Z" for i in _IDS]
    masked, count = normalize_counted(_notes(_IDS))
    assert masked == normalize(_notes(other)) == _notes(["<id>"] * len(_IDS))
    assert count == len(_IDS)


@pytest.mark.parametrize(
    "kept",
    [
        "call_id",
        "call_me_later",
        "order_12345",
        "msg_count",
        "msg_count_of_all_messages_total",  # a long snake_case name
        "msg_countofallmessagesintotalsofar",  # a long lowercase word after a prefix
        "xmsg_01Ab3dEf6hIj9kLm2nOp5qRs",  # a prefix in the middle of a word
        "my_req_011Ab3dEf6hIj9kLm2nOp5qR",
        "msg_01Ab3dEf6hIj9kLm2nOp5qRs_total",  # the tail runs into an underscore
    ],
)
def test_text_that_only_looks_like_a_provider_id_is_kept(kept: str) -> None:
    assert normalize_counted(f"see {kept} here") == (f"see {kept} here", 0)


def test_the_replay_key_keeps_provider_ids_unless_told_to_mask_them() -> None:
    raw = {"notes": _notes(_IDS[:2])}
    assert key_view(raw) == raw
    assert key_view(raw, mask_volatile=True) == {"notes": _notes(["<id>"] * 2)}


def test_an_encrypted_reasoning_blob_in_text_still_differs() -> None:
    # A limit, pinned: it is no id. Masking a volatile key's value inside text is T213b.
    a, b = (
        f"[{{'id': '{_IDS[0]}', 'encrypted_content': '{blob}'}}]" for blob in ("gAAAx", "gAAAy")
    )
    assert normalize(a) != normalize(b)
