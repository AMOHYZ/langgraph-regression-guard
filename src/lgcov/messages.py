"""T165: LangChain messages in node input and output.

A message is compared by what it says (`fields`) and saved whole (`encode`). What `fields`
leaves out changes on every model call, but a served message still needs it: the next node
may read a tool call's id, and `add_messages` can't rebuild a tool call without one. Pure:
values in, values out.
"""

from __future__ import annotations

import json
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


def plain(value: Any) -> Any:
    """`value` as JSON a judge can read: each message replaced by its fields."""
    if isinstance(value, BaseMessage):
        return fields(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def encode(value: object) -> object:
    # json.dumps calls this for each value it can't write itself
    if isinstance(value, BaseMessage):
        saved = message_to_dict(value)
        # include_response_headers=True puts them here; they can hold cookies, and cassettes
        # get committed.
        saved["data"]["response_metadata"].pop("headers", None)
        # What JSON can't hold, like a tool's artifact, isn't compared and must not cost the
        # recording, so it's saved as its repr.
        return {_SAVED: json.loads(json.dumps(saved, default=repr))}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def decode(data: dict[str, Any]) -> object:
    # json.loads calls this for each object it reads, innermost first. messages_from_dict
    # only builds message classes: a cassette can't name anything else.
    saved = data.get(_SAVED) if len(data) == 1 else None
    if isinstance(saved, dict) and saved.keys() == {"type", "data"}:
        return messages_from_dict([saved])[0]
    return data
