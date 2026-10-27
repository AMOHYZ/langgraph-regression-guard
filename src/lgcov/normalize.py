"""T085: mask volatile fields in node I/O before L1 compares it.

UUIDs and ISO date-times inside strings, values of well-known id keys, and any
`ignore_paths` (JSON pointers into a node's input/output; `*` matches one key or
index) are replaced with placeholders. Pure: values in, values out.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
# Date plus time (a bare date is usually meaningful, so it is kept).
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# Exact names only: business ids such as order_id can be a real regression.
VOLATILE_ID_KEYS = frozenset(
    {"id", "message_id", "request_id", "run_id", "span_id", "tool_call_id", "trace_id"}
)


def _segments(pointer: str) -> tuple[str, ...]:
    return tuple(s.replace("~1", "/").replace("~0", "~") for s in pointer.split("/")[1:])


def _ignored(path: tuple[str, ...], patterns: tuple[tuple[str, ...], ...]) -> bool:
    return any(
        len(p) == len(path) and all(want in ("*", got) for want, got in zip(p, path, strict=True))
        for p in patterns
    )


def _norm(value: object, path: tuple[str, ...], patterns: tuple[tuple[str, ...], ...]) -> object:
    if path and _ignored(path, patterns):
        return "<ignored>"
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for k, v in value.items():
            volatile = (
                k in VOLATILE_ID_KEYS and isinstance(v, str | int) and not isinstance(v, bool)
            )
            out[k] = "<id>" if volatile else _norm(v, (*path, str(k)), patterns)
        return out
    if isinstance(value, list):
        return [_norm(v, (*path, str(i)), patterns) for i, v in enumerate(value)]
    if isinstance(value, str):
        return _TIMESTAMP.sub("<timestamp>", _UUID.sub("<uuid>", value))
    return value


def normalize(value: object, ignore_paths: Sequence[str] = ()) -> object:
    """`value` with volatile fields masked; `ignore_paths` are JSON pointers like /meta/nonce."""
    return _norm(value, (), tuple(_segments(p) for p in ignore_paths))
