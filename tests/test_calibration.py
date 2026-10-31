"""T063: judge calibration against human labels (pure)."""

import json

import pytest

from lgcov.calibration import (
    Row,
    band,
    bands_document,
    cohen_kappa,
    fit_temperature,
    judge_bands,
    judge_stats,
    labels_needed,
    read_labels,
    rows_from_overrides,
    tune_threshold,
)


def _rows(pairs: list[tuple[str, str]], conf: float | None = None, form: str = "") -> list[Row]:
    return [Row(f"c{i}", "grounded", j, h, conf, form) for i, (j, h) in enumerate(pairs)]


def test_kappa_of_identical_labels_is_one_and_undefined_for_one_label_only() -> None:
    assert cohen_kappa([("pass", "pass"), ("fail", "fail"), ("pass", "pass")]) == 1.0
    assert cohen_kappa([("pass", "pass")] * 3) is None  # p_e = 1: no chance correction


def test_tpr_wilson_ci_for_9_of_10() -> None:
    # positive = a failure: TPR is the share of human-failed cases the judge also failed
    rows = _rows([("fail", "fail")] * 9 + [("pass", "fail")] + [("pass", "pass")] * 10)
    s = judge_stats("grounded", rows)
    assert (s.positives, s.negatives, s.tpr, s.tnr) == (10, 10, 0.9, 1.0)
    assert s.tpr_ci is not None and tuple(round(x, 2) for x in s.tpr_ci) == (0.60, 0.98)
    assert (s.prevalence, s.agreement) == (0.5, 0.95)


def test_kappa_ci_is_a_seeded_bootstrap_around_kappa() -> None:
    rows = _rows([("fail", "fail")] * 8 + [("pass", "fail")] * 2 + [("pass", "pass")] * 10)
    s = judge_stats("grounded", rows)
    assert s.kappa is not None and s.kappa_ci is not None
    assert s.kappa_ci[0] <= s.kappa <= s.kappa_ci[1]
    assert judge_stats("grounded", rows).kappa_ci == s.kappa_ci  # same seed, same interval


@pytest.mark.parametrize(("n", "half"), [(50, 0.22), (100, 0.16), (200, 0.11), (400, 0.08)])
def test_labels_needed_matches_the_anchors(n: int, half: float) -> None:
    needed = labels_needed(p_o=0.8, p_e=0.5, n=n)
    assert needed is not None and round(needed.half_width, 2) == half
    assert needed.n_for_010 == 246  # labels for a 95% CI of +-0.10 on kappa


def test_brier_and_reliability_bins_keep_the_pile_up_at_one() -> None:
    rows = [
        Row("a", "grounded", "fail", "fail", 0.62),
        Row("b", "grounded", "pass", "fail", 0.62),
        Row("c", "grounded", "pass", "pass", 1.0),
        Row("d", "grounded", "fail", "pass", 1.0),
    ]
    s = judge_stats("grounded", rows)
    # (0.62-1)^2 + (0.62-0)^2 + (1-1)^2 + (1-0)^2, over 4
    assert s.brier == pytest.approx((0.1444 + 0.3844 + 0 + 1) / 4)
    assert [(b.confidence, b.n, b.accuracy) for b in s.reliability] == [
        (0.62, 2, 0.5),
        (1.0, 2, 0.5),
    ]


def test_temperatures_are_fitted_per_question_form_and_never_pooled() -> None:
    yes_no = [(0.9, True)] * 9 + [(0.9, False)]  # 90% sure, 90% right: calibrated
    scale = [(0.9, True)] * 6 + [(0.9, False)] * 4  # 90% sure, 60% right: overconfident
    assert fit_temperature(yes_no) == pytest.approx(1.0, rel=1e-3)
    assert fit_temperature(scale) == pytest.approx(5.42, rel=1e-2)  # logit(.9) / logit(.6)
    rows = [
        Row(f"y{i}", "grounded", "pass" if ok else "fail", "pass", c, "yes_no")
        for i, (c, ok) in enumerate(yes_no)
    ]
    rows += [
        Row(f"s{i}", "grounded", "pass" if ok else "fail", "pass", c, "scale")
        for i, (c, ok) in enumerate(scale)
    ]
    temps = dict(judge_stats("grounded", rows).temperatures)
    assert temps["yes_no"] == pytest.approx(1.0, rel=1e-3)
    assert temps["scale"] == pytest.approx(5.42, rel=1e-2)


def test_threshold_tuning_needs_50_labels() -> None:
    few = [(0.9, True)] * 20 + [(0.1, False)] * 20
    assert tune_threshold(few) is None  # n = 40: too few labels to tune
    many = [(0.8, True)] * 35 + [(0.2, False)] * 30  # (P(fail) from the judge, human failed)
    t = tune_threshold(many)
    assert t is not None and t.cutoff == 0.8 and t.cv_balanced_accuracy == 1.0


def test_overrides_map_to_rows_and_older_records_are_counted_not_guessed() -> None:
    lines = [
        json.dumps(
            {
                "kpi": "grounded",
                "judge_label": "fail",
                "case_id": "run-0/answer",
                "human_label": 1.0,
                "node": "answer",
            }
        ),
        json.dumps({"kpi": "grounded", "judge_label": "pass", "human_label": 0.0, "node": "fmt"}),
        json.dumps({"human_label": 1.0, "node": "answer"}),  # before T063: no KPI, no judge label
        "",
    ]
    rows, skipped = rows_from_overrides(lines, {})
    assert rows == [
        Row("run-0/answer", "grounded", "fail", "pass"),
        Row("override-1", "grounded", "pass", "fail"),
    ]
    assert skipped == 1


def test_labels_csv_needs_its_columns_and_a_confidence_in_0_1() -> None:
    good = "case_id,judge_id,judge_label,human_label,judge_confidence,question_form\n"
    good += "c1,grounded,fail,fail,0.91,yes_no\nc2,grounded,pass,fail,,\n"
    assert read_labels(good) == [
        Row("c1", "grounded", "fail", "fail", 0.91, "yes_no"),
        Row("c2", "grounded", "pass", "fail"),
    ]
    with pytest.raises(ValueError, match=r"missing columns \['human_label'\]"):
        read_labels("case_id,judge_id,judge_label\nc1,grounded,fail\n")
    with pytest.raises(ValueError, match="line 2.*judge_confidence"):
        read_labels(
            "case_id,judge_id,judge_label,human_label,judge_confidence\nc1,g,fail,fail,1.5\n"
        )


def test_labels_outside_the_kpi_name_the_judge() -> None:
    with pytest.raises(ValueError, match="grounded.*'maybe'"):
        judge_stats("grounded", _rows([("maybe", "pass")]))


# T063 PR B: bands, on the lower bounds of the TPR and TNR intervals.
def test_twenty_of_twenty_each_way_blocks() -> None:
    s = judge_stats("grounded", _rows([("fail", "fail")] * 20 + [("pass", "pass")] * 20))
    assert s.tpr_ci is not None and s.tpr_ci[0] > 0.80  # 0.84
    assert band(s) == "blocking"


def test_sixteen_of_twenty_is_a_flag_the_point_is_080_but_not_the_lower_bound() -> None:
    rows = _rows([("fail", "fail")] * 16 + [("pass", "fail")] * 4 + [("pass", "pass")] * 20)
    s = judge_stats("grounded", rows)
    assert s.tpr == 0.8 and s.tpr_ci is not None and s.tpr_ci[0] < 0.8
    assert band(s) == "flag"


def test_no_labels_or_a_point_rate_under_080_is_advisory() -> None:
    assert band(None) == "advisory"
    rows = _rows([("fail", "fail")] * 10 + [("pass", "fail")] * 10 + [("pass", "pass")] * 20)
    assert band(judge_stats("grounded", rows)) == "advisory"


def test_code_judges_block_and_unlabelled_model_judges_take_gate_uncalibrated() -> None:
    kinds = {"visits": "code", "tone": "fake", "grounded": "jev"}
    bands = judge_bands(kinds, None, None)
    assert bands.of == {"visits": "blocking", "tone": "advisory", "grounded": "advisory"}
    assert bands.uncalibrated == ("grounded", "tone")
    assert judge_bands(kinds, None, None, uncalibrated="flag").of["tone"] == "flag"


def test_a_jev_model_other_than_the_labels_caps_jev_judges_at_flag_with_a_warning() -> None:
    rows = _rows([("fail", "fail")] * 20 + [("pass", "pass")] * 20)
    doc = json.loads(bands_document([judge_stats("grounded", rows)], jev_model="jev-2026-10"))
    assert doc["judges"]["grounded"]["band"] == "blocking"
    same = judge_bands({"grounded": "jev"}, doc, "jev-2026-10")
    assert (same.of, same.warnings) == ({"grounded": "blocking"}, ())
    moved = judge_bands({"grounded": "jev"}, doc, "jev-2026-11")
    assert moved.of == {"grounded": "flag"}
    assert "jev-2026-11" in moved.warnings[0] and "jev-2026-10" in moved.warnings[0]
