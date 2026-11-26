"""T155-B: approve binds SHAs/hashes; cassette accept needs non-stale approval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lgcov.adoption import (
    Approval,
    ChangeRecord,
    StaleApproval,
    approve_bind,
    load_change_record,
    require_fresh_approval,
    save_change_record,
)


def test_approve_binds_shas_and_hashes_and_advances_state() -> None:
    record = ChangeRecord.new(pr=7, base_sha="b" * 40, candidate_sha="c" * 40)
    approved = approve_bind(
        record,
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hbase",
        candidate_hash="hcand",
        approver="ahmed",
    )
    assert approved.state == "approved"
    assert approved.approval is not None
    assert approved.approval == Approval(
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hbase",
        candidate_hash="hcand",
        approver="ahmed",
        risk_acceptance=None,
    )
    assert approved.history[-1] == "approved"


def test_override_records_risk_acceptance_verdict_unchanged() -> None:
    record = ChangeRecord.new(pr=1, base_sha="b" * 40, candidate_sha="c" * 40)
    statistical = "BLOCK"
    approved = approve_bind(
        record,
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hb",
        candidate_hash="hc",
        approver="ahmed",
        override=True,
        note="accept risk",
        statistical_verdict=statistical,
    )
    assert approved.approval is not None
    assert approved.approval.risk_acceptance == "accept risk"
    assert approved.statistical_verdict == "BLOCK"  # unchanged


def test_stale_when_shas_or_hashes_drift() -> None:
    record = approve_bind(
        ChangeRecord.new(pr=1, base_sha="b" * 40, candidate_sha="c" * 40),
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hb",
        candidate_hash="hc",
        approver="ahmed",
    )
    with pytest.raises(StaleApproval, match="candidate"):
        require_fresh_approval(
            record,
            base_sha="b" * 40,
            candidate_sha="d" * 40,
            base_hash="hb",
            candidate_hash="hc",
        )
    with pytest.raises(StaleApproval, match="hash"):
        require_fresh_approval(
            record,
            base_sha="b" * 40,
            candidate_sha="c" * 40,
            base_hash="hb",
            candidate_hash="HCnew",
        )
    # matching → OK
    require_fresh_approval(
        record,
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hb",
        candidate_hash="hc",
    )


def test_cassette_accept_refuses_without_valid_approval(tmp_path: Path) -> None:
    from lgcov.cli import main

    (tmp_path / "cassettes").mkdir()
    # No change record → refuse before any graph work
    code = main(
        [
            "cassette",
            "accept",
            "--all-drifted",
            "--cassette-dir",
            str(tmp_path / "cassettes"),
            "--require-approval",
            "--base-sha",
            "b" * 40,
            "--candidate-sha",
            "c" * 40,
            "--base-hash",
            "hb",
            "--candidate-hash",
            "hc",
            "--change-record",
            str(tmp_path / "change-record.json"),
        ]
    )
    assert code == 2


def test_cassette_accept_advances_approved_change_record(tmp_path: Path) -> None:
    """Accept with a fresh approval re-baselines and moves eligible→approved→baseline_updated.

    Also guards against shadowing cassette.record with a local `record = ...` in accept.
    """
    from test_cli import _ab_project

    from lgcov.cli import main

    cfg, _baseline = _ab_project(tmp_path)
    path = tmp_path / "change-record.json"
    save_change_record(
        path,
        approve_bind(
            ChangeRecord.new(pr=3, base_sha="b" * 40, candidate_sha="c" * 40),
            base_sha="b" * 40,
            candidate_sha="c" * 40,
            base_hash="hb",
            candidate_hash="hc",
            approver="ahmed",
        ),
    )
    code = main(
        [
            "cassette",
            "accept",
            "--all-drifted",
            "--config",
            str(cfg),
            "--change-record",
            str(path),
            "--base-sha",
            "b" * 40,
            "--candidate-sha",
            "c" * 40,
            "--base-hash",
            "hb",
            "--candidate-hash",
            "hc",
        ]
    )
    assert code == 0
    assert load_change_record(path).state == "baseline_updated"


def test_change_record_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "change-record.json"
    record = approve_bind(
        ChangeRecord.new(pr=9, base_sha="b" * 40, candidate_sha="c" * 40),
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        base_hash="hb",
        candidate_hash="hc",
        approver="ahmed",
    )
    save_change_record(path, record)
    loaded = load_change_record(path)
    assert loaded == record
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "approved"
