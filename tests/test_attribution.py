"""T101a: the attribution benchmark's scoring and report (pure). T100a: real rows, pending
human review. T100: rows whose labels a human reviewed but that can't run yet."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from lgcov.attribution import (
    NEEDS_LIVE,
    NO_FINGERPRINT,
    NOT_PROBED,
    UNEXPLAINED,
    NodeLabels,
    Probe,
    Result,
    Score,
    classify,
    drift_exceeds_noise,
    explain_runs,
    macro,
    micro,
    parse_benchmark,
    pending_result,
    render_markdown,
    score,
)


def _result(name: str, found: set[str], expected: set[str], hard: tuple[str, ...] = ()) -> Result:
    return Result(
        name,
        found_root=tuple(sorted(found)),
        found_propagated=("c",),
        root=score(found, expected),
        propagated=score({"c"}, {"c"}),
        seconds=0.25,
        expected_root=tuple(sorted(expected)),
        expected_propagated=("c",),
        hard_cases=hard,
        notes="the PR edits the inner node",
    )


def test_score_counts_tp_fp_fn_and_derives_precision_recall_f1() -> None:
    s = score({"b", "x"}, {"b", "c"})
    assert (s.tp, s.fp, s.fn) == (1, 1, 1)
    assert (s.precision, s.recall, s.f1) == (0.5, 0.5, 0.5)
    assert (score(set(), set()).precision, score(set(), set()).f1) == (1.0, 1.0)  # nothing to find


def test_macro_and_micro_differ_when_benchmarks_differ_in_size() -> None:
    small = _result("small", {"a"}, {"a"})
    big = _result("big", {"a", "b"}, {"a", "b", "c"})
    assert micro([small, big], "root").recall == 0.75  # 3 of 4 labels over both
    assert macro([small, big], "root")[1] == pytest.approx((1 + 2 / 3) / 2)  # mean of 1 and 2/3


def test_the_table_marks_a_miss_on_a_hard_case_as_a_known_gap() -> None:
    linear = _result("linear", {"b"}, {"b"})
    sub = _result("subgraph", {"sub"}, {"sub.inner"}, hard=("subgraph",))
    md = render_markdown([linear, sub], "lgcov bench tests/fixtures/bench", "2026-10-31")
    assert "| linear | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b` | `b` | 0.25 s | ✓ |" in md
    assert "| subgraph | 0.00 / 0.00 / 0.00 |" in md and "❌ known gap (subgraph) |" in md
    assert "## Known gaps" in md
    assert "- `subgraph` (subgraph): lgcov said root `sub`, expected `sub.inner`." in md
    assert "```bash\nlgcov bench tests/fixtures/bench\n```" in md


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"cases": [{}], "expected": {"root": [], "propagated": []}}, "graph"),
        (
            {"graph": "g.py:g", "cases": [{}], "expected": {"root": [], "propagated": []}},
            "base_patch",
        ),
        (
            {
                "graph": "g.py:g",
                "base_patch": "b.py",
                "head_patch": "h.py",
                "cases": [{}],
                "expected": {"root": [], "propagated": []},
                "hard_cases": ["wormhole"],
            },
            "hard_cases",
        ),
    ],
)
def test_a_benchmark_file_needs_a_graph_base_and_head(raw: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_benchmark("x", raw, Path("."))


# T100: the first real row, langchain-ai/open_deep_research PR #163, labels reviewed by a human.
ROW_163 = Path(__file__).resolve().parents[1] / "docs" / "benchmark" / "open_deep_research-163.yaml"
_ROW: dict[str, object] = {
    "graph": "g.py:graph",
    "base_ref": "a1",
    "head_ref": "b2",
    "cases": [{"q": "hi"}],
    "expected": {"root": ["x"], "propagated": ["y"]},
}


def test_the_163_row_validates_against_the_benchmark_schema() -> None:
    b = parse_benchmark(ROW_163.stem, yaml.safe_load(ROW_163.read_text("utf-8")), ROW_163.parent)
    assert b.status == "labels_reviewed"
    assert (b.base_ref, b.head_ref) == (
        "f95d200654b5c4733e87375be15b92822c6c4ff8",  # the merge commit's only parent
        "6532a4176a93cc9bb2102b3d825dcefa560c85d9",
    )
    assert b.graph == "src/open_deep_research/deep_researcher.py:deep_researcher"
    assert "research_supervisor.supervisor" in b.root
    assert b.propagated == {"final_report_generation"}
    assert set(b.hard_cases) == {"subgraph", "loop"}
    assert {(e.node, e.label) for e in b.evidence} == {(n, "root") for n in b.root} | {
        ("final_report_generation", "propagated")
    }
    assert b.changed_nodes and all(c.file.startswith("src/") for c in b.changed_nodes)


def test_every_label_needs_evidence_when_a_row_gives_some() -> None:
    evidence = [{"node": "x", "label": "root", "at": "g.py:3", "diff": "+    return 2"}]
    with pytest.raises(ValueError, match="no evidence for propagated `y`"):
        parse_benchmark("b", {**_ROW, "evidence": evidence}, Path("."))


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        (
            {"node": "z", "label": "root", "at": "g.py:3", "diff": "+x"},
            "`z` isn't an expected root",
        ),
        ({"node": "x", "label": "root", "at": "g.py", "diff": "+x"}, "<file>:<line>"),
        ({"node": "x", "label": "root", "at": "g.py:3", "diff": "x"}, "start with"),
        ({"node": "x", "label": "maybe", "at": "g.py:3", "diff": "+x"}, "root or propagated"),
    ],
)
def test_an_evidence_entry_names_an_expected_label_a_line_and_diff_lines(
    entry: dict[str, str], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        parse_benchmark("b", {**_ROW, "evidence": [entry]}, Path("."))


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(
        ValueError,
        match=r"'status' must be one of \['labels_pending_human_review', 'labels_reviewed'\]",
    ):
        parse_benchmark("b", {**_ROW, "status": "done"}, Path("."))


def test_a_pending_row_is_listed_but_left_out_of_micro_and_macro() -> None:
    scored = _result("linear", {"b"}, {"b"})
    pending = pending_result(
        parse_benchmark("odr", {**_ROW, "status": "labels_pending_human_review"}, Path("."))
    )
    assert (pending.root, pending.seconds, pending.pending) == (Score(0, 0, 0), 0.0, True)
    assert micro([scored, pending], "root") == micro([scored], "root")
    assert macro([scored, pending], "propagated") == macro([scored], "propagated")
    md = render_markdown([scored, pending], "lgcov bench", "2026-10-31")
    assert "| odr | — | — | — | `x` | — | ⏳ pending review |" in md
    assert "⏳ 1 pending human review: listed, not run, and left out of micro and macro." in md
    assert "Known gaps" not in md  # waiting for labels isn't a gap
    alone = render_markdown([pending], "lgcov bench", "2026-10-31")
    assert "| **micro** | — | — | | | | |" in alone  # nothing scored, so no aggregate


def test_pending_reviewed_and_errored_rows_read_differently_and_none_is_scored() -> None:
    # T100: labels a human confirmed on a row that can't run yet (no clone, no API keys) must
    # not look like labels nobody checked, nor like a run that crashed.
    scored = _result("linear", {"b"}, {"b"})
    pending = pending_result(
        parse_benchmark("p", {**_ROW, "status": "labels_pending_human_review"}, Path("."))
    )
    reviewed = pending_result(
        parse_benchmark("r", {**_ROW, "status": "labels_reviewed"}, Path("."))
    )
    errored = replace(_result("e", set(), {"x"}), error="CalledProcessError: no such ref")
    rows = [scored, pending, reviewed, errored]
    assert (reviewed.status, reviewed.root, reviewed.seconds) == (
        "labels_reviewed",
        Score(0, 0, 0),
        0.0,
    )
    assert micro(rows, "root") == micro([scored], "root")
    assert macro(rows, "propagated") == macro([scored], "propagated")
    md = render_markdown(rows, "lgcov bench", "2026-11-02")
    assert "| p | — | — | — | `x` | — | ⏳ pending review |" in md
    assert "| r | — | — | — | `x` | — | 📝 labels reviewed, not run |" in md
    # An errored row shows no scores: Score(0, 0, 0) would read as a perfect 1.00.
    assert "| e | — | — | — | `x` | — | ❌ run error: CalledProcessError: no such ref |" in md
    assert "⏳ 1 pending human review: listed, not run, and left out of micro and macro." in md
    assert (
        "📝 1 with labels reviewed, not yet runnable: listed, not run, and left out of micro and"
        " macro." in md
    )
    assert "- `e`: run error: CalledProcessError: no such ref" in md


def test_pythonpath_defaults_to_nothing_and_is_read_when_given() -> None:
    raw = {**_ROW, "pythonpath": ["src", "lib/python"]}
    assert parse_benchmark("x", raw, Path(".")).pythonpath == ("src", "lib/python")
    assert parse_benchmark("x", dict(_ROW), Path(".")).pythonpath == ()


@pytest.mark.parametrize(
    "bad", ["src", [1], ["/abs/src"], ["../other"], ["src/../../other"]], ids=str
)
def test_pythonpath_must_be_folders_inside_the_side(bad: object) -> None:
    # A folder outside the side would give both sides one copy: what this field exists to stop.
    with pytest.raises(ValueError, match="pythonpath"):
        parse_benchmark("x", {**_ROW, "pythonpath": bad}, Path("."))


# T162: a node is root when its own code changed and it drifts on base's recorded input.
def test_drift_exceeds_noise_is_plain_drift_until_t211() -> None:
    assert drift_exceeds_noise("n", {"x": 1}, {"x": 2})
    assert not drift_exceeds_noise("n", {"x": 1}, {"x": 1})


def test_classify_names_every_changed_node_that_drifts_on_its_own_input_root() -> None:
    labels = classify(
        drifted=["b", "c", "d", "e"],
        codes=(
            {"a": "1", "b": "1", "c": "1", "d": "1", "e": "1"},
            {"a": "1", "b": "2", "c": "2", "d": "2", "e": "1"},
        ),
        probes={"b": Probe(True), "c": Probe(True), "d": Probe(False)},
    )
    assert (labels.root, labels.propagated, labels.inconclusive) == (("b", "c"), ("d", "e"), {})


def test_classify_a_changed_node_with_no_offline_probe_is_inconclusive_not_root() -> None:
    labels = classify(
        drifted=["b", "c", "sub"],
        codes=({"b": "1", "c": "1", "sub": None}, {"b": "2", "c": "2", "sub": None}),
        probes={"b": Probe(None, NEEDS_LIVE)},
    )
    assert labels.root == labels.propagated == ()
    assert labels.inconclusive == {"b": NEEDS_LIVE, "c": NOT_PROBED, "sub": NO_FINGERPRINT}
    assert NEEDS_LIVE == "needs live probe; noise threshold pending T211"


def test_classify_a_node_only_one_side_ran_is_root() -> None:
    labels = classify(drifted=["new"], codes=({}, {"new": "1"}), probes={})
    assert labels.root == ("new",)


def test_a_run_that_drifted_with_no_changed_node_still_has_an_inconclusive_node() -> None:
    # A change outside node code (an edge, a router, a Send) or an edited recording: nothing is
    # root, so the run's first drifted node is inconclusive and fail-on: root-drift still blocks.
    labels = NodeLabels(root=("b",), propagated=("c", "w"), inconclusive={})
    out = explain_runs(labels, first_drifts=["b", "w", None], drifted=[["b", "c"], ["w"], []])
    assert (out.root, out.propagated, out.inconclusive) == (("b",), ("c",), {"w": UNEXPLAINED})
