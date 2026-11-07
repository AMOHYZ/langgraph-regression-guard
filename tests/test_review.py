import json
from pathlib import Path

import pytest

from lgcov.review import ReviewDecision, apply_decision

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
