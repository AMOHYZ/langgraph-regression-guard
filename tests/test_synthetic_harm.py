"""T262: synthetic harm status from committed code-check taxonomies only.

Harm status must not call or copy outcome_ok, and must not read regressed.
"""

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import cast

from examples.synthetic_agents import DEMO
from lgcov.cassette import record
from lgcov.drift_gate import HarmStatus, root_harm_statuses
from lgcov.synthetic import Case, every_case
from lgcov.synthetic_harm import (
    TAXONOMY_BY_AGENT,
    code_check_new_failures,
    harm_for_roots,
    harm_rate_counts,
)
from lgcov.synthetic_score import HarmRates, Rate
from lgcov.taxonomy import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


def test_each_suite_agent_has_a_committed_taxonomy() -> None:
    for agent, rel in TAXONOMY_BY_AGENT.items():
        path = ROOT / rel
        assert path.is_file(), agent
        tax = load_taxonomy(path)
        assert tax.kpi_specs
        assert all(s.judge == "code" for s in tax.kpi_specs)


def test_harm_apis_do_not_take_outcome_ok_or_suite_labels() -> None:
    import inspect

    src = (ROOT / "src" / "lgcov" / "synthetic_harm.py").read_text(encoding="utf-8")
    assert "outcome_ok" not in src  # never call or copy that helper by name
    for fn in (code_check_new_failures, harm_for_roots):
        params = inspect.signature(fn).parameters
        assert "regressed" not in params and "truth" not in params and "label" not in params


def test_code_checks_on_an_unchanged_demo_run_have_no_new_failures() -> None:
    base = record(DEMO.graph, {"question": "Where is my order?"}, units=True)
    specs = load_taxonomy(ROOT / TAXONOMY_BY_AGENT["demo"]).kpi_specs
    failed, judged = code_check_new_failures(base, base, specs)
    assert failed == frozenset()
    assert judged == frozenset(s.name for s in specs)


def test_harm_status_for_demo_roots_presence_checks_do_not_measure() -> None:
    """Today's demo taxonomy is same-node node_visited; those roots stay unmeasured."""
    specs = load_taxonomy(ROOT / TAXONOMY_BY_AGENT["demo"]).kpi_specs
    covered = {s.name: s.node for s in specs}
    checks = {s.name: dict(s.check) for s in specs if s.check is not None}
    statuses = root_harm_statuses(
        ("lookup_order", "respond", "plan"),
        covered,
        failed_kpis=frozenset(),
        judged_kpis=frozenset(covered),
        kpi_checks=checks,
    )
    assert statuses == {
        "lookup_order": "unmeasured",
        "respond": "unmeasured",
        "plan": "unmeasured",
    }


def test_harm_rates_score_enforced_ff_and_measured_recall() -> None:
    """Scoring may use labels; the statuses themselves were produced without them."""
    rows = cast(
        list[tuple[str, Mapping[str, HarmStatus]]],
        [
            ("benign", {"respond": "no harm seen"}),
            ("benign", {"respond": "harmed"}),
            ("regressed", {"lookup_order": "harmed"}),
            ("regressed", {"lookup_order": "no harm seen"}),
            ("regressed", {"plan": "unmeasured"}),
        ],
    )
    ff, recall, unmeas = harm_rate_counts(rows)
    found = HarmRates(Rate(*ff), Rate(*recall), Rate(*unmeas))
    assert (found.enforced_false_flag.hits, found.enforced_false_flag.of) == (1, 2)
    assert (found.measured_recall.hits, found.measured_recall.of) == (1, 2)


@cache
def _demo_benign() -> Case:
    return next(c for c in every_case(DEMO) if c.truth.label == "benign")


def test_a_benign_demo_root_is_no_harm_seen_or_unmeasured_not_harmed() -> None:
    from lgcov.cli import _shipped

    case = _demo_benign()
    roots = sorted(_shipped(case, set(getattr(DEMO.graph, "nodes", {}))).root)
    assert roots  # T262: still named root today
    statuses = harm_for_roots(case.base, case.pr, roots, ROOT / TAXONOMY_BY_AGENT["demo"])
    assert all(statuses[r] != "harmed" for r in roots), statuses


def test_all_regressed_cases_are_unmeasured_with_todays_taxonomies() -> None:
    """With today's node_visited taxonomies, every rooted regressed case is unmeasured."""
    from examples.synthetic_agents import AGENTS
    from lgcov.cli import _shipped

    rows: list[dict[str, HarmStatus]] = []
    for agent in AGENTS:
        for case in every_case(agent):
            if case.truth.label != "regressed":
                continue
            roots = sorted(_shipped(case, set(getattr(agent.graph, "nodes", {}))).root)
            assert roots, (agent.name, case.truth)
            rel = TAXONOMY_BY_AGENT[agent.name]
            statuses = harm_for_roots(case.base, case.pr, roots, ROOT / rel)
            assert all(s == "unmeasured" for s in statuses.values()), (agent.name, statuses)
            rows.append(statuses)
    assert len(rows) == 31
