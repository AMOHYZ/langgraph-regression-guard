"""T165: fit a served output to the ids this run made.

A step is served when its input matches once masked, so this run's ids can differ from the
recording's: a live model call makes new tool-call ids, and `add_messages` gives an input
message a new id each run. An output that still names the recorded ones is wrong: a tool
result answers a call this run never made, which a chat API refuses, and a RemoveMessage
names a message this run doesn't have, which `add_messages` refuses.

Only masked fields are read or changed: a message's id, its tool calls' ids, a tool_call_id,
and id keys in plain dicts. Never content, and never a tool's args. If the recorded ids don't
map one-to-one onto this run's, that's `Ambiguous`: the step mustn't be served. Pure: values
in, values out.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from lgcov.normalize import VOLATILE_ID_KEYS

_ARGS = "args"  # a tool's own data, wherever it sits


class Ambiguous(Exception):
    """The recorded ids don't map one-to-one onto this run's: wrong count, or wrong order."""


def _message_ids(m: BaseMessage) -> list[str | None]:
    ids: list[str | None] = [m.id]
    if isinstance(m, AIMessage):
        ids += [c["id"] for c in m.tool_calls]
        ids += [c["id"] for c in m.invalid_tool_calls]
    if isinstance(m, ToolMessage):
        ids.append(m.tool_call_id)
    return ids


def _ids(value: object) -> Iterator[str]:
    if isinstance(value, BaseMessage):
        yield from (i for i in _message_ids(value) if i is not None)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in VOLATILE_ID_KEYS and isinstance(v, str):
                yield v
            elif k != _ARGS:
                yield from _ids(v)
    elif isinstance(value, list):
        for v in value:
            yield from _ids(v)


def _pairs(recorded: object, current: object) -> Iterator[tuple[str, str]]:
    # (recorded id, this run's) at each place the two inputs hold one. They match once masked,
    # so they have one shape, unless ignore_paths hid a difference.
    if isinstance(recorded, BaseMessage) and isinstance(current, BaseMessage):
        old, new = _message_ids(recorded), _message_ids(current)
        if len(old) != len(new):
            raise Ambiguous(f"{len(old)} recorded ids in a message, {len(new)} in this run's")
        yield from (
            (o, n) for o, n in zip(old, new, strict=True) if o is not None and n is not None
        )
    elif isinstance(recorded, BaseMessage) or isinstance(current, BaseMessage):
        raise Ambiguous("a message on only one side")
    elif isinstance(recorded, dict) and isinstance(current, dict):
        if recorded.keys() != current.keys():
            raise Ambiguous("different keys on each side")
        for k, a in recorded.items():
            b = current[k]
            if k in VOLATILE_ID_KEYS and isinstance(a, str) and isinstance(b, str):
                yield a, b
            elif k != _ARGS:
                yield from _pairs(a, b)
    elif isinstance(recorded, list) and isinstance(current, list):
        if len(recorded) != len(current):
            raise Ambiguous(f"{len(recorded)} recorded items, {len(current)} in this run's")
        for a, b in zip(recorded, current, strict=True):
            yield from _pairs(a, b)


def _fit(one: str | None, ids: dict[str, str]) -> str | None:
    return None if one is None else ids.get(one, one)


def _swap(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, BaseMessage):
        update: dict[str, object] = {"id": _fit(value.id, ids)}
        if isinstance(value, AIMessage):
            update["tool_calls"] = [{**c, "id": _fit(c["id"], ids)} for c in value.tool_calls]
            update["invalid_tool_calls"] = [
                {**c, "id": _fit(c["id"], ids)} for c in value.invalid_tool_calls
            ]
        if isinstance(value, ToolMessage):
            update["tool_call_id"] = _fit(value.tool_call_id, ids)
        return value.model_copy(update=update)
    if isinstance(value, dict):
        return {
            k: v
            if k == _ARGS
            else _fit(v, ids)
            if k in VOLATILE_ID_KEYS and isinstance(v, str)
            else _swap(v, ids)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_swap(v, ids) for v in value]
    return value


def rebind(output: Any, recorded: object, current: object) -> Any:
    """`output`, made after input `recorded`, fitted to the ids in input `current`. An output
    that names none of the input's ids is returned as it is, whatever the input's ids do.

    Raises Ambiguous when an id it names has no single counterpart in `current`."""
    named = set(_ids(output)) & set(_ids(recorded))
    if not named:
        return output
    pairs = list(_pairs(recorded, current))
    fitted: dict[str, str] = {}
    for old in named:
        new = {n for o, n in pairs if o == old}
        if len(new) != 1:
            raise Ambiguous(f"recorded id {old} pairs with {len(new)} of this run's")
        fitted[old] = next(iter(new))
        if {o for o, n in pairs if n == fitted[old]} != {old}:
            raise Ambiguous(f"this run's id {fitted[old]} pairs with several recorded ones")
    changes = {o: n for o, n in fitted.items() if o != n}
    return _swap(output, changes) if changes else output
