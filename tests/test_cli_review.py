"""T186: `lgcov review reject` puts the PR's cassette_dir back to the base's, which undoes an
accept made in the PR (docs/review.md)."""

import os
from pathlib import Path

import pytest

from lgcov.cassette import Cassette, Step, runs_to_json
from lgcov.cli import main

CONFIG = "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"


def _recording(b_said: str) -> str:
    runs = [
        Cassette(
            {"q": i}, [Step("a", {"a": i}, {"q": i}, "ca"), Step("b", {"b": b_said}, {}, "cb")]
        )
        for i in (1, 2)
    ]
    return runs_to_json(runs)


RECORDED = _recording("B1")
ACCEPTED = _recording("B2")  # what accepting `b` leaves in the PR


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The PR's checkout, as the working directory, with ../base beside it. Both start equal."""
    for side in ("base", "pr"):
        _write(tmp_path / side, {"lgcov.yaml": CONFIG, "cassettes/baseline.json": RECORDED})
    monkeypatch.chdir(tmp_path / "pr")
    return tmp_path / "pr"


def _reject(*flags: str) -> int:
    return main(["review", "reject", "--base-dir", "../base", *flags])


def test_reject_makes_the_pr_s_cassettes_the_base_s(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = pr.parent / "base"
    _write(pr, {"cassettes/baseline.json": ACCEPTED, "cassettes/accept.md": "| `b` |\n"})
    _write(base, {"cassettes/notes.txt": "the PR removed this\n"})
    capsys.readouterr()
    assert _reject() == 0
    assert _files(pr / "cassettes") == _files(base / "cassettes")
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith("| `")] == [
        line for line in out if line.startswith("| `b` | `")
    ]
    assert len([line for line in out if line.startswith("| `b` | `")]) == 1
    assert "Restored: `cassettes/baseline.json`." in out
    assert "Put back: `cassettes/notes.txt`." in out
    assert "Removed: `cassettes/accept.md`." in out


def test_reject_removes_a_folder_the_pr_added_once_it_is_empty(pr: Path) -> None:
    _write(pr, {"cassettes/extra/more.json": "{}\n", "cassettes/extra/deeper/x.json": "{}\n"})
    assert _reject() == 0
    assert not (pr / "cassettes" / "extra").exists()
    assert (pr / "cassettes").is_dir()


def test_reject_restores_a_baseline_it_can_t_read_and_names_no_node(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(pr, {"cassettes/baseline.json": "not a baseline"})
    capsys.readouterr()
    assert _reject() == 0
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == RECORDED
    out = capsys.readouterr().out
    assert "No node is named: this PR's baseline.json isn't one lgcov can read." in out
    assert "| node |" not in out


def test_reject_puts_back_a_cassette_dir_the_pr_deleted(pr: Path) -> None:
    (pr / "cassettes" / "baseline.json").unlink()
    (pr / "cassettes").rmdir()
    assert _reject() == 0
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == RECORDED


def test_reject_with_nothing_to_undo_exits_one(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _reject() == 1
    assert "nothing to reject: cassettes already matches the base's" in capsys.readouterr().err
    assert _files(pr / "cassettes") == {"baseline.json": RECORDED.encode("utf-8")}


def test_reject_without_a_recording_on_the_base_exits_two(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (pr.parent / "base" / "cassettes" / "baseline.json").unlink()
    _write(pr, {"cassettes/baseline.json": ACCEPTED})
    assert _reject() == 2
    assert "the base has no cassettes/baseline.json" in capsys.readouterr().err
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == ACCEPTED


def test_reject_takes_cassette_dir_from_the_base_s_lgcov_yaml(pr: Path) -> None:
    """The gate reads cassette_dir from the base, so a PR that moves it can't move reject."""
    _write(pr, {"lgcov.yaml": CONFIG.replace("cassettes", "moved")})
    _write(pr, {"cassettes/baseline.json": ACCEPTED, "moved/baseline.json": ACCEPTED})
    assert _reject() == 0
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == RECORDED
    assert (pr / "moved" / "baseline.json").read_text(encoding="utf-8") == ACCEPTED


def test_reject_takes_cassette_dir_from_its_flag(pr: Path) -> None:
    _write(pr.parent / "base", {"other/baseline.json": RECORDED})
    _write(pr, {"other/baseline.json": ACCEPTED, "cassettes/baseline.json": ACCEPTED})
    assert _reject("--cassette-dir", "other") == 0
    assert (pr / "other" / "baseline.json").read_text(encoding="utf-8") == RECORDED
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == ACCEPTED


@pytest.mark.parametrize(
    ("cassette_dir", "why"),
    [
        (".github/cassettes", "is in a .github folder"),
        ("../cassettes", "isn't a path inside the repo"),
        (".", "isn't a path inside the repo"),
        ("/tmp/elsewhere", "outside this checkout"),
    ],
)
def test_reject_refuses_a_cassette_dir_it_must_not_write(
    pr: Path, capsys: pytest.CaptureFixture[str], cassette_dir: str, why: str
) -> None:
    _write(pr.parent / "base", {"lgcov.yaml": CONFIG.replace("cassettes", cassette_dir)})
    _write(pr, {"cassettes/baseline.json": ACCEPTED})
    before = _files(pr)
    assert _reject() == 2
    assert why in capsys.readouterr().err
    assert _files(pr) == before


@pytest.mark.parametrize("side", ["base", "pr"])
def test_reject_refuses_an_action_file_under_cassette_dir(
    pr: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    _write(pr.parent / side, {"cassettes/action.yml": "runs: {using: composite, steps: []}\n"})
    _write(pr, {"cassettes/baseline.json": ACCEPTED})
    before = _files(pr)
    assert _reject() == 2
    assert "cassettes/action.yml` is an action file" in capsys.readouterr().err
    assert _files(pr) == before


@pytest.mark.parametrize("side", ["base", "pr"])
def test_reject_refuses_a_symlink_under_cassette_dir(
    pr: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    os.symlink("/etc/hosts", pr.parent / side / "cassettes" / "linked.json")
    _write(pr, {"cassettes/baseline.json": ACCEPTED})
    assert _reject() == 2
    assert "linked.json is a symlink" in capsys.readouterr().err
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == ACCEPTED


def test_reject_refuses_a_submodule_under_cassette_dir(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(pr, {"cassettes/sub/.git": "gitdir: ../../.git/modules/sub\n"})
    _write(pr, {"cassettes/baseline.json": ACCEPTED})
    assert _reject() == 2
    assert "sub is a submodule" in capsys.readouterr().err
    assert (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8") == ACCEPTED


@pytest.mark.parametrize("argv", [["review"], ["review", "merge"]])
def test_review_without_a_command_shows_both(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "lgcov review override --kpi" in err and "lgcov review reject --base-dir" in err
