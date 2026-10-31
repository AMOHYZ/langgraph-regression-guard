"""T073: k samples per judged case; pass@k, pass^k, flakiness and cost per KPI."""

from collections.abc import Callable, Iterator

import pytest

from lgcov.judges import KPI, JudgeInfraError, NodeState, Verdict
from lgcov.sampling import (
    Sample,
    case_stats,
    gate_exit_code,
    gate_kpis,
    judge_samples,
    kpi_stats,
    render_gate_table,
    usage_tokens,
)

KPI_ = KPI.binary("grounded", "every claim is supported", "Is every claim supported?")
POLITE = KPI.binary("polite", "the answer is polite", "Is the answer polite?")


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
    assert stats.paired is not None and stats.paired.ci == (-1.0, -1.0)  # T091: paired Δ


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
        paired=None,
        ms_total=600.0,
        ms_mean=100.0,
        tokens_total=3234,
        tokens_mean=539.0,
        flaky_cases=("run-0/respond",),
    )
    steady = KpiStats("responds", 2, 3, 1.0, 1.0, 0, "fail", None, 0.6, 0.05, 0, 0.0, ())
    lines = render_kpi_table([flaky, steady])
    # T091: the verdict and the Δ CI moved to the gate table (render_gate_table).
    assert lines[:4] == [
        "| KPI | pass@k | pass^k | flaky | ms/judgment | tokens |",
        "|---|---|---|---|---|---|",
        "| grounded (n=2, k=3) | 100% | 50% | ⚠️ 50% | 100 (600 total) | 539 (3,234 total) |",
        "| responds (n=2, k=3) | 100% | 100% | 0% | 0.05 (0.6 total) | 0 |",
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
    assert "| ⚠️ judge unreliable: grounded (4 of 6 judgments didn't run) | | | | | |" in (
        render_kpi_table([stats])
    )
    steady = kpi_stats(KPI_, 3, base=_cases(["pass"] * 3), new=_cases(["pass"] * 3))
    assert not steady.judge_unreliable


# T091 PR B: the paired gate. Holm across blocking KPIs, Benjamini-Hochberg across advisory.
def _drop(
    n: int, fails: int
) -> tuple[dict[str, tuple[Sample, ...]], dict[str, tuple[Sample, ...]]]:
    """n cases that all pass on base; the first `fails` of them fail on the PR."""
    return _cases(*[["pass"]] * n), _cases(*[["fail"]] * fails, *[["pass"]] * (n - fails))


def test_a_ten_point_drop_on_200_cases_blocks() -> None:
    base, new = _drop(200, 20)
    stats = kpi_stats(KPI_, 1, base, new)
    assert stats.paired is not None and stats.paired.delta == pytest.approx(-0.10)
    assert (stats.paired.n, stats.decision, stats.outcome) == (200, "BLOCK", "fail")
    assert gate_exit_code([stats]) == 1


def test_the_same_drop_on_10_cases_is_inconclusive_and_prints_the_required_n() -> None:
    base, new = _drop(10, 1)
    stats = kpi_stats(KPI_, 1, base, new)
    assert (stats.decision, stats.outcome) == ("INCONCLUSIVE", "needs_human")
    assert stats.paired is not None and stats.paired.required_n == 71  # Eq. 6, Δ = -0.10
    assert "| ⚠️ underpowered: need 61 more cases for grounded (N*=71) |" + " |" * 10 in (
        render_gate_table([stats])
    )
    assert gate_exit_code([stats]) == 0  # INCONCLUSIVE exits 0 by default...
    assert gate_exit_code([stats], inconclusive_blocks=True) == 1  # ...1 with the switch, never 2


def test_holm_stops_a_borderline_kpi_from_blocking() -> None:
    base, new = _drop(60, 6)  # exact McNemar p = 2 / 2**6 = 0.031: blocks on its own
    grounded = kpi_stats(KPI_, 1, base, new)
    assert grounded.decision == "BLOCK"
    same = _cases(*[["pass"]] * 60)
    gated = gate_kpis([grounded, kpi_stats(POLITE, 1, same, same)])
    assert [(s.kpi, s.decision, s.outcome) for s in gated] == [
        ("grounded", "INCONCLUSIVE", "needs_human"),
        ("polite", "SHIP", "pass"),
    ]
    assert gated[0].p_adjusted == pytest.approx(0.0625)  # Holm with m = 2
    assert gate_exit_code(gated) == 0


def test_an_advisory_kpi_never_blocks() -> None:
    base, new = _drop(200, 20)
    [advisory] = gate_kpis([kpi_stats(KPI_, 1, base, new)], advisory={"grounded"})
    assert (advisory.blocking, advisory.decision, advisory.outcome) == (
        False,
        "BLOCK",
        "needs_human",
    )
    assert gate_exit_code([advisory], inconclusive_blocks=True) == 0


def test_a_blocking_infra_error_exits_two_unless_another_kpi_blocks() -> None:
    infra = kpi_stats(KPI_, 1, base=_cases(["pass"]), new=_cases(["infra"]))
    assert (infra.outcome, infra.decision) == ("infra_error", None)
    assert gate_exit_code([infra]) == 2
    base, new = _drop(200, 20)
    assert gate_exit_code([infra, kpi_stats(POLITE, 1, base, new)]) == 1


def test_gate_table_row_has_n_rates_delta_ci_p_margin_mde_n_star_verdict_and_root() -> None:
    base, new = _drop(200, 20)
    stats = kpi_stats(KPI_, 1, base, new, case_roots={"run-0": "respond", "run-50": "plan"})
    lines = render_gate_table([stats])
    assert lines[:3] == [
        "| KPI | n | base | PR | Δ (95% CI) | p adj. | δ | MDE | N* | verdict | root |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        "| grounded | 200 | 100% | 90% | -0.10 [-0.14, -0.06] | <0.001 | 0.05 | 0.05 | 71 "
        "| 🔴 BLOCK | `respond` |",  # run-50 didn't change: its root isn't this KPI's
    ]
