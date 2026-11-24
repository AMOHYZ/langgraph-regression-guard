"""T188: OpenAI-compatible LLM judge client (fakes only; no network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from lgcov.judges import KPI, JudgeInfraError, Verdict
from lgcov.llm_judge import LlmJudge


def _kpi() -> KPI:
    return KPI.binary("tone", "Is the answer polite?", "Is the answer polite?")


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_llm_judge_maps_openai_json_choice_to_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    body = {
        "choices": [{"message": {"content": json.dumps({"label": "pass", "confidence": 0.91})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    client = _FakeClient(_FakeResponse(200, body))
    judge = LlmJudge(
        _kpi(),
        client=client,
        model="gpt-4o-mini",
        base_url="https://api.example.com/v1",
    )
    verdict = judge.judge({"q": "hi"}, {"answer": "hello"})
    assert isinstance(verdict, Verdict)
    assert (verdict.label, verdict.score, verdict.confidence) == ("pass", 1.0, 0.91)
    assert verdict.raw["usage"] == body["usage"]
    assert client.calls[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert client.calls[0]["json"]["model"] == "gpt-4o-mini"
    assert "Authorization" in client.calls[0]["headers"]
    assert "sk-test-not-real" not in repr(judge)
    assert "sk-test-not-real" not in str(verdict)


def test_llm_judge_missing_key_is_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    judge = LlmJudge(_kpi(), model="m", base_url="https://api.example.com/v1")
    with pytest.raises(JudgeInfraError, match="OPENAI_API_KEY"):
        judge.judge({}, {})


def test_llm_judge_4xx_is_infra_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = _FakeClient(_FakeResponse(401, {"error": "bad key"}))
    judge = LlmJudge(_kpi(), client=client, model="m", base_url="https://api.example.com/v1")
    with pytest.raises(JudgeInfraError, match="HTTP 401"):
        judge.judge({}, {"a": 1})
    assert len(client.calls) == 1


def test_llm_judge_5xx_retries_then_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = _FakeClient(_FakeResponse(503, {"error": "busy"}))
    judge = LlmJudge(_kpi(), client=client, model="m", base_url="https://api.example.com/v1")
    with pytest.raises(JudgeInfraError, match="unreachable"):
        judge.judge({}, {"a": 1})
    assert len(client.calls) == 2


def test_llm_judge_transport_error_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = _FakeClient(httpx.ConnectError("boom"))
    judge = LlmJudge(_kpi(), client=client, model="m", base_url="https://api.example.com/v1")
    with pytest.raises(JudgeInfraError, match="ConnectError"):
        judge.judge({}, {"a": 1})
    assert len(client.calls) == 2


def test_llm_judge_bad_label_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    body = {"choices": [{"message": {"content": json.dumps({"label": "maybe", "confidence": 1})}}]}
    client = _FakeClient(_FakeResponse(200, body))
    judge = LlmJudge(_kpi(), client=client, model="m", base_url="https://api.example.com/v1")
    with pytest.raises(ValueError, match="maybe"):
        judge.judge({}, {})


def test_cli_factory_builds_llm_judge_from_config() -> None:
    from lgcov.cli import _llm_judge_factory
    from lgcov.config import parse_config

    cfg = parse_config(
        {
            "graph_entrypoint": "a.py:g",
            "llm_model": "gpt-4o-mini",
            "llm_base_url": "https://api.example.com/v1",
        }
    )
    factory = _llm_judge_factory(cfg)
    assert factory is not None
    judge = factory(_kpi())
    assert isinstance(judge, LlmJudge)
    assert (judge.model, judge.base_url) == ("gpt-4o-mini", "https://api.example.com/v1")
    assert _llm_judge_factory(parse_config({"graph_entrypoint": "a.py:g"})) is None
