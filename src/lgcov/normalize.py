"""T085: mask volatile fields in node I/O before L1 compares it.

UUIDs and ISO date-times inside strings, values of well-known id keys, and any
`ignore_paths` (JSON pointers into a node's input/output; `*` matches one key or
index) are replaced with placeholders. T165: a LangChain message is compared by what it
says (`lgcov.messages.fields`). T177: `key_view` is what a recorded step's input is matched on;
it masks less, and reads more of a message. Pure: values in, values out.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from langchain_core.messages import BaseMessage

from lgcov.messages import fields, key_fields

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
# Date plus time (a bare date is usually meaningful, so it is kept).
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# Exact names only: business ids such as order_id can be a real regression.
VOLATILE_ID_KEYS = frozenset(
    {"id", "message_id", "request_id", "run_id", "span_id", "tool_call_id", "trace_id"}
)
# T165: a message's tool calls. Their args are the tool's own data, so an `id` there is real.
_TOOL_CALLS = frozenset({"tool_calls", "invalid_tool_calls"})


@dataclass(frozen=True)
class _Mask:
    ids: frozenset[str]  # keys whose values are hidden, at any depth
    in_text: bool  # hide UUIDs and date-times inside strings
    read: Callable[[BaseMessage], dict[str, object]]  # what counts in a message


_COMPARED = _Mask(VOLATILE_ID_KEYS, True, fields)
# T177: a changed `id`, UUID or date-time can change what a node does, so the key sees them.
_KEY = _Mask(VOLATILE_ID_KEYS - {"id"}, False, key_fields)
_KEY_MASKED = _Mask(VOLATILE_ID_KEYS, True, key_fields)


def _segments(pointer: str) -> tuple[str, ...]:
    return tuple(s.replace("~1", "/").replace("~0", "~") for s in pointer.split("/")[1:])


def _ignored(path: tuple[str, ...], patterns: tuple[tuple[str, ...], ...]) -> bool:
    return any(
        len(p) == len(path) and all(want in ("*", got) for want, got in zip(p, path, strict=True))
        for p in patterns
    )


def _norm(
    value: object,
    path: tuple[str, ...],
    patterns: tuple[tuple[str, ...], ...],
    counts: list[int],
    mask: _Mask,
    keep_ids: bool = False,
) -> object:
    if path and _ignored(path, patterns):
        counts[0] += 1
        return "<ignored>"
    in_message = isinstance(value, BaseMessage)
    if isinstance(value, BaseMessage):
        value = mask.read(value)
    if isinstance(value, dict):
        out: dict[object, object] = {}
        # A LangChain ToolCall in plain state, as in a Send payload: its `id` is a tool_call_id.
        ids = mask.ids | {"id"} if value.get("type") == "tool_call" else mask.ids
        for k, v in value.items():
            volatile = (
                not keep_ids and k in ids and isinstance(v, str | int) and not isinstance(v, bool)
            )
            if volatile:
                counts[0] += 1
                out[k] = "<id>"
            else:
                keep = keep_ids or (in_message and k in _TOOL_CALLS)
                # An `id` in additional_kwargs is a provider's, like the message's own.
                provider = in_message and k == "additional_kwargs"
                inner = replace(mask, ids=VOLATILE_ID_KEYS) if provider else mask
                out[k] = _norm(v, (*path, str(k)), patterns, counts, inner, keep)
        return out
    if isinstance(value, list):
        return [
            _norm(v, (*path, str(i)), patterns, counts, mask, keep_ids) for i, v in enumerate(value)
        ]
    if isinstance(value, str) and mask.in_text:
        no_uuid, n_uuid = _UUID.subn("<uuid>", value)
        masked, n_ts = _TIMESTAMP.subn("<timestamp>", no_uuid)
        counts[0] += n_uuid + n_ts
        return masked
    return value


def normalize_counted(value: object, ignore_paths: Sequence[str] = ()) -> tuple[object, int]:
    """Like normalize, plus how many leaf values were replaced."""
    counts = [0]
    out = _norm(value, (), tuple(_segments(p) for p in ignore_paths), counts, _COMPARED)
    return out, counts[0]


def normalize(value: object, ignore_paths: Sequence[str] = ()) -> object:
    """`value` with volatile fields masked; `ignore_paths` are JSON pointers like /meta/nonce."""
    return normalize_counted(value, ignore_paths)[0]


def key_view(
    value: object, ignore_paths: Sequence[str] = (), mask_volatile: bool = False
) -> object:
    """`value` as a recorded step's input is matched. `mask_volatile`, from lgcov.yaml's
    `mask_volatile_in_key`, also hides `id`s, UUIDs and date-times in text, as comparing does."""
    mask = _KEY_MASKED if mask_volatile else _KEY
    return _norm(value, (), tuple(_segments(p) for p in ignore_paths), [0], mask)
