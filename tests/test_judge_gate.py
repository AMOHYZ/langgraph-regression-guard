from lgcov.judge_gate import judge_gate


def fake_judge(output: str) -> float:
    return 1.0 if output == "good" else 0.0


def test_equal_score_distributions_pass() -> None:
    runs = ["good"] * 8 + ["bad"] * 2
    assert judge_gate(runs, list(runs), fake_judge).passed


def test_clear_drop_fails() -> None:
    base = ["good"] * 18 + ["bad"] * 2  # 90%
    new = ["good"] * 10 + ["bad"] * 10  # 50%
    result = judge_gate(base, new, fake_judge)
    assert not result.passed
    assert result.drop_ci[0] > 0.05


def test_small_noisy_drop_passes() -> None:
    base = ["good"] * 8 + ["bad"] * 2  # 80%
    new = ["good"] * 7 + ["bad"] * 3  # 70%, within noise for 10 samples
    assert judge_gate(base, new, fake_judge).passed
