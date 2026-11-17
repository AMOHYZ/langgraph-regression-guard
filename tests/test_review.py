import json
from pathlib import Path

import pytest

from lgcov.review import Changes, ReviewDecision, apply_decision, reject_changes, unwritable

BASE = {
    "answer": {"input": {"q": "hi"}, "output": {"a": "v1:hi"}},
    "fmt": {"input": {"a": "v1:hi"}, "output": {"out": "V1:HI"}},
}
NEW = {
    "answer": {"input": {"q": "hi"}, "output": {"a": "v2:hi"}},
    "fmt": {"input": {"a": "v2:hi"}, "output": {"out": "V2:HI"}},
}


def test_decision_json_round_trip() -> None:
    d = ReviewDecision(node="answer", action="accept", reviewer="ahmed", reason="intended")
    assert ReviewDecision.from_json(d.to_json()) == d


@pytest.mark.parametrize(
    "raw",
    [
        '{"node": "answer", "action": "merge", "reviewer": "ahmed"}',
        '{"node": "answer", "action": "override", "reviewer": "ahmed"}',
        '{"action": "accept", "reviewer": "ahmed"}',
    ],
)
def test_invalid_decisions_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        ReviewDecision.from_json(raw)


def test_accept_re_records_only_that_node(tmp_path: Path) -> None:
    d = ReviewDecision(node="answer", action="accept", reviewer="ahmed")
    out = apply_decision(d, BASE, NEW, tmp_path / "cal.jsonl")
    assert out["answer"] == NEW["answer"]
    assert out["fmt"] == BASE["fmt"]
    assert BASE["answer"]["output"] == {"a": "v1:hi"}  # input not mutated


def test_reject_keeps_baseline(tmp_path: Path) -> None:
    d = ReviewDecision(node="answer", action="reject", reviewer="ahmed")
    assert apply_decision(d, BASE, NEW, tmp_path / "cal.jsonl") == BASE


def test_override_saves_labelled_example(tmp_path: Path) -> None:
    cal = tmp_path / "cal.jsonl"
    d = ReviewDecision(
        node="answer", action="override", reviewer="ahmed", label=1.0, reason="judge too strict"
    )
    assert apply_decision(d, BASE, NEW, cal) == BASE
    row = json.loads(cal.read_text().strip())
    assert row["human_label"] == 1.0 and row["output"] == {"a": "v2:hi"}


def test_override_records_the_judge_it_corrects(tmp_path: Path) -> None:
    # T063: what `lgcov calibrate` needs to count an override as a label; T102 builds on it.
    cal = tmp_path / "cal.jsonl"
    d = ReviewDecision(
        node="answer",
        action="override",
        reviewer="ahmed",
        label=1.0,
        kpi="grounded",
        judge_label="fail",
        case_id="run-0/answer",
    )
    assert ReviewDecision.from_json(d.to_json()) == d
    apply_decision(d, BASE, NEW, cal)
    row = json.loads(cal.read_text().strip())
    assert (row["kpi"], row["judge_label"], row["case_id"]) == ("grounded", "fail", "run-0/answer")


# T186: `reject` makes the PR's cassette_dir the base's again (docs/review.md).
def test_reject_writes_what_the_base_has_and_removes_what_it_doesn_t() -> None:
    pr = {"baseline.json": b"accepted", "accept.md": b"table", "same.json": b"x"}
    base = {"baseline.json": b"recorded", "notes.txt": b"put back", "same.json": b"x"}
    assert reject_changes(pr, base) == Changes(
        write={"baseline.json": b"recorded", "notes.txt": b"put back"}, remove=("accept.md",)
    )


def test_reject_of_a_folder_that_matches_the_base_s_does_nothing() -> None:
    files = {"baseline.json": b"recorded", "sub/more.json": b"y"}
    changes = reject_changes(files, dict(files))
    assert changes == Changes(write={}, remove=())
    assert changes.empty


def test_reject_puts_back_a_folder_the_pr_deleted() -> None:
    assert reject_changes({}, {"baseline.json": b"recorded"}) == Changes(
        write={"baseline.json": b"recorded"}, remove=()
    )


@pytest.mark.parametrize(
    ("path", "why"),
    [
        (".github/cassettes/baseline.json", "is in a .github folder"),
        ("examples/.github/baseline.json", "is in a .github folder"),
        ("cassettes/action.yml", "is an action file"),
        ("cassettes/deep/action.yaml", "is an action file"),
        ("../cassettes/baseline.json", "isn't a path inside the repo"),
        ("cassettes/../../x", "isn't a path inside the repo"),
        ("/etc/cassettes/baseline.json", "isn't a path inside the repo"),
        (".", "isn't a path inside the repo"),
        ("", "isn't a path inside the repo"),
    ],
)
def test_no_review_command_writes_outside_the_repo_or_to_github_s_files(
    path: str, why: str
) -> None:
    assert why in (unwritable(path) or "")


@pytest.mark.parametrize(
    "path",
    [
        "cassettes/baseline.json",
        "examples/demo_agent/cassettes/accept.md",
        ".githubx/baseline.json",
        "cassettes/action.yml.bak",
        "./cassettes/baseline.json",
    ],
)
def test_any_other_path_in_the_repo_is_writable(path: str) -> None:
    assert unwritable(path) is None
