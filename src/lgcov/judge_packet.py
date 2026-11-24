"""T200: pre-judge input contract for model (and other) judges.

Required packet slots: node I/O, messages, tool_calls. Empty messages/tool_calls are
allowed so demos without LangChain messages stay measured (narrow validators — requiring
non-empty would flip green fixtures to infra). Bytes-at-use refuses a mutated packet.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from lgcov.judges import JudgeInfraError, NodeState
from lgcov.messages import plain, run_tools

_REQUIRED = ("node_input", "node_output", "messages", "tool_calls")


@dataclass(frozen=True)
class JudgePacket:
    """What a judge call is allowed to see: node I/O plus extracted messages and tool calls."""

    node_input: NodeState
    node_output: NodeState
    messages: tuple[object, ...]
    tool_calls: tuple[str, ...]


@dataclass(frozen=True)
class PreparedJudgment:
    """Packet frozen at prepare time; `at_use` checks bytes have not changed."""

    packet: JudgePacket
    payload_bytes: bytes

    def at_use(self) -> JudgePacket:
        now = packet_bytes(self.packet)
        if now != self.payload_bytes:
            raise JudgeInfraError("judge packet bytes-at-use mismatch (packet mutated before call)")
        return self.packet


def packet_bytes(packet: JudgePacket) -> bytes:
    """Canonical JSON bytes of the packet (sort_keys) for the bytes-at-use check."""
    payload = {
        "node_input": packet.node_input,
        "node_output": packet.node_output,
        "messages": list(packet.messages),
        "tool_calls": list(packet.tool_calls),
    }
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")


def _require_slots(raw: Mapping[str, object]) -> None:
    """Raise JudgeInfraError naming the first missing required slot."""
    for key in _REQUIRED:
        if key not in raw:
            raise JudgeInfraError(f"judge packet missing required slot '{key}'")


def validate_judge_packet(packet: JudgePacket) -> str | None:
    """None if the packet satisfies the contract; else a short reason."""
    try:
        _require_slots(
            {
                "node_input": packet.node_input,
                "node_output": packet.node_output,
                "messages": packet.messages,
                "tool_calls": packet.tool_calls,
            }
        )
    except JudgeInfraError as exc:
        return str(exc)
    if not isinstance(packet.node_input, dict):
        return "judge packet node_input must be a mapping"
    if not isinstance(packet.node_output, dict):
        return "judge packet node_output must be a mapping"
    if not isinstance(packet.messages, tuple):
        return "judge packet messages must be a tuple"
    if not isinstance(packet.tool_calls, tuple):
        return "judge packet tool_calls must be a tuple"
    return None


def _messages_from(value: object) -> tuple[object, ...]:
    """Plain message dicts found under a `messages` key, if any."""
    if not isinstance(value, dict):
        return ()
    raw = value.get("messages")
    if not isinstance(raw, list):
        return ()
    return tuple(plain(m) for m in raw)


def prepare_judge_packet(node_input: NodeState, node_output: NodeState) -> PreparedJudgment:
    """Build and validate the packet; freeze its bytes for at-use checking."""
    if not isinstance(node_input, dict):
        raise JudgeInfraError("judge packet node_input must be a mapping")
    if not isinstance(node_output, dict):
        raise JudgeInfraError("judge packet node_output must be a mapping")
    messages = _messages_from(node_input) + _messages_from(node_output)
    tool_calls = tuple(run_tools([node_input, node_output]))
    packet = JudgePacket(
        node_input=dict(node_input),
        node_output=dict(node_output),
        messages=messages,
        tool_calls=tool_calls,
    )
    reason = validate_judge_packet(packet)
    if reason is not None:
        raise JudgeInfraError(reason)
    return PreparedJudgment(packet, packet_bytes(packet))


def ensure_judge_packet(node_input: NodeState, node_output: NodeState) -> JudgePacket:
    """Prepare and check bytes-at-use immediately (call site for judge_samples)."""
    return prepare_judge_packet(node_input, node_output).at_use()
