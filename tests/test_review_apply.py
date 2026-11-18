"""T186: `lgcov review apply`, the step with no token. It runs the command on the files fetch
read, in-process, and leaves changes.json for commit, or reply.md when there is nothing to commit.
The project is the two-node graph test_cli.py uses, recorded, changed and run by `lgcov ci`."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from lgcov.cli import _review_apply, main
from lgcov.review_apply import blob_sha, changes_from_json, job_from_json

GRAPH = """\
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    a: str
    b: str


def a(state: S) -> S:
    return {"a": "A1"}


def b(state: S) -> S:
    return {"b": "B1"}


g = StateGraph(S)
g.add_node("a", a)
g.add_node("b", b)
g.add_edge(START, "a")
g.add_edge("a", "b")
g.add_edge("b", END)
graph = g.compile()
"""
H, B = "a" * 40, "b" * 40


@dataclass(frozen=True)
class Project:
    recorded: bytes  # the base's baseline.json
    candidate: bytes  # what the candidate job wrote for the changed graph
    accepted: bytes  # the baseline once every node is accepted


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    root = tmp_path_factory.mktemp("project")
    graph, cfg, inputs = root / "graph.py", root / "lgcov.yaml", root / "inputs.json"
    graph.write_text(GRAPH, encoding="utf-8")
    cfg.write_text(
        f"graph_entrypoint: {graph}:graph\ncassette_dir: {root / 'cassettes'}\n", encoding="utf-8"
    )
    inputs.write_text("[{}, {}]", encoding="utf-8")
    assert main(["record", "--config", str(cfg), "--inputs", str(inputs)]) == 0
    graph.write_text(GRAPH.replace("A1", "A2").replace("B1", "B2"), encoding="utf-8")
    candidate = root / "candidate.json"
    report = str(root / "report.md")
    ci = ["ci", "--report", report, "--candidate-out", str(candidate), "--config", str(cfg)]
    assert main(ci) == 0
    baseline = root / "cassettes" / "baseline.json"
    recorded = baseline.read_bytes()
    assert main(["cassette", "accept", "--all-drifted", "--config", str(cfg)]) == 0
    return Project(recorded, candidate.read_bytes(), baseline.read_bytes())


def _folder(
    tmp_path: Path,
    command: dict[str, object],
    pr: dict[str, bytes],
    base: dict[str, bytes] | None = None,
    candidate: bytes | None = None,
) -> Path:
    """The folder as fetch leaves it: files under cassette_dir by their path in it."""
    out = tmp_path / "review"
    for side, files in (("pr", pr), ("base", base or {})):
        for path, data in files.items():
            target = out / side / "cassettes" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (out / "base").mkdir(parents=True, exist_ok=True)
    (out / "base" / "lgcov.yaml").write_text("cassette_dir: cassettes\n", encoding="utf-8")
    if candidate is not None:
        (out / "candidate.json").write_bytes(candidate)
    job = {
        "command": command,
        "pr": 12,
        "head_sha": H,
        "head_ref": "feat/x",
        "base_sha": B,
        "cassette_dir": "cassettes",
        "reviewer": "maintainer",
        "comment_id": 123,
        "comment_url": "https://github.com/octo-org/agent/pull/12#issuecomment-123",
    }
    (out / "event.json").write_text(json.dumps({"pr": 12, "comment_id": 123}), encoding="utf-8")
    (out / "command.json").write_text(json.dumps(job), encoding="utf-8")
    fetched = {f"cassettes/{path}": blob_sha(data) for path, data in pr.items()}
    (out / "fetched.json").write_text(json.dumps(fetched), encoding="utf-8")
    return out


def _accept(tmp_path: Path, project: Project, nodes: list[str] | None) -> Path:
    command: dict[str, object] = {"verb": "accept", "nodes": nodes}
    return _folder(tmp_path, command, {"baseline.json": project.recorded}, None, project.candidate)


def _reply(out: Path) -> str:
    return (out / "reply.md").read_text(encoding="utf-8")


def test_accept_writes_the_baseline_and_a_row_per_accepted_node(
    tmp_path: Path, project: Project
) -> None:
    out = _accept(tmp_path, project, ["b"])
    assert _review_apply(["--dir", str(out)]) == 0
    changes = changes_from_json((out / "changes.json").read_text(encoding="utf-8"))
    assert changes.write == ("cassettes/baseline.json",) and changes.remove == ()
    assert [row[0] for row in changes.rows] == ["b"]
    node, before, after = changes.rows[0]
    assert before != after
    assert not (out / "reply.md").exists()
    # accept.md is lgcov's own summary beside the baseline: never committed
    assert (out / "pr" / "cassettes" / "accept.md").exists()


def test_accept_all_takes_every_drifted_node(tmp_path: Path, project: Project) -> None:
    out = _accept(tmp_path, project, None)
    assert _review_apply(["--dir", str(out)]) == 0
    changes = changes_from_json((out / "changes.json").read_text(encoding="utf-8"))
    assert [row[0] for row in changes.rows] == ["a", "b"]
    assert (out / "pr" / "cassettes" / "baseline.json").read_bytes() == project.accepted


def test_accept_of_an_unknown_node_is_answered_and_commits_nothing(
    tmp_path: Path, project: Project
) -> None:
    out = _accept(tmp_path, project, ["zzz"])
    assert _review_apply(["--dir", str(out)]) == 0
    assert _reply(out).startswith("Not run: `lgcov cassette accept: unknown node 'zzz'")
    assert not (out / "changes.json").exists()


def test_reject_restores_the_base_s_files_and_removes_the_pr_s(
    tmp_path: Path, project: Project
) -> None:
    pr = {"baseline.json": project.accepted, "extra.json": b"{}\n"}
    out = _folder(tmp_path, {"verb": "reject"}, pr, {"baseline.json": project.recorded})
    assert _review_apply(["--dir", str(out)]) == 0
    changes = changes_from_json((out / "changes.json").read_text(encoding="utf-8"))
    assert changes.write == ("cassettes/baseline.json",)
    assert changes.remove == ("cassettes/extra.json",)
    assert [row[0] for row in changes.rows] == ["a", "b"]
    assert (out / "pr" / "cassettes" / "baseline.json").read_bytes() == project.recorded


def test_reject_with_nothing_to_undo_is_answered(tmp_path: Path, project: Project) -> None:
    files = {"baseline.json": project.recorded}
    out = _folder(tmp_path, {"verb": "reject"}, files, files)
    assert _review_apply(["--dir", str(out)]) == 0
    assert _reply(out).startswith("Nothing committed: `lgcov review reject: nothing to reject")
    assert not (out / "changes.json").exists()


def test_override_is_answered_with_the_local_command(tmp_path: Path, project: Project) -> None:
    command: dict[str, object] = {
        "verb": "override",
        "kpi": "tone",
        "case": "run-0/respond",
        "label": "fail",
    }
    out = _folder(tmp_path, command, {"baseline.json": project.recorded})
    assert _review_apply(["--dir", str(out)]) == 0
    assert "isn't built yet" in _reply(out) and "lgcov review override --kpi tone" in _reply(out)


def test_a_folder_fetch_answered_or_left_empty_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "reply.md").write_text("Not run: no.\n", encoding="utf-8")
    assert _review_apply(["--dir", str(tmp_path)]) == 0
    assert _reply(tmp_path) == "Not run: no.\n"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _review_apply(["--dir", str(empty)]) == 0
    assert list(empty.iterdir()) == []


def test_apply_prints_only_its_own_words(
    tmp_path: Path, project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    """The runner obeys `::` commands in a job's output, and node names are the PR's."""
    out = _accept(tmp_path, project, ["b"])
    capsys.readouterr()
    assert _review_apply(["--dir", str(out)]) == 0
    seen = capsys.readouterr()
    assert seen.out == "lgcov review apply: ready to commit\n" and seen.err == ""


def test_apply_runs_where_it_started(tmp_path: Path, project: Project) -> None:
    here = Path.cwd()
    _review_apply(["--dir", str(_accept(tmp_path, project, ["b"]))])
    assert Path.cwd() == here


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("head_sha", "abc"),
        ("cassette_dir", ".github/cassettes"),
        ("cassette_dir", "../out"),
        ("pr", "12"),
        ("command", {"verb": "merge"}),
    ],
)
def test_a_command_file_out_of_shape_is_refused(field: str, value: object) -> None:
    job: dict[str, object] = {
        "command": {"verb": "reject"},
        "pr": 12,
        "head_sha": H,
        "head_ref": "feat/x",
        "base_sha": B,
        "cassette_dir": "cassettes",
        "reviewer": "maintainer",
        "comment_id": 123,
        "comment_url": "u",
    }
    job_from_json(json.dumps(job))
    job[field] = value
    with pytest.raises(ValueError):
        job_from_json(json.dumps(job))
