"""T200: pre-judge input contract — node I/O, messages, tool_calls; bytes-at-use."""

from __future__ import annotations

import pytest

from lgcov.judge_packet import (
    JudgePacket,
    packet_bytes,
    prepare_judge_packet,
    validate_judge_packet,
)
from lgcov.judges import KPI, FakeJudge, JudgeInfraError
from lgcov.sampling import judge_samples


def _ok_packet() -> JudgePacket:
    return JudgePacket(
        node_input={"q": "hi"},
        node_output={"answer": "hello", "messages": []},
        messages=(),
        tool_calls=(),
    )


def test_validate_rejects_missing_node_io() -> None:
    bad = JudgePacket(node_input={}, node_output={}, messages=(), tool_calls=())
    # empty dicts are valid I/O; non-mapping is not — simulate via validate helpers
    assert validate_judge_packet(bad) is None
    with pytest.raises(JudgeInfraError, match="node_input"):
        prepare_judge_packet(None, {"a": 1})  # type: ignore[arg-type]
    with pytest.raises(JudgeInfraError, match="node_output"):
        prepare_judge_packet({"a": 1}, None)  # type: ignore[arg-type]


def test_validate_rejects_omitted_messages_or_tool_calls_slots() -> None:
    """Each required slot must be present on the packet (empty is OK)."""
    assert validate_judge_packet(_ok_packet()) is None
    # Construct via object.__new__ then assign incomplete — use helper that omits slots
    from lgcov.judge_packet import _require_slots

    with pytest.raises(JudgeInfraError, match="messages"):
        _require_slots({"node_input": {}, "node_output": {}, "tool_calls": ()})
    with pytest.raises(JudgeInfraError, match="tool_calls"):
        _require_slots({"node_input": {}, "node_output": {}, "messages": ()})
    with pytest.raises(JudgeInfraError, match="node_input"):
        _require_slots({"node_output": {}, "messages": (), "tool_calls": ()})


def test_bytes_at_use_detects_mutation() -> None:
    prepared = prepare_judge_packet({"q": 1}, {"a": 2})
    assert prepared.payload_bytes == packet_bytes(prepared.packet)
    # Same frozen packet → still matches at use
    prepared.at_use()
    # Tamper with a rebuilt packet's recorded bytes
    from lgcov.judge_packet import PreparedJudgment

    tampered = PreparedJudgment(prepared.packet, b"not-the-bytes")
    with pytest.raises(JudgeInfraError, match="bytes-at-use"):
        tampered.at_use()


def test_empty_messages_and_tools_do_not_block_demo_like_io() -> None:
    """Narrow contract: demos without messages/tools stay callable (not infra)."""
    kpi = KPI.binary("k", "d", "r")
    samples = judge_samples(FakeJudge(kpi, "pass"), {"question": "x"}, {"answer": "y"}, k=1)
    assert samples[0].passed is True
    assert samples[0].error == ""


def test_prepare_extracts_messages_and_tool_calls() -> None:
    from langchain_core.messages import AIMessage

    out = {
        "messages": [
            AIMessage(
                "",
                id="ai-1",
                tool_calls=[{"name": "search", "args": {}, "id": "c1"}],
            )
        ]
    }
    prepared = prepare_judge_packet({}, out)
    assert prepared.packet.tool_calls == ("search",)
    assert len(prepared.packet.messages) == 1
    prepared.at_use()
