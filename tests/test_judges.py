"""T062 step 1: Judge protocol, JevJudge, and the confidence-aware verdict gate."""

from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from lgcov.jev_judge import JevJudge
from lgcov.judge_gate import verdict_gate
from lgcov.judges import KPI, FakeJudge, JudgeInfraError, Verdict, build_judges

GOOD_BAD = KPI(
    name="answer_quality",
    description="Is the answer correct and complete?",
    labels=("good", "bad"),
    rubric="good = correct and complete; bad = anything else",
    score_map={"good": 1.0, "bad": 0.0},
    label_hints={"good": "correct and complete", "bad": "wrong or incomplete"},
)

TOOL_USE = KPI(
    name="tool_use",
    description="Does the node call the right tools?",
    labels=("right", "wrong"),
    rubric="right = correct tool and arguments; wrong = anything else",
    score_map={"right": 1.0, "wrong": 0.0},
)


# --- judges.py: taxonomy, Verdict contract, FakeJudge ---


def test_taxonomy_yields_one_judge_per_kpi() -> None:
    judges = build_judges([GOOD_BAD, TOOL_USE], lambda k: FakeJudge(k, label=k.labels[0]))
    assert [j.kpi.name for j in judges] == ["answer_quality", "tool_use"]


def test_binary_kpi_defaults() -> None:
    kpi = KPI.binary(
        "grounded",
        "Every claim in the answer has a source.",
        "pass = every claim is cited; fail = any uncited claim",
    )
    assert kpi.labels == ("pass", "fail")
    assert kpi.score_map == {"pass": 1.0, "fail": 0.0}


def test_fake_judge_verdict_is_in_range() -> None:
    verdict = FakeJudge(GOOD_BAD, label="good", confidence=0.9).judge({}, {})
    assert verdict.label in GOOD_BAD.labels
    assert 0.0 <= verdict.score <= 1.0
    assert 0.0 <= verdict.confidence <= 1.0


def test_verdict_rejects_unknown_label() -> None:
    with pytest.raises(ValueError, match="answer_quality"):
        Verdict(GOOD_BAD, "awful", 0.5, 0.5, {})


def test_verdict_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="score"):
        Verdict(GOOD_BAD, "good", 1.5, 0.5, {})
    with pytest.raises(ValueError, match="confidence"):
        Verdict(GOOD_BAD, "good", 0.5, -0.1, {})


def test_kpi_validates_score_map() -> None:
    with pytest.raises(ValueError, match="tool_use"):
        KPI(
            name="tool_use",
            description="bad score_map",
            labels=("right", "wrong"),
            rubric="r",
            score_map={"right": 1.0},
        )


# --- judge_gate.py wiring: pass / fail / needs_human / infra_error ---


def _verdicts(kpi: KPI, label: str, n: int, confidence: float) -> list[Verdict]:
    return [Verdict(kpi, label, kpi.score_map[label], confidence, {}) for _ in range(n)]


def test_equal_verdict_distributions_pass() -> None:
    base = _verdicts(GOOD_BAD, "good", 10, 0.9) + _verdicts(GOOD_BAD, "bad", 2, 0.9)
    new = _verdicts(GOOD_BAD, "good", 10, 0.9) + _verdicts(GOOD_BAD, "bad", 2, 0.9)
    assert verdict_gate(base, new) == "pass"


def test_confident_drop_fails() -> None:
    base = _verdicts(GOOD_BAD, "good", 12, 0.9)
    new = _verdicts(GOOD_BAD, "bad", 12, 0.9)
    assert verdict_gate(base, new) == "fail"


def test_unconfident_drop_needs_human() -> None:
    base = _verdicts(GOOD_BAD, "good", 12, 0.9)
    new = _verdicts(GOOD_BAD, "bad", 12, 0.5)
    assert verdict_gate(base, new) == "needs_human"


def test_verdicts_pair_up_by_position() -> None:
    # T091: the same scores on both sides, but every case flipped: paired, that's no SHIP.
    good, bad = _verdicts(GOOD_BAD, "good", 6, 0.9), _verdicts(GOOD_BAD, "bad", 6, 0.9)
    assert verdict_gate(good + bad, bad + good) == "needs_human"


def test_infra_error_in_candidates_gates_to_infra_error() -> None:
    base = _verdicts(GOOD_BAD, "good", 12, 0.9)
    new: Sequence[Verdict | JudgeInfraError] = [
        *_verdicts(GOOD_BAD, "good", 11, 0.9),
        JudgeInfraError("jev down"),
    ]
    assert verdict_gate(base, new) == "infra_error"


def test_confident_drop_with_infra_error_is_not_fail() -> None:
    base = _verdicts(GOOD_BAD, "good", 12, 0.9)
    new: Sequence[Verdict | JudgeInfraError] = [
        *_verdicts(GOOD_BAD, "bad", 11, 0.9),
        JudgeInfraError("jev down"),
    ]
    assert verdict_gate(base, new) == "infra_error"


# --- jev_judge.py: env, request shape, response mapping, retry policy ---


def _jev_response(kpi: KPI, label: str, confidence: float) -> dict[str, Any]:
    probabilities = {lab: (confidence if lab == label else 1.0 - confidence) for lab in kpi.labels}
    return {
        "model": "typesafe/jev-1.13.0",
        "answers": {
            kpi.name: {
                "type": "choice",
                "choice": label,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        },
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _ok(kpi: KPI, label: str, confidence: float) -> _FakeResponse:
    return _FakeResponse(_jev_response(kpi, label, confidence))


class _FakeClient:
    """Each post() call pops one queued outcome: an Exception to raise, or a response."""

    def __init__(self, outcomes: list[Exception | _FakeResponse]) -> None:
        self._outcomes = outcomes
        self.payloads: list[dict[str, Any]] = []
        self.timeouts: list[float] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> _FakeResponse:
        self.payloads.append(json)
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_API_URL", "https://jev.example/v1/score")
    monkeypatch.setenv("JEV_API_KEY", "test-key")


def test_missing_env_vars_raise_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_URL", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JEV_API_URL"):
        JevJudge(GOOD_BAD).judge({}, {})


def test_jev_response_maps_to_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([_ok(GOOD_BAD, "bad", 0.85)])
    verdict = JevJudge(GOOD_BAD, client=client).judge({"q": "?"}, {"a": "wrong"})
    assert verdict.label == "bad"
    assert verdict.score == 0.0
    assert verdict.confidence == 0.85
    assert verdict.raw["choice"] == "bad"
    question = client.payloads[0]["questions"][GOOD_BAD.name]
    assert question["instructions"] == GOOD_BAD.rubric
    assert question["criteria"] == GOOD_BAD.label_hints


def test_jev_criteria_fall_back_to_bare_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([_ok(TOOL_USE, "right", 0.9)])
    JevJudge(TOOL_USE, client=client).judge({}, {})
    question = client.payloads[0]["questions"][TOOL_USE.name]
    assert question["criteria"] == {"right": "right", "wrong": "wrong"}


def test_jev_label_outside_kpi_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([_ok(GOOD_BAD, "awful", 0.9)])
    with pytest.raises(ValueError, match="answer_quality"):
        JevJudge(GOOD_BAD, client=client).judge({}, {})


def test_transport_error_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([httpx.ConnectError("boom"), _ok(GOOD_BAD, "good", 0.9)])
    verdict = JevJudge(GOOD_BAD, client=client).judge({}, {})
    assert verdict.label == "good"
    assert client.timeouts == [10.0, 10.0]


def test_second_transport_failure_raises_infra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([httpx.ConnectError("boom"), httpx.ConnectError("still down")])
    with pytest.raises(JudgeInfraError):
        JevJudge(GOOD_BAD, client=client).judge({}, {})
    assert len(client.payloads) == 2


def test_5xx_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([_FakeResponse({}, status_code=503), _ok(GOOD_BAD, "good", 0.9)])
    verdict = JevJudge(GOOD_BAD, client=client).judge({}, {})
    assert verdict.label == "good"
    assert len(client.payloads) == 2


def test_4xx_fails_immediately_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    client = _FakeClient([_FakeResponse({}, status_code=401)])
    with pytest.raises(JudgeInfraError, match="401") as excinfo:
        JevJudge(GOOD_BAD, client=client).judge({}, {})
    assert len(client.payloads) == 1
    assert "test-key" not in str(excinfo.value)


# T073: a deterministic flaky mode for offline pass@k demos, and Jev's token usage.
def test_fake_judge_fails_every_nth_call_on_matching_inputs() -> None:
    kpi = KPI.binary("k", "d", "r")
    judge = FakeJudge(kpi, "pass", fail_every=3, when_input_contains="order")
    order = [judge.judge({"question": "Where is my order?"}, {}).label for _ in range(6)]
    assert order == ["pass", "pass", "fail", "pass", "pass", "fail"]
    assert {judge.judge({"question": "What are your hours?"}, {}).label for _ in range(5)} == {
        "pass"
    }


def test_jev_keeps_the_response_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    usage = {"input_tokens": 462, "output_tokens": 77}
    payload = {**_jev_response(GOOD_BAD, "good", 0.9), "usage": usage}
    verdict = JevJudge(GOOD_BAD, client=_FakeClient([_FakeResponse(payload)])).judge({}, {})
    assert verdict.raw["usage"] == usage
    plain = JevJudge(GOOD_BAD, client=_FakeClient([_ok(GOOD_BAD, "good", 0.9)])).judge({}, {})
    assert "usage" not in plain.raw
