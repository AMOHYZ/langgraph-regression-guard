"""T062 step 1: JevJudge — one typed Jev question per KPI, mapped to a Verdict."""

from __future__ import annotations

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


def _question(kpi: KPI) -> dict[str, Any]:
    """One typed choice question: the rubric as text, labels as the enum criteria."""
    return {
        "type": "choice",
        "instructions": kpi.rubric,
        "criteria": {label: kpi.hint(label) for label in kpi.labels},
    }


class JevJudge:
    """Scores one KPI by sending the node I/O to the Jev API as one typed question."""

    def __init__(self, kpi: KPI, client: _PostClient | None = None) -> None:
        self.kpi = kpi
        self._client = client

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        url = os.environ.get("JEV_API_URL")
        key = os.environ.get("JEV_API_KEY")
        if not url or not key:
            raise JudgeInfraError(
                "JevJudge needs JEV_API_URL and JEV_API_KEY in the environment "
                f"(kpi '{self.kpi.name}')"
            )
        payload = {
            "model": "jev-latest",
            "state": {"input": node_input, "output": node_output},
            "questions": {self.kpi.name: _question(self.kpi)},
        }
        # The key never appears in payloads we log, error messages, or exceptions.
        headers = {"Authorization": f"Bearer {key}"}
        if self._client is not None:
            return self._request(self._client, url, payload, headers)
        with httpx.Client() as client:  # owned per call: inject one to reuse connections
            return self._request(client, url, payload, headers)

    def _request(
        self, client: _PostClient, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Verdict:
        last: Exception | None = None
        for _ in range(ATTEMPTS):
            try:
                response = client.post(url, json=payload, timeout=TIMEOUT_S, headers=headers)
            except httpx.HTTPError as exc:  # transport errors and timeouts: retry
                last = exc
                continue
            if response.status_code >= 500:
                last = JudgeInfraError(
                    f"jev: HTTP {response.status_code} for kpi '{self.kpi.name}'"
                )
                continue
            if response.status_code >= 400:
                # No retry: a bad key or malformed request will not fix itself.
                raise JudgeInfraError(
                    f"jev: HTTP {response.status_code} for kpi '{self.kpi.name}' "
                    "(check JEV_API_URL / JEV_API_KEY)"
                )
            return self._to_verdict(response.json())
        raise JudgeInfraError(
            f"jev: kpi '{self.kpi.name}' unreachable after {ATTEMPTS} attempts: {last}"
        )

    def _to_verdict(self, body: dict[str, Any]) -> Verdict:
        try:
            answer = body["answers"][self.kpi.name]
            label = answer["choice"]
            confidence = answer["confidence"]
        except (KeyError, TypeError) as exc:
            raise JudgeInfraError(
                f"jev: unexpected response shape for kpi '{self.kpi.name}': {exc!r}"
            ) from exc
        if label not in self.kpi.labels:
            raise ValueError(
                f"kpi '{self.kpi.name}': judge returned label '{label}' not in {self.kpi.labels}"
            )
        return Verdict(self.kpi, label, self.kpi.score_map[label], confidence, answer)
