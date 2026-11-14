"""T119: `lgcov round init | lock | check`, in a real git checkout (no network)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from lgcov.bench_round import parse_ledger, parse_round, round_to_yaml
from lgcov.cli import lgcov_main

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "rounds" / "odr-163"
EXAMPLE = ROOT / "docs" / "benchmark" / "rounds" / "t101-163"
HEAD = "6532a4176a93cc9bb2102b3d825dcefa560c85d9"


def _git(repo: Path, *args: str) -> None:
    identity = ("-c", "user.name=test", "-c", "user.email=test@example.com")
    subprocess.run(
        ["git", "-C", str(repo), *identity, "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "state")


def _draft(repo: Path, name: str = "lgcov-round.yaml", round_id: str = "t101-163") -> Path:
    """An unfrozen copy of the #163 round, committed; a person fills in `init`'s template."""
    text = (FIXTURE / "lgcov-round.yaml").read_text(encoding="utf-8")
    path = repo / name
    draft = parse_round(text.replace("frozen: true", "frozen: false"))
    updated = tuple(c.model_copy(update={"locked_at": None}) for c in draft.candidates)
    draft = draft.model_copy(update={"id": round_id, "candidates": updated})
    path.write_text(round_to_yaml(draft), encoding="utf-8")
    _commit_all(repo)
    return path


def _rows(repo: Path) -> int:
    ledger = repo / "exposure.jsonl"
    return len(parse_ledger(ledger.read_text(encoding="utf-8"))) if ledger.exists() else 0


def test_init_writes_an_unfrozen_template(repo: Path) -> None:
    path = repo / "lgcov-round.yaml"
    assert lgcov_main(["round", "init", "--id", "r1", str(path)]) == 0
    written = parse_round(path.read_text(encoding="utf-8"))
    assert (written.id, written.frozen, written.candidates, written.cases) == ("r1", False, (), ())


def test_init_refuses_to_overwrite(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = repo / "lgcov-round.yaml"
    path.write_text("kept\n", encoding="utf-8")
    assert lgcov_main(["round", "init", "--id", "r1", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == "kept\n"
    assert "already exists" in capsys.readouterr().err


def test_lock_then_check_passes(repo: Path) -> None:
    path = _draft(repo)
    assert lgcov_main(["round", "lock", str(path)]) == 0
    locked = parse_round(path.read_text(encoding="utf-8"))
    assert locked.frozen is True
    assert all(c.locked_at for c in locked.candidates)
    assert _rows(repo) == 1
    assert lgcov_main(["round", "check", str(path)]) == 0


@pytest.mark.parametrize(
    ("change", "why"),
    [("untracked", "untracked"), ("modified", "uncommitted")],
)
def test_lock_refuses_a_dirty_tree(
    repo: Path, change: str, why: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _draft(repo)
    before = path.read_text(encoding="utf-8")
    if change == "untracked":
        (repo / "stray.txt").write_text("x\n", encoding="utf-8")
    else:
        path.write_text(f"{before}# edited\n", encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert lgcov_main(["round", "lock", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == written
    assert _rows(repo) == 0
    assert why in capsys.readouterr().err


def _draft_in_folder(repo: Path) -> Path:
    (repo / "rounds" / "r1").mkdir(parents=True)
    return _draft(repo, "rounds/r1/lgcov-round.yaml")


def test_lock_warns_about_untracked_files_outside_the_rounds_folder(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _draft_in_folder(repo)
    (repo / ".DS_Store").write_bytes(b"x")
    (repo / "docs").mkdir()
    (repo / "docs" / "implementation-status.md").write_text("x\n", encoding="utf-8")
    assert lgcov_main(["round", "lock", str(path)]) == 0
    err = capsys.readouterr().err
    assert "warning" in err
    assert ".DS_Store" in err and "docs/implementation-status.md" in err
    assert parse_round(path.read_text(encoding="utf-8")).frozen is True


def test_lock_refuses_an_untracked_file_in_a_subfolder_of_the_round(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _draft_in_folder(repo)
    (path.parent / "extra").mkdir()
    (path.parent / "extra" / "cases.yaml").write_text("x\n", encoding="utf-8")
    assert lgcov_main(["round", "lock", str(path)]) == 2
    assert "rounds/r1/extra/cases.yaml" in capsys.readouterr().err
    assert parse_round(path.read_text(encoding="utf-8")).frozen is False


def test_lock_refuses_a_tracked_change_outside_the_rounds_folder(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repo / "tracked.txt"
    tracked.write_text("a\n", encoding="utf-8")
    path = _draft_in_folder(repo)
    tracked.write_text("b\n", encoding="utf-8")
    assert lgcov_main(["round", "lock", str(path)]) == 2
    assert "tracked.txt" in capsys.readouterr().err


def test_lock_refuses_outside_a_git_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "lgcov-round.yaml"
    shutil.copy(FIXTURE / "lgcov-round.yaml", path)
    assert lgcov_main(["round", "lock", str(path)]) == 2
    assert "git checkout" in capsys.readouterr().err


def test_check_fails_on_sha_drift(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _draft(repo)
    assert lgcov_main(["round", "lock", str(path)]) == 0
    capsys.readouterr()
    path.write_text(path.read_text(encoding="utf-8").replace(HEAD, "a" * 40), encoding="utf-8")
    assert lgcov_main(["round", "check", str(path)]) == 2
    assert HEAD in capsys.readouterr().err


def test_record_appends_after_a_clean_check_and_keeps_what_was_there(repo: Path) -> None:
    path = _draft(repo)
    assert lgcov_main(["round", "lock", str(path)]) == 0
    before = (repo / "exposure.jsonl").read_text(encoding="utf-8")
    assert lgcov_main(["round", "check", str(path), "--record", "run-1"]) == 0
    after = (repo / "exposure.jsonl").read_text(encoding="utf-8")
    assert after.startswith(before)
    assert _rows(repo) == 3  # the lock row, then one per candidate x case


def test_record_writes_nothing_when_the_check_fails(repo: Path) -> None:
    path = _draft(repo)
    assert lgcov_main(["round", "lock", str(path)]) == 0
    path.write_text(path.read_text(encoding="utf-8").replace(HEAD, "a" * 40), encoding="utf-8")
    assert lgcov_main(["round", "check", str(path), "--record", "run-1"]) == 2
    assert _rows(repo) == 1


def test_a_second_round_on_a_seen_case_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _draft(repo)
    assert lgcov_main(["round", "lock", str(first)]) == 0
    assert lgcov_main(["round", "check", str(first), "--record", "run-1"]) == 0
    second = _draft(repo, "round-2.yaml", "t101-163-b")
    assert lgcov_main(["round", "lock", str(second)]) == 0
    capsys.readouterr()
    assert lgcov_main(["round", "check", str(second)]) == 2
    assert "eu-ai-act" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["lock", "check"])
def test_a_missing_or_broken_round_exits_two(repo: Path, verb: str) -> None:
    assert lgcov_main(["round", verb, str(repo / "nope.yaml")]) == 2
    broken = repo / "broken.yaml"
    broken.write_text("a: [", encoding="utf-8")
    assert lgcov_main(["round", verb, str(broken)]) == 2


def test_a_broken_ledger_exits_two(repo: Path) -> None:
    path = repo / "lgcov-round.yaml"
    shutil.copy(FIXTURE / "lgcov-round.yaml", path)
    (repo / "exposure.jsonl").write_text("not json\n", encoding="utf-8")
    assert lgcov_main(["round", "check", str(path)]) == 2


def test_the_example_round_passes() -> None:
    assert lgcov_main(["round", "check", str(EXAMPLE / "lgcov-round.yaml")]) == 0
