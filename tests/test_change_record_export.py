"""T129: change record export as JSON + markdown (evidence, never a compliance claim)."""

from __future__ import annotations

from pathlib import Path

from lgcov.adoption import Approval, ChangeRecord
from lgcov.change_record_export import (
    ART_17_MAP,
    ISO_42001_MAP,
    ExportPayload,
    export_json,
    export_markdown,
)


def _record() -> ChangeRecord:
    return ChangeRecord(
        pr=42,
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        state="approved",
        history=("eligible", "approved"),
        approval=Approval(
            base_sha="b" * 40,
            candidate_sha="c" * 40,
            base_hash="hb",
            candidate_hash="hc",
            approver="ahmed",
            risk_acceptance=None,
        ),
        statistical_verdict="SHIP",
    )


def test_export_payload_includes_required_sections() -> None:
    payload = ExportPayload.from_record(
        _record(),
        diff_summary="respond prompt tweak",
        affected_nodes=("respond",),
        kpis=("answer_ok",),
    )
    assert payload.pr == 42
    assert payload.diff == "respond prompt tweak"
    assert payload.affected_nodes == ("respond",)
    assert payload.kpis == ("answer_ok",)
    assert payload.verdict == "SHIP"
    assert payload.approver == "ahmed"
    assert payload.state == "approved"


def test_json_and_markdown_round_trip_fields(tmp_path: Path) -> None:
    payload = ExportPayload.from_record(
        _record(),
        diff_summary="x",
        affected_nodes=("respond",),
        kpis=("k",),
    )
    jpath = tmp_path / "change-42.json"
    mpath = tmp_path / "change-42.md"
    export_json(jpath, payload)
    export_markdown(mpath, payload)
    text = mpath.read_text(encoding="utf-8")
    assert "PR #42" in text
    assert "respond" in text and "SHIP" in text and "ahmed" in text
    assert "evidence" in text.lower()
    assert "compliant" not in text.lower()
    assert "certif" not in text.lower()
    # Docs map cited as evidence mapping only
    assert "Art. 17" in text or "17(1)" in text
    assert ART_17_MAP and ISO_42001_MAP
    data = __import__("json").loads(jpath.read_text(encoding="utf-8"))
    assert data["state"] == "approved" and data["approver"] == "ahmed"


def test_mapping_docs_are_evidence_not_claims() -> None:
    for line in (*ART_17_MAP, *ISO_42001_MAP):
        assert "evidence" in line.lower()
        assert "compliant" not in line.lower()
        assert "guarantee" not in line.lower()
