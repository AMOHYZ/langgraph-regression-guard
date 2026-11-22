"""T264: score enforced-FF and measured recall split by taxonomy check tag."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from lgcov.drift_gate import HarmStatus
from lgcov.synthetic_harm import (
    CHECK_TAGS,
    TAXONOMY_BY_AGENT,
    harm_for_roots,
    harm_rate_counts,
)
from lgcov.synthetic_score import HarmRates, Rate, Report, report_json
from lgcov.taxonomy import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


def test_check_tags_are_requirement_and_format() -> None:
    assert CHECK_TAGS == ("requirement", "format")


def test_suite_taxonomy_kpis_load_with_requirement_or_format_tag() -> None:
    """Each synthetic content KPI carries a first-class tag (requirement or format)."""
    for agent, rel in TAXONOMY_BY_AGENT.items():
        tax = load_taxonomy(ROOT / rel)
        assert tax.kpi_specs, agent
        for spec in tax.kpi_specs:
            assert spec.tag in CHECK_TAGS, (agent, spec.name, spec.tag)
        tags = {s.tag for s in tax.kpi_specs}
        assert tags == set(CHECK_TAGS), agent


def test_kpi_tag_field_parses_and_rejects_unknown(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text(
        """\
version: 1
system: sandbox
failure_modes:
  - id: mode_a
    description: a failure
kpis:
  - name: req_kpi
    failure_mode: mode_a
    node: respond
    judge: fake
    tag: requirement
    description: one line
    rubric: ok?
  - name: fmt_kpi
    failure_mode: mode_a
    node: respond
    judge: fake
    tag: format
    description: one line
    rubric: ok?
""",
        encoding="utf-8",
    )
    tax = load_taxonomy(good)
    assert [s.tag for s in tax.kpi_specs] == ["requirement", "format"]

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """\
version: 1
system: sandbox
failure_modes:
  - id: mode_a
    description: a failure
kpis:
  - name: weird
    failure_mode: mode_a
    node: respond
    judge: fake
    tag: style
    description: one line
    rubric: ok?
""",
        encoding="utf-8",
    )
    try:
        load_taxonomy(bad)
    except ValueError as exc:
        assert "tag" in str(exc) and "style" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown tag")


def test_harm_for_roots_only_tag_uses_that_tags_checks_only() -> None:
    """A root is harmed under a tag only when a covering check of that tag newly fails."""
    from examples.synthetic_agents import DEMO
    from lgcov.cli import _shipped
    from lgcov.synthetic import every_case

    case = next(c for c in every_case(DEMO) if c.truth.label == "regressed")
    roots = sorted(_shipped(case, set(getattr(DEMO.graph, "nodes", {}))).root)
    assert roots
    tax = ROOT / TAXONOMY_BY_AGENT["demo"]
    overall = harm_for_roots(case.base, case.pr, roots, tax)
    req = harm_for_roots(case.base, case.pr, roots, tax, only_tag="requirement")
    fmt = harm_for_roots(case.base, case.pr, roots, tax, only_tag="format")
    # Slices are independent: each uses only its tag's KPIs as covering/failing.
    assert set(req) == set(overall) == set(fmt) == set(roots)
    assert overall and req and fmt


def test_harm_rates_split_by_tag_from_precomputed_statuses() -> None:
    """enforced-FF / measured-recall / unmeasured can be scored per tag independently."""
    rows_req = cast(
        list[tuple[str, Mapping[str, HarmStatus]]],
        [
            ("benign", {"respond": "harmed"}),
            ("benign", {"respond": "no harm seen"}),
            ("regressed", {"respond": "harmed"}),
            ("regressed", {"respond": "unmeasured"}),
        ],
    )
    rows_fmt = cast(
        list[tuple[str, Mapping[str, HarmStatus]]],
        [
            ("benign", {"respond": "no harm seen"}),
            ("benign", {"respond": "no harm seen"}),
            ("regressed", {"respond": "no harm seen"}),
            ("regressed", {"respond": "harmed"}),
        ],
    )
    ff_r, rec_r, un_r = harm_rate_counts(rows_req)
    ff_f, rec_f, un_f = harm_rate_counts(rows_fmt)
    by_tag = {
        "requirement": HarmRates(Rate(*ff_r), Rate(*rec_r), Rate(*un_r)),
        "format": HarmRates(Rate(*ff_f), Rate(*rec_f), Rate(*un_f)),
    }
    overall = HarmRates(Rate(1, 2), Rate(1, 1), Rate(1, 4), by_tag=by_tag)
    assert overall.by_tag["requirement"].enforced_false_flag.hits == 1
    assert overall.by_tag["requirement"].measured_recall == Rate(1, 1)
    assert overall.by_tag["format"].enforced_false_flag.hits == 0
    assert overall.by_tag["format"].measured_recall == Rate(1, 2)


def test_report_json_and_section_include_harm_by_tag() -> None:
    from lgcov.synthetic_score import _harm_by_tag_phrase

    by_tag = {
        "requirement": HarmRates(Rate(1, 4), Rate(2, 3), Rate(1, 5)),
        "format": HarmRates(Rate(0, 4), Rate(1, 2), Rate(2, 5)),
    }
    harm = HarmRates(Rate(1, 4), Rate(3, 4), Rate(1, 5), by_tag=by_tag)
    report = Report(
        "2026-01-01",
        0,
        0,
        0,
        0,
        {},
        (),
        (),
        Rate(0, 0),
        Rate(0, 0),
        (),
        harm=harm,
    )
    blob = report_json(report)
    assert '"requirement"' in blob and '"format"' in blob
    assert '"by_tag"' in blob
    phrase = _harm_by_tag_phrase(harm)
    assert "requirement" in phrase and "format" in phrase
    assert "1 of 4" in phrase and "0 of 4" in phrase
