"""T073: k samples per judged case; pass@k, pass^k, flakiness and cost per KPI."""

from collections.abc import Callable, Iterator

import pytest

from lgcov.judges import KPI, JudgeInfraError, NodeState, Verdict
from lgcov.sampling import Sample, case_stats, judge_samples, kpi_stats, usage_tokens

KPI_ = KPI.binary("grounded", "every claim is supported", "Is every claim supported?")


class Scripted:
    """Answers from a script of 'pass', 'fail' or 'infra' (raises JudgeInfraError)."""

    def __init__(self, script: list[str], usage: dict[str, int] | None = None) -> None:
        self.kpi = KPI_
        self._script = iter(script)
        self._usage = usage

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        step = next(self._script)
        if step == "infra":
            raise JudgeInfraError("jev: 503")
        if step == "timeout":
            raise TimeoutError("judge timed out")
        if step == "conn":
            raise ConnectionError("judge connection reset")
        raw: dict[str, object] = {} if self._usage is None else {"usage": dict(self._usage)}
        return Verdict(self.kpi, step, self.kpi.score_map[step], 0.9, raw)


def _ticks(*seconds: float) -> Callable[[], float]:
    """A clock whose consecutive calls return these times."""
    it: Iterator[float] = iter(seconds)
    return lambda: next(it)


def _cases(*scripts: list[str]) -> dict[str, tuple[Sample, ...]]:
    return {f"run-{i}": judge_samples(Scripted(s), {}, {}, len(s)) for i, s in enumerate(scripts)}


def test_k1_is_the_plain_pass_rate_and_never_flaky() -> None:
    cases = _cases(["pass"], ["fail"])
    stats = kpi_stats(KPI_, 1, base=cases, new=cases)
    assert (stats.pass_at_k, stats.pass_hat_k, stats.flakiness) == (0.5, 0.5, 0.0)
    assert stats.flaky_cases == ()


def test_pass_fail_pass_is_pass_at_k_but_not_pass_hat_k_and_is_flaky() -> None:
    c = case_stats("run-0", judge_samples(Scripted(["pass", "fail", "pass"]), {}, {}, 3))
    assert (c.pass_at_k, c.pass_hat_k, c.flaky, c.k) == (True, False, True, 3)


def test_all_fails_is_neither_pass_at_k_nor_flaky() -> None:
    c = case_stats("run-0", judge_samples(Scripted(["fail"] * 3), {}, {}, 3))
    assert (c.pass_at_k, c.pass_hat_k, c.flaky) == (False, False, False)


def test_an_infra_error_does_not_count_toward_k_or_as_a_fail() -> None:
    c = case_stats("run-0", judge_samples(Scripted(["pass", "infra", "pass"]), {}, {}, 3))
    assert (c.k, c.infra, c.pass_hat_k, c.flaky) == (2, 1, True, False)


def test_ms_and_token_totals_add_up() -> None:
    judge = Scripted(["pass", "fail", "pass"], usage={"input_tokens": 462, "output_tokens": 77})
    samples = judge_samples(judge, {}, {}, 3, clock=_ticks(0, 0.5, 0.5, 1.25, 1.25, 1.5))
    assert [s.ms for s in samples] == [500.0, 750.0, 250.0]
    stats = kpi_stats(KPI_, 3, base={"run-0": samples}, new={"run-0": samples})
    assert (stats.ms_total, stats.ms_mean) == (3000.0, 500.0)  # base + new: 6 judgments
    assert (stats.tokens_total, stats.tokens_mean) == (6 * 539, 539.0)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ({"usage": {"input_tokens": 462, "output_tokens": 77}}, (462, 77)),
        ({"usage": {"prompt_tokens": 462, "completion_tokens": 77}}, (462, 77)),
        ({"usage": {}}, None),
        ({}, None),
    ],
)
def test_usage_tokens_accepts_both_namings(raw: dict[str, object], want: object) -> None:
    assert usage_tokens(raw) == want


def test_tokens_are_unknown_unless_some_judgment_reports_usage() -> None:
    cases = _cases(["pass"])
    assert kpi_stats(KPI_, 1, base=cases, new=cases).tokens_total is None
    assert kpi_stats(KPI_, 1, base=cases, new=cases, code=True).tokens_total == 0


def test_flakiness_alone_never_fails_the_gate() -> None:
    cases = _cases(*[["pass", "fail", "pass"]] * 4)  # same flaky cases on base and new
    stats = kpi_stats(KPI_, 3, base=cases, new=cases)
    assert (stats.outcome, stats.flakiness) == ("pass", 1.0)
    assert stats.flaky_cases == ("run-0", "run-1", "run-2", "run-3")


def test_a_pass_hat_k_drop_fails_the_gate() -> None:
    base = _cases(*[["pass"] * 3] * 6)
    new = _cases(*[["fail"] * 3] * 6)
    stats = kpi_stats(KPI_, 3, base=base, new=new)
    assert stats.outcome == "fail"
    assert stats.drop_ci is not None and stats.drop_ci[0] > 0.05


def test_no_valid_samples_on_a_side_is_an_infra_error() -> None:
    stats = kpi_stats(KPI_, 3, base=_cases(["pass"] * 3), new=_cases(["infra"] * 3))
    assert (stats.outcome, stats.infra, stats.n) == ("infra_error", 3, 0)


def test_table_shows_rates_flaky_rows_cost_and_top_flaky_cases() -> None:
    from lgcov.sampling import KpiStats, render_kpi_table

    flaky = KpiStats(
        kpi="grounded",
        n=2,
        k=3,
        pass_at_k=1.0,
        pass_hat_k=0.5,
        infra=1,
        outcome="pass",
        drop_ci=(-0.5, 0.5),
        ms_total=600.0,
        ms_mean=100.0,
        tokens_total=3234,
        tokens_mean=539.0,
        flaky_cases=("run-0/respond",),
    )
    steady = KpiStats("responds", 2, 3, 1.0, 1.0, 0, "fail", None, 0.6, 0.05, 0, 0.0, ())
    lines = render_kpi_table([flaky, steady])
    assert lines[:4] == [
        "| KPI | pass@k | pass^k | flaky | CI | ms/judgment | tokens |",
        "|---|---|---|---|---|---|---|",
        "| 🟢 grounded (n=2, k=3) | 100% | 50% | ⚠️ 50% | [-0.50, +0.50] | 100 (600 total) "
        "| 539 (3,234 total) |",
        "| 🔴 responds (n=2, k=3) | 100% | 100% | 0% | — | 0.05 (0.6 total) | 0 |",
    ]
    assert "Top flaky cases, grounded: `run-0/respond`" in lines
    assert "Infra errors, not counted toward k: grounded 1" in lines


# T074: judge transport errors are infra samples; mostly-infra KPIs are flagged.
def test_judge_timeouts_and_connection_errors_are_infra_samples() -> None:
    c = case_stats("run-0", judge_samples(Scripted(["timeout", "conn", "pass"]), {}, {}, 3))
    assert (c.k, c.infra, c.pass_hat_k) == (1, 2, True)
    stats = kpi_stats(KPI_, 3, base=_cases(["timeout"] * 3), new=_cases(["timeout"] * 3))
    assert stats.outcome == "infra_error"


def test_a_kpi_with_mostly_infra_samples_shows_judge_unreliable() -> None:
    from lgcov.sampling import render_kpi_table

    cases = _cases(["pass", "timeout", "conn"])  # 2 of 3 judgments didn't run
    stats = kpi_stats(KPI_, 3, base=cases, new=cases)
    assert stats.outcome == "pass" and stats.judge_unreliable
    assert "| ⚠️ judge unreliable: grounded (4 of 6 judgments didn't run) | | | | | | |" in (
        render_kpi_table([stats])
    )
    steady = kpi_stats(KPI_, 3, base=_cases(["pass"] * 3), new=_cases(["pass"] * 3))
    assert not steady.judge_unreliable
