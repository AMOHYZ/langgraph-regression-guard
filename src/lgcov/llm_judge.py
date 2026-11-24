"""T188: OpenAI-compatible chat judge for `judge: llm` KPIs (nightly tier).

Same Verdict type as Jev. Model and base URL come from lgcov.yaml (`llm_model`,
`llm_base_url`). The key is OPENAI_API_KEY only — never logged, repr'd, or committed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

from lgcov.judges import KPI, JudgeInfraError, NodeState, Verdict

TIMEOUT_S = 10.0
ATTEMPTS = 2  # one try + one retry; only transport errors, timeouts and 5xx retry


class _Response(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]: ...


class _PostClient(Protocol):
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> _Response: ...


def _prompt(kpi: KPI, node_input: NodeState, node_output: NodeState) -> list[dict[str, str]]:
    labels = ", ".join(kpi.labels)
    hints = "\n".join(f"- {label}: {kpi.hint(label)}" for label in kpi.labels)
    return [
        {
            "role": "system",
            "content": (
                "You are a strict binary/multi-label judge for an agent regression gate. "
                f'Reply with JSON only: {{"label": one of [{labels}], "confidence": 0..1}}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"KPI: {kpi.name}\nDescription: {kpi.description}\nRubric: {kpi.rubric}\n"
                f"Label hints:\n{hints}\n\n"
                f"Node input:\n{json.dumps(node_input, default=str)}\n\n"
                f"Node output:\n{json.dumps(node_output, default=str)}"
            ),
        },
    ]


class LlmJudge:
    """Scores one KPI via an OpenAI-compatible `/chat/completions` endpoint."""

    def __init__(
        self,
        kpi: KPI,
        *,
        model: str,
        base_url: str,
        client: _PostClient | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self.kpi = kpi
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"LlmJudge(kpi={self.kpi.name!r}, model={self.model!r}, base_url={self.base_url!r})"

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise JudgeInfraError(
                f"LlmJudge needs OPENAI_API_KEY in the environment (kpi '{self.kpi.name}')"
            )
        payload = {
            "model": self.model,
            "messages": _prompt(self.kpi, node_input, node_output),
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {key}"}
        url = f"{self.base_url}/chat/completions"
        if self._client is not None:
            return self._request(self._client, url, payload, headers)
        with httpx.Client() as client:
            return self._request(client, url, payload, headers)

    def _request(
        self, client: _PostClient, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Verdict:
        last: Exception | None = None
        for _ in range(ATTEMPTS):
            try:
                response = client.post(url, json=payload, timeout=self.timeout_s, headers=headers)
            except httpx.HTTPError as exc:
                last = exc
                continue
            if response.status_code >= 500:
                last = JudgeInfraError(
                    f"llm: HTTP {response.status_code} for kpi '{self.kpi.name}'"
                )
                continue
            if response.status_code >= 400:
                raise JudgeInfraError(
                    f"llm: HTTP {response.status_code} for kpi '{self.kpi.name}' "
                    "(check OPENAI_API_KEY / llm_base_url)"
                )
            return self._to_verdict(response.json())
        why = str(last) if isinstance(last, JudgeInfraError) else type(last).__name__
        raise JudgeInfraError(
            f"llm: kpi '{self.kpi.name}' unreachable after {ATTEMPTS} attempts: {why}"
        )

    def _to_verdict(self, body: dict[str, Any]) -> Verdict:
        try:
            content = body["choices"][0]["message"]["content"]
            answer = json.loads(content) if isinstance(content, str) else content
            if not isinstance(answer, dict):
                raise TypeError("content is not a JSON object")
            label = answer["label"]
            confidence = float(answer["confidence"])
        except (KeyError, TypeError, IndexError, json.JSONDecodeError, ValueError) as exc:
            raise JudgeInfraError(
                f"llm: unexpected response shape for kpi '{self.kpi.name}': {type(exc).__name__}"
            ) from None
        if label not in self.kpi.labels:
            raise ValueError(
                f"kpi '{self.kpi.name}': judge returned label '{label}' not in {self.kpi.labels}"
            )
        raw = dict(answer)
        if isinstance(body.get("usage"), dict):
            raw["usage"] = body["usage"]
        return Verdict(self.kpi, label, self.kpi.score_map[label], confidence, raw)
