"""T264: held-out cases are opt-in via every_held_out_case / --held-out; not in the main suite."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from examples.synthetic_agents import AGENTS, HELD_OUT, every_held_out_case
from examples.synthetic_agents.held_out import HeldOutCase
from lgcov.cli import main
from lgcov.synthetic import Case, every_case
from lgcov.synthetic_score import HarmRates, Rate, Report


def test_default_every_case_excludes_held_out() -> None:
    suite = tuple(case for agent in AGENTS for case in every_case(agent))
    assert len(suite) == 72
    held_ids = {h.id for h in HELD_OUT}
    assert held_ids
    assert not any(case.variant in held_ids for case in suite)


def test_every_held_out_case_is_reachable_and_maps_truth() -> None:
    cases = every_held_out_case()
    assert len(HELD_OUT) == 30
    assert len(cases) == len(HELD_OUT) * len(AGENTS[0].questions)
    assert all(isinstance(case, Case) for case in cases)
    assert {case.agent for case in cases} == {h.agent for h in HELD_OUT}
    assert {case.variant for case in cases} == {h.id for h in HELD_OUT}
    by_id = {h.id: h for h in HELD_OUT}
    for case in cases:
        held = by_id[case.variant]
        assert isinstance(held, HeldOutCase)
        question = str(case.base.inputs["question"])
        kind = "hours" if "hours" in question.lower() else "order"
        expect_regressed = held.truth == "harmful" and held.harm_on in ("all", kind)
        assert case.truth.regressed is expect_regressed
        if expect_regressed:
            assert case.truth.label == "regressed"
        else:
            assert case.truth.label in ("benign", "inert")
        assert case.graph is held.graph
        assert case.pr.inputs == case.base.inputs
    assert Counter(c.truth.regressed for c in cases)[True] >= 15


def _empty_report(cases: list[Case]) -> Report:
    return Report(
        "2026-01-01",
        len(cases),
        1,
        1,
        1,
        {},
        (),
        (),
        Rate(0, 0),
        Rate(0, 0),
        (),
        harm=HarmRates(Rate(0, 0), Rate(0, 0), Rate(0, 0)),
    )


def test_bench_synthetic_held_out_flag_scores_only_held_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "held.md"
    results = tmp_path / "held.json"
    seen: list[list[Case]] = []

    def fake_build_report(cases: Any, *args: Any, **kwargs: Any) -> Report:
        scored = list(cases)
        seen.append(scored)
        return _empty_report(scored)

    class _Found:
        root: frozenset[str] = frozenset()
        inconclusive: frozenset[str] = frozenset()
        one_sided: frozenset[str] = frozenset()

    monkeypatch.setattr("lgcov.cli.build_report", fake_build_report)
    monkeypatch.setattr("lgcov.cli._shipped", lambda case, nodes=None: _Found())
    monkeypatch.setattr("lgcov.cli.aa_arms", lambda *a, **k: ())
    monkeypatch.setattr("lgcov.cli.noise_rule_rates", lambda *a, **k: ())
    monkeypatch.setattr("lgcov.cli.render_section", lambda report: "## Held-out\n\nok\n")
    monkeypatch.setattr("lgcov.cli.report_json", lambda report: "{}\n")
    monkeypatch.setattr("lgcov.cli.harm_for_roots", lambda *a, **k: {})
    monkeypatch.setattr("lgcov.cli.harm_rate_counts", lambda rows: ((0, 0), (0, 0), (0, 0)))

    with patch("lgcov.cli.every_shared_case", return_value=()):
        code = main(
            ["bench", "--synthetic", "--held-out", "--out", str(out), "--json", str(results)]
        )
    assert code == 0
    assert seen and {c.variant for c in seen[0]} == {h.id for h in HELD_OUT}
    assert "## Held-out" in out.read_text(encoding="utf-8")
    assert results.read_text(encoding="utf-8").strip() == "{}"
