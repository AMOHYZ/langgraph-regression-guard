"""T105: the speed benchmark's aggregation and table, on fake timings."""

from lgcov.bench import Sample, Spread, aggregate, render

ENV = {"date": "2026-10-31", "platform": "macOS-15-arm64", "python": "3.12.9", "lgcov": "0.1.0"}


def _sample(wall: float, live: int = 3, tokens: int | None = None) -> Sample:
    return Sample(wall, wall / 2, 0.1, live, 5 - live, judge_calls=4, tokens=tokens)


def test_aggregate_takes_the_median_min_and_max_of_each_measure() -> None:
    row = aggregate("served", [_sample(3.0), _sample(1.0), _sample(2.0, live=2)])
    assert (row.mode, row.runs) == ("served", 3)
    assert row.wall == Spread(2.0, 1.0, 3.0)
    assert row.agent == Spread(1.0, 0.5, 1.5)
    assert row.live == Spread(3.0, 2.0, 3.0)
    assert row.tokens is None  # no run reported any


def test_tokens_count_only_the_runs_that_reported_them() -> None:
    row = aggregate("--live", [_sample(1.0, tokens=1200), _sample(1.0), _sample(1.0, tokens=1000)])
    assert row.tokens == Spread(1100.0, 1000.0, 1200.0)


def test_render_shows_one_row_per_mode_the_command_and_where_it_ran() -> None:
    rows = [
        aggregate("served", [_sample(1.5), _sample(2.5)]),
        aggregate("--live", [_sample(2.0, live=5)] * 2),
    ]
    doc = render(rows, "make bench-speed", ENV)
    header = "| mode | wall-clock | agent | judges | live / served nodes | judge calls | tokens |"
    assert header in doc
    assert (
        "| served | 2.00 s (1.50 s–2.50 s) | 1.00 s (750.0 ms–1.25 s) "
        "| 100.0 ms (100.0 ms–100.0 ms) | 3 / 2 | 4 | — |"
    ) in doc
    assert (
        "| --live | 2.00 s (2.00 s–2.00 s) | 1.00 s (1.00 s–1.00 s) "
        "| 100.0 ms (100.0 ms–100.0 ms) | 5 / 0 | 4 | — |"
    ) in doc
    assert "```bash\nmake bench-speed\n```" in doc
    assert "2026-10-31 on macOS-15-arm64, Python 3.12.9, lgcov 0.1.0" in doc
