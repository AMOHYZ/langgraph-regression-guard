"""T157: commit-first judge mode (fakes only; no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lgcov.commit_first import (
    AgreementRecord,
    CommitFirstFakeJudge,
    aa_false_positive_rate,
    independent_contains_check,
)
from lgcov.judges import KPI
from lgcov.taxonomy import load_taxonomy, parse_taxonomy


def _kpi() -> KPI:
    return KPI.binary(
        "answer_states_order_status",
        "Does the answer state the order's status?",
        "Does the answer state the order's status?",
    )


def test_taxonomy_refuses_commit_first_without_reference_or_check() -> None:
    raw = {
        "version": 1,
        "system": "demo",
        "failure_modes": [{"id": "m", "description": "d"}],
        "kpis": [
            {
                "name": "tone",
                "failure_mode": "m",
                "node": "respond",
                "judge": "fake",
                "description": "d",
                "rubric": "r",
                "mode": "commit_first",
            }
        ],
    }
    with pytest.raises(ValueError, match=r"tone.*commit_first.*reference|check"):
        parse_taxonomy(raw)


def test_commit_first_fake_logs_agreement_and_hides_output_from_commit() -> None:
    kpi = _kpi()
    seen: list[object] = []

    def commit(node_input: dict[str, object]) -> str:
        seen.append(node_input)
        status = str(node_input.get("status", ""))
        return status or "cannot answer from this input"

    judge = CommitFirstFakeJudge(kpi, commit=commit, reference_key="status")
    node_input = {"status": "shipped", "question": "where is my order?"}
    node_output = {"answer": "Your order is shipped."}
    verdict = judge.judge(node_input, node_output)
    assert verdict.label == "pass"
    assert verdict.raw["mode"] == "commit_first"
    assert "commit_sha256" in verdict.raw
    record = AgreementRecord.from_raw(kpi.name, "run-0", "base", 0, verdict.raw)
    assert record.commit_matches_check is True
    assert record.label_matches_check is True
    assert record.check_label == "pass"
    # Step 1 must not have been handed the output.
    assert seen == [node_input]
    assert "answer" not in json.dumps(seen[0], default=str)


def test_aa_false_positive_plumbing_with_fakes_only() -> None:
    """A/A: same I/O as base and PR. Commit-first fake stays consistent; one-step can flip."""
    kpi = _kpi()
    cases = [
        ({"status": "shipped"}, {"answer": "Order shipped."}),
        ({"status": "pending"}, {"answer": "Still pending."}),
    ]
    cf = CommitFirstFakeJudge(
        kpi,
        commit=lambda inp: str(inp.get("status", "cannot answer from this input")),
        reference_key="status",
    )
    # Flaky one-step: fails every other call even when the check would pass.
    one_step_labels = ["pass", "fail", "pass", "fail"]
    idx = {"i": 0}

    def one_step(_inp: dict[str, object], _out: dict[str, object]) -> str:
        label = one_step_labels[idx["i"] % len(one_step_labels)]
        idx["i"] += 1
        return label

    cf_rate = aa_false_positive_rate(cases, judge_label=lambda i, o, _side: cf.judge(i, o).label)
    os_rate = aa_false_positive_rate(cases, judge_label=lambda i, o, _side: one_step(i, o))
    assert cf_rate.false_positives == 0
    assert cf_rate.eligible > 0
    assert os_rate.false_positives > 0
    assert independent_contains_check({"status": "shipped"}, {"answer": "Order shipped."}, "status")


def test_demo_taxonomy_may_opt_in_to_commit_first(tmp_path: Path) -> None:
    body = """\
version: 1
system: demo
failure_modes:
  - id: respond_drift
    description: d
kpis:
  - name: answer_states_order_status
    failure_mode: respond_drift
    node: respond
    judge: fake
    description: d
    rubric: Does the answer state the order's status?
    mode: commit_first
    reference: {key: status}
"""
    path = tmp_path / "t.yaml"
    path.write_text(body, encoding="utf-8")
    tax = load_taxonomy(path)
    spec = tax.kpi_specs[0]
    assert spec.mode == "commit_first"
    assert spec.reference == {"key": "status"}
