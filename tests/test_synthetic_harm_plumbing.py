"""T264 PR A: synthetic harm evaluates content checks and when: like CI L2."""

from __future__ import annotations

from pathlib import Path

from lgcov.cassette import Cassette, Step
from lgcov.drift_gate import root_harm_statuses
from lgcov.synthetic_harm import code_check_new_failures, harm_for_roots
from lgcov.taxonomy import KPISpec, load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


def _spec(
    name: str,
    node: str,
    check: dict[str, object],
    *,
    judge: str = "code",
) -> KPISpec:
    return KPISpec(
        name=name,
        failure_mode="m",
        node=node,
        judge=judge,
        description="d",
        rubric="r",
        check=check,
    )


def _cassette(
    question: str,
    steps: list[Step],
) -> Cassette:
    return Cassette(inputs={"question": question}, steps=steps)


def test_content_check_new_failure_when_pr_loses_text() -> None:
    """output_contains must read the named node's output, not only the trace."""
    base = _cassette(
        "Where is my order?",
        [Step("respond", {"answer": "order #42: shipped"})],
    )
    pr = _cassette(
        "Where is my order?",
        [Step("respond", {"answer": "sorry, unavailable"})],
    )
    specs = [
        _spec(
            "respond_shipped",
            "respond",
            {"type": "output_contains", "text": "shipped"},
        )
    ]
    failed, judged = code_check_new_failures(base, pr, specs)
    assert judged == frozenset({"respond_shipped"})
    assert failed == frozenset({"respond_shipped"})
    statuses = root_harm_statuses(
        ("respond",),
        {"respond_shipped": "respond"},
        failed,
        judged,
        kpi_checks={"respond_shipped": specs[0].check},
        kpi_judges={"respond_shipped": "code"},
    )
    assert statuses == {"respond": "harmed"}


def test_when_skip_never_reads_no_harm_seen() -> None:
    """A covering content check skipped by when: is unmeasured — never 'no harm seen'."""
    # Hours question: when: order → skip. Check would pass if it ran (no "shipped" needed).
    cass = _cassette(
        "What are your hours?",
        [Step("respond", {"answer": "9 to 5"})],
    )
    specs = [
        _spec(
            "order_shipped",
            "respond",
            {
                "type": "output_contains",
                "text": "shipped",
                "when": {"input_contains": "order"},
            },
        )
    ]
    failed, judged = code_check_new_failures(cass, cass, specs)
    assert failed == frozenset()
    assert judged == frozenset()  # skipped → not judged
    statuses = root_harm_statuses(
        ("respond",),
        {"order_shipped": "respond"},
        failed,
        judged,
        kpi_checks={"order_shipped": specs[0].check},
        kpi_judges={"order_shipped": "code"},
    )
    assert statuses == {"respond": "unmeasured"}
    assert statuses["respond"] != "no harm seen"


def test_fake_judge_covering_check_leaves_root_unmeasured(tmp_path: Path) -> None:
    """FakeJudge never measures a root outside tests: covering fake → unmeasured."""
    tax = tmp_path / "tax.yaml"
    tax.write_text(
        """
version: 1
system: fake_cover
failure_modes:
  - id: m
    description: m
kpis:
  - name: always_pass
    failure_mode: m
    node: respond
    judge: fake
    description: constant
    rubric: always
    check: {fail_every: "0"}
""",
        encoding="utf-8",
    )
    loaded = load_taxonomy(tax)
    assert loaded.kpi_specs[0].judge == "fake"
    base = _cassette("Where is my order?", [Step("respond", {"answer": "shipped"})])
    pr = _cassette("Where is my order?", [Step("respond", {"answer": "broken"})])
    statuses = harm_for_roots(base, pr, ("respond",), tax)
    assert statuses == {"respond": "unmeasured"}
    # Same covering set as harm_for_roots must treat judge:fake as non-observing.
    statuses2 = root_harm_statuses(
        ("respond",),
        {"always_pass": "respond"},
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"always_pass"}),
        kpi_checks={"always_pass": {"fail_every": "0"}},
        kpi_judges={"always_pass": "fake"},
    )
    assert statuses2 == {"respond": "unmeasured"}


def test_content_check_on_unit_path_sub_inner() -> None:
    """with_units exposes sub.inner so content checks can read the inner output."""
    base = _cassette(
        "Where is my order?",
        [
            Step(
                "sub",
                {"draft": "STYLE:Where is my order?"},
                units=[Step("inner", {"draft": "STYLE:Where is my order?"})],
            )
        ],
    )
    pr = _cassette(
        "Where is my order?",
        [
            Step(
                "sub",
                {"draft": "STYLE:W"},
                units=[Step("inner", {"draft": "STYLE:W"})],
            )
        ],
    )
    specs = [
        _spec(
            "inner_draft",
            "sub.inner",
            {"type": "output_contains", "text": "Where is my order?"},
        )
    ]
    failed, judged = code_check_new_failures(base, pr, specs)
    assert judged == frozenset({"inner_draft"})
    assert failed == frozenset({"inner_draft"})


def test_when_in_scope_still_judges() -> None:
    cass = _cassette(
        "Where is my order?",
        [Step("respond", {"answer": "order #42: shipped"})],
    )
    specs = [
        _spec(
            "order_shipped",
            "respond",
            {
                "type": "output_contains",
                "text": "shipped",
                "when": {"input_contains": "order"},
            },
        )
    ]
    failed, judged = code_check_new_failures(cass, cass, specs)
    assert judged == frozenset({"order_shipped"})
    assert failed == frozenset()
