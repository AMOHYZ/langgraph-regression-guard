"""T165: LangChain messages in node input and output.

A message is compared by what it says (`fields`) and saved whole (`encode`). What `fields`
leaves out changes on every model call, but a served message still needs it: the next node
may read a tool call's id, and `add_messages` can't rebuild a tool call without one. Pure:
values in, values out.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    message_chunk_to_message,
    message_to_dict,
    messages_from_dict,
)

# A saved message is LangChain's own dict form under this key, so loading gives it back.
_SAVED = "langchain_message"
# Why the model stopped: `stop_reason` is Anthropic's name for `finish_reason`.
_STOPPED = ("finish_reason", "stop_reason")
# In a default repr, the object's memory address differs on every run.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def fields(message: BaseMessage) -> dict[str, object]:
    """What a message says. Left out: its id, its metadata, its tool calls' ids, and
    additional_kwargs, where some providers report usage. normalize masks the tool_call_id
    kept here. A chunk reads as the message it becomes."""
    m = message_chunk_to_message(message)
    out: dict[str, object] = {"type": m.type, "content": m.content}
    if m.name is not None:
        out["name"] = m.name
    if isinstance(m, AIMessage):
        if m.tool_calls:
            out["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
        if m.invalid_tool_calls:
            out["invalid_tool_calls"] = [
                {"name": c["name"], "args": c["args"]} for c in m.invalid_tool_calls
            ]
    if isinstance(m, ToolMessage):
        out["tool_call_id"] = m.tool_call_id
        out["status"] = m.status
    return out


def key_fields(message: BaseMessage) -> dict[str, object]:
    """T177: what counts when a recorded step's input is matched. `fields`, plus what a node can
    act on: a tool's artifact, additional_kwargs and why the model stopped. Read as `encode` saves
    them, so a live message and its recording give the same values."""
    data = message_to_dict(message)["data"]
    acted_on = {
        "artifact": data.get("artifact"),
        "additional_kwargs": data["additional_kwargs"],
        **{k: data["response_metadata"].get(k) for k in _STOPPED},
    }
    saved: dict[str, object] = json.loads(
        _ADDRESS.sub(" at 0x", json.dumps(acted_on, default=repr))
    )
    return fields(message) | saved


def plain(value: Any) -> Any:
    """`value` as JSON a judge can read: each message replaced by its fields."""
    if isinstance(value, BaseMessage):
        return fields(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def _asked(message: dict[object, object]) -> list[object] | None:
    """The tool calls in an AI message saved as a dict: plain, as `message_to_dict` writes it,
    or in LangChain's `lc` constructor form. None: not an AI message."""
    lc_id = message.get("id")
    if message.get("type") in ("ai", "AIMessageChunk"):
        data = message.get("data")
        body = data if isinstance(data, dict) else message
    elif message.get("type") == "constructor" and isinstance(lc_id, list):
        if lc_id[-1:] not in (["AIMessage"], ["AIMessageChunk"]):
            return None
        kwargs = message.get("kwargs")
        body = kwargs if isinstance(kwargs, dict) else {}
    else:
        return None
    calls = body.get("tool_calls")
    return calls if isinstance(calls, list) else []


def _calls(value: object) -> list[tuple[str, object, object]]:
    """Each tool call an AI message in `value` asks for, in order, as (name, id, args)."""
    if isinstance(value, BaseMessage):
        m = message_chunk_to_message(value)
        asked: list[object] = list(m.tool_calls) if isinstance(m, AIMessage) else []
    elif isinstance(value, dict):
        found = _asked(value)
        if found is None:
            return [c for v in value.values() for c in _calls(v)]
        asked = found
    elif isinstance(value, (list, tuple)):
        return [c for v in value for c in _calls(v)]
    else:
        return []
    return [
        (c["name"], c.get("id"), c.get("args"))
        for c in asked
        if isinstance(c, dict) and isinstance(c.get("name"), str)
    ]


def run_tools(outputs: Sequence[object]) -> list[str]:
    """T181: the tools a run called, in order, from its steps' outputs. A call counts once by its
    id, since a node can return messages an earlier one did; one with no id, once per step, name
    and args."""
    seen: set[object] = set()
    tools = []
    for i, output in enumerate(outputs):
        for name, call_id, args in _calls(output):
            key = call_id or (i, name, json.dumps(args, sort_keys=True, default=str))
            if key not in seen:
                seen.add(key)
                tools.append(name)
    return tools


def encode(value: object) -> object:
    # json.dumps calls this for each value it can't write itself
    if isinstance(value, BaseMessage):
        saved = message_to_dict(value)
        # include_response_headers=True puts them here; they can hold cookies, and cassettes
        # get committed.
        saved["data"]["response_metadata"].pop("headers", None)
        # What JSON can't hold, like a tool's artifact, must not cost the recording, so it's
        # saved as its repr.
        return {_SAVED: json.loads(json.dumps(saved, default=repr))}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def decode(data: dict[str, Any]) -> object:
    # json.loads calls this for each object it reads, innermost first. messages_from_dict
    # only builds message classes: a cassette can't name anything else.
    saved = data.get(_SAVED) if len(data) == 1 else None
    if isinstance(saved, dict) and saved.keys() == {"type", "data"}:
        return messages_from_dict([saved])[0]
    return data
