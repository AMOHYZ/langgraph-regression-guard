"""T264: content checks that can measure a root without a model."""

from __future__ import annotations

from lgcov.code_judges import OutputContains, OutputMatches, check_in_scope, code_judge
from lgcov.drift_gate import check_observes_root, root_harm_statuses
from lgcov.judges import KPI
from lgcov.taxonomy import parse_taxonomy

KPI_ = KPI.binary("k", "one line", "content check")


def test_output_contains_passes_when_text_in_output() -> None:
    out = {"answer": "Your order has shipped."}
    assert OutputContains(KPI_, "shipped").judge({}, out).label == "pass"
    assert OutputContains(KPI_, "shipped").judge({}, {"answer": "pending"}).label == "fail"


def test_output_matches_uses_regex_on_output() -> None:
    out = {"answer": "Status: SHIPPED"}
    assert OutputMatches(KPI_, r"Status:\s+SHIPPED").judge({}, out).label == "pass"
    assert OutputMatches(KPI_, r"Status:\s+SHIPPED").judge({}, {"answer": "Status: x"}).label == (
        "fail"
    )


def test_output_contains_measures_named_root() -> None:
    check = {"type": "output_contains", "text": "shipped"}
    assert check_observes_root("respond", check) is True
    assert check_observes_root("classify", check) is True  # kind observes; covering is by KPI.node


def test_fake_judge_never_measures() -> None:
    assert check_observes_root("respond", None, judge="fake") is False
    assert check_observes_root("respond", {"type": "fake"}, judge="fake") is False
    assert check_observes_root("respond", None, judge="llm") is True
    assert check_observes_root("respond", None, judge="jev") is True


def test_when_input_contains_scopes_check() -> None:
    check = {
        "type": "output_contains",
        "text": "shipped",
        "when": {"input_contains": "order"},
    }
    assert check_in_scope(check, {"question": "where is my order?"}) is True
    assert check_in_scope(check, {"question": "what are your hours?"}) is False


def test_root_whose_only_check_is_skipped_is_unmeasured() -> None:
    """A covering content check that is out of scope for every judged sample → unmeasured."""
    statuses = root_harm_statuses(
        ("respond",),
        {"order_status": "respond"},
        failed_kpis=frozenset(),
        judged_kpis=frozenset(),  # skipped: never judged
        kpi_checks={"order_status": {"type": "output_contains", "text": "shipped"}},
        kpi_judges={"order_status": "code"},
    )
    assert statuses == {"respond": "unmeasured"}


def test_several_samples_one_in_scope_measures() -> None:
    """If any sample runs an observing check, the root is measured from those samples."""
    # One sample judged and passed → no harm seen; skipped samples do not count as measuring alone.
    statuses = root_harm_statuses(
        ("respond",),
        {"order_status": "respond"},
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"order_status"}),
        kpi_checks={"order_status": {"type": "output_contains", "text": "shipped"}},
        kpi_judges={"order_status": "code"},
    )
    assert statuses == {"respond": "no harm seen"}
    harmed = root_harm_statuses(
        ("respond",),
        {"order_status": "respond"},
        failed_kpis=frozenset({"order_status"}),
        judged_kpis=frozenset({"order_status"}),
        kpi_checks={"order_status": {"type": "output_contains", "text": "shipped"}},
        kpi_judges={"order_status": "code"},
    )
    assert harmed == {"respond": "harmed"}


def test_taxonomy_preserves_when_and_builds_output_check() -> None:
    tax = parse_taxonomy(
        {
            "version": 1,
            "system": "demo",
            "failure_modes": [{"id": "lost", "description": "lost status"}],
            "kpis": [
                {
                    "name": "respond_shipped",
                    "failure_mode": "lost",
                    "node": "respond",
                    "judge": "code",
                    "description": "mentions shipped",
                    "rubric": "shipped?",
                    "check": {
                        "type": "output_contains",
                        "text": "shipped",
                        "when": {"input_contains": "order"},
                    },
                }
            ],
        }
    )
    spec = tax.kpi_specs[0]
    assert spec.check is not None
    assert spec.check["type"] == "output_contains"
    assert spec.check["text"] == "shipped"
    assert spec.check["when"] == {"input_contains": "order"}
    judge = code_judge(spec.to_kpi(), spec.check)
    assert isinstance(judge, OutputContains)


def test_output_matches_pattern_comes_from_check_mapping() -> None:
    """Pattern is whatever the (base-ref) taxonomy check carries — not invented at judge time."""
    judge = code_judge(KPI_, {"type": "output_matches", "pattern": r"ship+ed"})
    assert isinstance(judge, OutputMatches)
    assert judge.pattern == r"ship+ed"


def test_subgraph_unit_name_in_node_sequence_is_known() -> None:
    from lgcov.code_judges import unknown_names

    assert (
        unknown_names(
            {"type": "node_sequence", "nodes": "before,sub.inner,after"},
            {"before", "sub.inner", "after"},
            {"before", "sub.inner", "after"},
        )
        == ()
    )
