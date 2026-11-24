"""T198: lgcov import langsmith --traces — fakes only; never print the API key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures.langsmith_api import KEY, Fake, Reply

from lgcov.cli import main
from lgcov.langsmith import LangSmith, LangSmithError, from_env, traces_to_cases

TRACES: list[dict[str, object]] = [
    {
        "id": "run-2",
        "start_time": "2026-10-02T09:00:00",
        "inputs": {"question": "hours?"},
        "outputs": {"answer": "9-5"},
        "name": "agent",
        "is_root": True,
    },
    {
        "id": "run-1",
        "start_time": "2026-10-01T09:00:00",
        "inputs": {"question": "where is my order?"},
        "outputs": {"answer": "shipped"},
        "name": "agent",
        "is_root": True,
    },
    {
        "id": "run-3",
        "start_time": "2026-10-03T09:00:00",
        "inputs": {"question": "refund?"},
        "outputs": None,
        "name": "agent",
        "is_root": True,
    },
]


def test_traces_to_cases_sorts_oldest_first_and_keeps_run_ids() -> None:
    inputs, sidecar = traces_to_cases(TRACES)
    assert [c["question"] for c in inputs] == [
        "where is my order?",
        "hours?",
        "refund?",
    ]
    assert [s["run_id"] for s in sidecar] == ["run-1", "run-2", "run-3"]


def test_langsmith_traces_lists_root_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake({"/runs": [Reply(200, TRACES[:2])]})
    ls = LangSmith(KEY, client=fake)
    got = ls.traces(limit=2)
    assert len(got) == 2
    assert fake.calls[0][0] == "/runs"
    assert fake.calls[0][1]["is_root"] == "true"
    assert KEY not in repr(LangSmith(KEY))


def test_cli_import_langsmith_traces_writes_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = Fake({"/runs": [Reply(200, TRACES)]})
    monkeypatch.setattr("lgcov.cli._langsmith_client", fake)
    monkeypatch.setenv("LANGSMITH_API_KEY", KEY)
    out = tmp_path / "traces.json"
    assert main(["import", "langsmith", "--traces", "--limit", "3", "--out", str(out)]) == 0
    body = json.loads(out.read_text(encoding="utf-8"))
    assert len(body) == 3
    sidecar = json.loads(out.with_suffix(".langsmith.json").read_text(encoding="utf-8"))
    assert sidecar[0]["run_id"] == "run-1"
    printed = capsys.readouterr().out + capsys.readouterr().err
    assert KEY not in printed


def test_cli_import_langsmith_traces_requires_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("lgcov.cli._langsmith_client", Fake({}))
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    out = tmp_path / "traces.json"
    assert main(["import", "langsmith", "--traces", "--out", str(out)]) == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "LANGSMITH_API_KEY" in err
    assert KEY not in err


def test_from_env_never_embeds_key_in_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", KEY)
    fake = Fake({"/runs": [Reply(401, {"detail": "nope"})]})
    ls = from_env(fake)
    assert ls is not None
    with pytest.raises(LangSmithError) as caught:
        ls.traces(limit=1)
    assert KEY not in str(caught.value)
