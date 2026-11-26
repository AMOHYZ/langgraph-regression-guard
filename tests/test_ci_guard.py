"""T235: `lgcov ci` maps gate-round problems to exit 1 / 2; never writes exposure."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgcov.cli import lgcov_main
from lgcov.gate_round import classify_ci_guard_problems


def test_digest_and_material_are_exit_1() -> None:
    assert (
        classify_ci_guard_problems(
            ["the round file was edited after `lgcov round new`: its digest doesn't match"]
        )
        == 1
    )
    assert classify_ci_guard_problems(["gate case set: locked abc, now def"]) == 1
    assert classify_ci_guard_problems(["judge model: locked x, now y"]) == 1
    assert classify_ci_guard_problems(["KPI respond_runs margin: locked 0.05, now 0.1"]) == 1


def test_unlocked_on_base_and_ledger_are_exit_2() -> None:
    assert (
        classify_ci_guard_problems(["candidate deadbeef is not locked in round 1 (locked: cafe)"])
        == 2
    )
    assert (
        classify_ci_guard_problems(
            ["exposure history lost: the ledger has 0 entries and this round started with 3"]
        )
        == 2
    )
    assert classify_ci_guard_problems(["no candidate to check: pass --candidate"]) == 2


def test_empty_problems_mean_continue() -> None:
    assert classify_ci_guard_problems([]) is None


def test_ci_skips_when_no_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No lgcov-round.yaml → guard skipped; ci continues (may fail later for other reasons)."""
    from test_cli_guard import _project

    _project(tmp_path, monkeypatch)
    # Minimal ci will harness-error without a graph; we only assert guard didn't fire.
    code = lgcov_main(["ci", "--report", "r.md", "--harness-error", "forced"])
    assert code == 2
    err = capsys.readouterr().err
    assert "gate round" not in err


def test_ci_missing_ledger_is_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_cli_guard import _draft, _project

    head = _project(tmp_path, monkeypatch)
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    Path("exposure.json").unlink()
    code = lgcov_main(["ci", "--report", "r.md", "--harness-error", "should-not-reach"])
    assert code == 2
    assert "ledger missing" in capsys.readouterr().err
    assert not Path("exposure.json").exists()  # never wrote exposure


def test_ci_digest_mismatch_is_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_cli_guard import _edit_round, _locked

    _locked(tmp_path, monkeypatch)
    _edit_round(delta_1=0.5)  # hand edit after lock → digest mismatch
    code = lgcov_main(["ci", "--report", "r.md"])
    assert code == 1
    assert "digest" in capsys.readouterr().err
    # guard alone still exits 2
    assert lgcov_main(["guard"]) == 2


def test_ci_unlocked_candidate_on_base_is_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_cli_guard import OTHER_SHA, _draft, _project

    _project(tmp_path, monkeypatch)
    _draft(OTHER_SHA)  # locked a different SHA than HEAD
    assert lgcov_main(["round", "new"]) == 0
    code = lgcov_main(["ci", "--report", "r.md"])
    assert code == 2
    assert "is not locked" in capsys.readouterr().err
