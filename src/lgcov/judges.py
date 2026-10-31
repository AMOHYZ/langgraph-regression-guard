"""T062 step 1: KPI taxonomy, the Judge protocol, and verdicts."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_LABELS = 255

# Arbitrary user state payloads, same rationale as cassette.NodeIO.
NodeState = dict[str, Any]


class JudgeInfraError(RuntimeError):
    """The judge backend could not be reached or answered (transport, auth, 5xx).

    An infra problem is never evidence of a regression, so the gate reports it
    separately instead of failing the run.
    """


@dataclass(frozen=True)
class KPI:
    name: str
    description: str
    labels: tuple[str, ...]
    rubric: str
    score_map: dict[str, float]
    label_hints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= len(self.labels) <= MAX_LABELS:
            raise ValueError(
                f"KPI '{self.name}': needs 1..{MAX_LABELS} labels, got {len(self.labels)}"
            )
        if len(set(self.labels)) != len(self.labels):
            raise ValueError(f"KPI '{self.name}': duplicate labels {self.labels}")
        if set(self.score_map) != set(self.labels):
            raise ValueError(f"KPI '{self.name}': score_map must cover exactly {self.labels}")
        bad_scores = [s for s in self.score_map.values() if not 0.0 <= s <= 1.0]
        if bad_scores:
            raise ValueError(f"KPI '{self.name}': scores must be in [0, 1], got {bad_scores}")
        if not set(self.label_hints) <= set(self.labels):
            raise ValueError(f"KPI '{self.name}': label_hints keys must be labels")

    @classmethod
    def binary(cls, name: str, description: str, rubric: str) -> KPI:
        """Pass/fail labelling, the recommended default for regression gates."""
        return cls(
            name=name,
            description=description,
            labels=("pass", "fail"),
            rubric=rubric,
            score_map={"pass": 1.0, "fail": 0.0},
        )

    def hint(self, label: str) -> str:
        """The label's question hint: its label_hints entry if given, else the label."""
        return self.label_hints.get(label, label)


@dataclass(frozen=True)
class Verdict:
    kpi: KPI
    label: str
    score: float
    confidence: float
    raw: dict[str, Any]  # provider JSON; the shape varies by judge backend

    def __post_init__(self) -> None:
        if self.label not in self.kpi.labels:
            raise ValueError(
                f"kpi '{self.kpi.name}': label '{self.label}' not in {self.kpi.labels}"
            )
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"kpi '{self.kpi.name}': score {self.score} outside [0, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"kpi '{self.kpi.name}': confidence {self.confidence} outside [0, 1]")


class Judge(Protocol):
    """One judge scores one KPI over one node's input/output pair."""

    # Read-only so frozen dataclasses (the T072 code judges) satisfy the protocol.
    @property
    def kpi(self) -> KPI: ...

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict: ...


class FakeJudge:
    """Deterministic judge for tests and offline demos: always the same label and confidence.

    With fail_every=n, every nth call whose input contains `when_input_contains` returns the
    KPI's lowest-scoring label instead: a reproducible flaky case for pass@k demos (T073).
    """

    def __init__(
        self,
        kpi: KPI,
        label: str,
        confidence: float = 1.0,
        fail_every: int = 0,
        when_input_contains: str = "",
    ) -> None:
        self.kpi = kpi
        self.label = label
        self.confidence = confidence
        self.fail_every = fail_every
        self.when_input_contains = when_input_contains
        self._matching_calls = 0

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        label = self.label
        if self.fail_every and self.when_input_contains in json.dumps(node_input, default=str):
            self._matching_calls += 1
            if self._matching_calls % self.fail_every == 0:
                label = min(self.kpi.labels, key=self.kpi.score_map.__getitem__)
        return Verdict(self.kpi, label, self.kpi.score_map[label], self.confidence, {})


def build_judges(kpis: Sequence[KPI], factory: Callable[[KPI], Judge]) -> list[Judge]:
    """One judge per KPI (tests pass a FakeJudge factory, prod a JevJudge one)."""
    return [factory(kpi) for kpi in kpis]
