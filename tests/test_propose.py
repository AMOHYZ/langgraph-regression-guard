"""T120 v0: `lgcov propose` JSON, prompt overlay resolution, and state rules (pure + CLI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from lgcov.propose import (
    PROMPT_ATTRS,
    ProposeKpi,
    ProposeNode,
    ProposeReport,
    declared_prompt,
    report_to_json,
    resolve_state,
    validate_against_schema,
)

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "guard-schema.json"


def test_schema_file_exists_and_validates_propose_json() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    kpi = ProposeKpi(
        name="respond_runs",
        verdict="INCONCLUSIVE",
        delta_hat=0.0,
        ci=(0.0, 0.0),
        mde=0.5,
        n_star=74,
        ni_n_star=74,
        q=2 / 74,
        p_adj=1.0,
    )
    report = ProposeReport(
        kpis=(kpi,),
        nodes=(ProposeNode("respond", "root"),),
        state="blocked",
        reasons=("root drift on respond",),
        exposure_recorded=False,
        k_gate=1,
    )
    doc = json.loads(report_to_json(report))
    validate_against_schema(doc, schema)
    assert doc["exposure_recorded"] is False
    assert doc["kpis"][0]["q"] == pytest.approx(2 / 74)
    assert "p_adj" in doc["kpis"][0]


def test_inconclusive_is_needs_human_never_eligible() -> None:
    state, reasons = resolve_state(
        statistical="INCONCLUSIVE",
        drift_roots=(),
        overlay=False,
        locked=True,
        pools_edited=False,
        unsupported=(),
        infra="",
    )
    assert state == "needs_human"
    assert state != "eligible"
    assert any("inconclusive" in r.lower() for r in reasons)


def test_overlay_never_eligible_candidate_not_locked() -> None:
    """`--node --prompt` vs `--ref` on an equivalent commit: same per-node drift labels.

    Overlay still cannot be eligible (reason candidate not locked) even if --ref would be
    eligible or blocked — drift labels should match; state may differ because of the lock cap.
    """
    blocked, _ = resolve_state(
        statistical="SHIP",
        drift_roots=("respond",),
        overlay=False,
        locked=True,
        pools_edited=False,
        unsupported=(),
        infra="",
        fail_on="root-drift",
    )
    overlay, reasons = resolve_state(
        statistical="SHIP",
        drift_roots=("respond",),
        overlay=True,
        locked=False,
        pools_edited=False,
        unsupported=(),
        infra="",
        fail_on="root-drift",
    )
    assert blocked == "blocked"
    assert overlay == "needs_human"
    assert any("candidate not locked" in r for r in reasons)


def test_no_declared_prompt_is_unsupported_without_guessing() -> None:
    graph = _prompt_graph(declare=False)
    found = declared_prompt(graph, "respond", parts=None)
    assert found is None


def test_declared_prompt_attribute_is_found_not_scraped_from_body() -> None:
    graph = _prompt_graph(declare=True)
    found = declared_prompt(graph, "respond", parts=None)
    assert found is not None
    assert found.attr in PROMPT_ATTRS
    assert found.value == "You are a concise support agent."
    assert declared_prompt(graph, "classify", parts=None) is None


def test_optimiser_proposal_sets_k_gate_one_in_report() -> None:
    from lgcov.sampling import KpiStats, gate_kpis
    from lgcov.stats import PairedResult

    def row(name: str) -> KpiStats:
        paired = PairedResult(
            n=8,
            base_rate=0.8,
            new_rate=0.7,
            delta=-0.1,
            ci=(-0.3, 0.1),
            p=0.2,
            method="mcnemar",
            rho=0.3,
            sd=0.4,
            required_n=40,
            mde=0.2,
            discordant=2,
        )
        return KpiStats(
            kpi=name,
            n=8,
            k=1,
            pass_at_k=0.7,
            pass_hat_k=0.7,
            infra=0,
            outcome="pass",
            paired=paired,
            ms_total=0.0,
            ms_mean=0.0,
            tokens_total=None,
            tokens_mean=None,
            flaky_cases=(),
        )

    gated = gate_kpis([row("a"), row("b")], k_gate_n=1)
    assert len(gated) == 2
    # Holm with m=1 leaves p unchanged; m=2 would inflate the larger p.
    assert gated[0].p_adjusted == pytest.approx(0.2)
    assert gated[1].p_adjusted == pytest.approx(0.2)


def _prompt_graph(*, declare: bool) -> Any:
    from typing import TypedDict

    class State(TypedDict, total=False):
        question: str
        answer: str

    prompt = "You are a concise support agent."

    def classify(state: State) -> State:
        return {"question": state["question"]}

    def respond(state: State) -> State:
        return {"answer": f"{prompt}:{state['question']}"}

    if declare:
        respond.system_prompt = prompt
    builder = StateGraph(State)
    builder.add_node("classify", classify)
    builder.add_node("respond", respond)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "respond")
    builder.add_edge("respond", END)
    return builder.compile()


def test_report_json_names_root_drift_rule() -> None:
    report = ProposeReport(
        kpis=(),
        nodes=(ProposeNode("respond", "root"),),
        state="blocked",
        reasons=("root drift on respond",),
        exposure_recorded=False,
    )
    doc = json.loads(report_to_json(report))
    assert doc["state"] == "blocked"
    assert any("root drift" in r and "respond" in r for r in doc["reasons"])
    assert doc["exposure_recorded"] is False


def test_fake_or_uncalibrated_block_is_capped() -> None:
    from lgcov.propose import cap_non_blocking_judges

    assert cap_non_blocking_judges("BLOCK", judge="fake") == "INCONCLUSIVE"
    assert cap_non_blocking_judges("BLOCK", judge="jev", uncalibrated=True) == "INCONCLUSIVE"
    assert cap_non_blocking_judges("BLOCK", judge="code") == "BLOCK"
