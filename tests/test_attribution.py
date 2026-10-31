"""T101a: the attribution benchmark's scoring and report (pure)."""

from pathlib import Path

import pytest

from lgcov.attribution import Result, macro, micro, parse_benchmark, render_markdown, score


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
