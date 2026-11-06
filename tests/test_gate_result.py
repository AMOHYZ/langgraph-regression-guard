"""T154: a result is bound to the commits and policy that produced it, and checked before its
verdict is posted. A mismatch gives no verdict: a harness error, exit 2."""

import json
from pathlib import Path

import pytest

from lgcov.cli import main
from lgcov.gate_result import (
    GateResult,
    file_hash,
    parse_result,
    result_to_json,
    verify_result,
)

FIXTURES = Path(__file__).parent / "fixtures"
GRAPH = (FIXTURES / "demo_graph.py").read_text(encoding="utf-8")
BASELINE = (FIXTURES / "demo_baseline.json").read_text(encoding="utf-8")
CONFIG = "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
BASE_SHA, HEAD_SHA = "a" * 40, "b" * 40
POLICY = {"lgcov.yaml": file_hash(b"policy"), "taxonomy.yaml": ""}
REPORT = b"report"
RESULT = GateResult(BASE_SHA, HEAD_SHA, POLICY, 0, "pass", file_hash(REPORT))


def test_a_result_survives_the_round_trip_and_binds() -> None:
    assert parse_result(result_to_json(RESULT)) == RESULT
    assert verify_result(RESULT, BASE_SHA, HEAD_SHA, POLICY, REPORT) == ""


@pytest.mark.parametrize(
    ("base", "head", "policy", "report", "why"),
    [
        (
            "c" * 40,
            HEAD_SHA,
            POLICY,
            REPORT,
            "it is for base `aaaaaaa`, and this PR's is `ccccccc`",
        ),
        (
            BASE_SHA,
            "c" * 40,
            POLICY,
            REPORT,
            "it is for head `bbbbbbb`, and this PR's is `ccccccc`",
        ),
        (
            BASE_SHA,
            HEAD_SHA,
            {**POLICY, "lgcov.yaml": file_hash(b"tampered")},
            REPORT,
            "it wasn't graded on the base's policy: lgcov.yaml",
        ),
        (
            BASE_SHA,
            HEAD_SHA,
            {"lgcov.yaml": POLICY["lgcov.yaml"]},
            REPORT,
            "it wasn't graded on the base's policy: taxonomy.yaml",
        ),
        (BASE_SHA, HEAD_SHA, POLICY, b"## pass", "the report isn't the one it was written with"),
    ],
    ids=["another base", "another head", "other policy", "another file set", "edited report"],
)
def test_a_result_that_does_not_bind_says_why(
    base: str, head: str, policy: dict[str, str], report: bytes, why: str
) -> None:
    assert verify_result(RESULT, base, head, policy, report) == why


def test_editing_a_field_breaks_the_digest() -> None:
    body = json.loads(result_to_json(RESULT))
    body["exit_code"] = 1
    with pytest.raises(ValueError, match="digest"):
        parse_result(json.dumps(body))


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "{}",
        '{"schema": 99}',
        '{"schema": 1}',
        '{"schema": 1, "policy": {}}',
        '{"schema": 1, "policy": {}, "base_sha": "", "head_sha": "", "exit_code": null, '
        '"verdict": "", "report_sha256": ""}',
    ],
    ids=["a list", "empty", "another schema", "no policy", "no fields", "a null exit code"],
)
def test_what_is_not_a_result_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        parse_result(text)


def test_the_known_limit_a_forger_who_recomputes_every_field_passes() -> None:
    # Every value in the binding is public, so this is tamper evidence, not a signature. While the
    # PR's code shares the job it can do just this: docs/trust-boundary.md, closed by T179.
    forged = GateResult(BASE_SHA, HEAD_SHA, POLICY, 0, "pass", file_hash(b"forged report"))
    parsed = parse_result(result_to_json(forged))
    assert verify_result(parsed, BASE_SHA, HEAD_SHA, POLICY, b"forged report") == ""


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PR with no drift, in the working directory, and its base at ../base."""
    files = {"graph.py": GRAPH, "cassettes/baseline.json": BASELINE, "lgcov.yaml": CONFIG}
    _write(tmp_path / "base", files)
    _write(tmp_path / "pr", files)
    monkeypatch.chdir(tmp_path / "pr")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": HEAD_SHA}}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    return tmp_path / "pr"


REPORT_MD, RESULT_JSON, OUTPUTS = (
    Path("../out/report.md"),
    Path("../out/result.json"),
    Path("../out/outputs.txt"),
)


def _ci() -> int:
    """`lgcov ci` as the Action runs it. Neither checkout is a git repo here, so base is ''."""
    args = ["ci", "--report", str(REPORT_MD), "--result-out", str(RESULT_JSON)]
    return main([*args, "--base-dir", "../base", "--policy-dir", "../base"])


def _verify_cmd(base_sha: str = "", head_sha: str = HEAD_SHA) -> tuple[int, dict[str, str]]:
    OUTPUTS.parent.mkdir(exist_ok=True)
    OUTPUTS.write_text("", encoding="utf-8")
    args = ["verify-result", "--result", str(RESULT_JSON), "--report", str(REPORT_MD)]
    args += ["--github-output", str(OUTPUTS), "--policy-dir", "../base"]
    code = main([*args, "--base-sha", base_sha, "--head-sha", head_sha])
    lines = OUTPUTS.read_text(encoding="utf-8").splitlines()
    return code, {k: v for k, v in (x.split("=", 1) for x in lines)}


def _rejected(outputs: dict[str, str], why: str) -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    assert outputs == {"exit-code": "2", "verdict": "error"}
    assert "⚠️ Harness error, not a regression" in report
    assert why in report
    assert "verdict: **error**" in report and "**pass**" not in report and "🟢" not in report


def test_a_real_result_verifies_and_keeps_its_verdict(pr: Path) -> None:
    assert _ci() == 0
    before = REPORT_MD.read_text(encoding="utf-8")
    code, outputs = _verify_cmd()
    assert (code, outputs) == (0, {"exit-code": "0", "verdict": "pass"})
    assert REPORT_MD.read_text(encoding="utf-8") == before


def test_a_failing_result_verifies_and_keeps_its_exit_code(pr: Path) -> None:
    _write(pr, {"graph.py": GRAPH.replace("a concise", "a friendly")})
    assert _ci() == 1
    code, outputs = _verify_cmd()
    assert (code, outputs) == (1, {"exit-code": "1", "verdict": "fail"})


def test_a_result_edited_to_pass_gives_no_verdict(pr: Path) -> None:
    _write(pr, {"graph.py": GRAPH.replace("a concise", "a friendly")})
    assert _ci() == 1
    forged = json.loads(RESULT_JSON.read_text(encoding="utf-8"))
    forged["exit_code"], forged["verdict"] = 0, "pass"
    RESULT_JSON.write_text(json.dumps(forged), encoding="utf-8")
    code, outputs = _verify_cmd()
    assert code == 2
    _rejected(outputs, "its digest doesn't match")


def test_a_report_edited_to_pass_gives_no_verdict(pr: Path) -> None:
    assert _ci() == 0
    REPORT_MD.write_text("## 🟢 lgcov: pass\nverdict: **pass**\n", encoding="utf-8")
    code, outputs = _verify_cmd()
    assert code == 2
    _rejected(outputs, "the report isn't the one it was written with")


def test_a_result_from_another_commit_gives_no_verdict(pr: Path) -> None:
    assert _ci() == 0
    code, outputs = _verify_cmd(head_sha="c" * 40)
    assert code == 2
    _rejected(outputs, "it is for head `bbbbbbb`, and this PR's is `ccccccc`")


def test_policy_changed_during_the_run_gives_no_verdict(pr: Path, tmp_path: Path) -> None:
    assert _ci() == 0
    _write(tmp_path / "base", {"lgcov.yaml": CONFIG + "ignore_paths: [/answer]\n"})
    code, outputs = _verify_cmd()
    assert code == 2
    _rejected(outputs, "it wasn't graded on the base's policy: lgcov.yaml")


@pytest.mark.parametrize("missing", [RESULT_JSON, REPORT_MD], ids=["result", "report"])
def test_a_missing_file_gives_no_verdict(pr: Path, missing: Path) -> None:
    assert _ci() == 0
    missing.unlink()
    code, outputs = _verify_cmd()
    assert code == 2
    _rejected(outputs, "The result failed verification")


def test_a_check_that_raises_gives_no_verdict_even_with_no_message(
    pr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raises(*_: object) -> str:
        raise ValueError("")

    assert _ci() == 0
    monkeypatch.setattr("lgcov.cli.verify_result", raises)
    code, outputs = _verify_cmd()
    assert code == 2
    _rejected(outputs, "The result failed verification")


def test_a_first_adoption_result_verifies_as_advisory(pr: Path, tmp_path: Path) -> None:
    (tmp_path / "base" / "lgcov.yaml").unlink()
    (tmp_path / "base" / "cassettes" / "baseline.json").unlink()
    assert _ci() == 0
    code, outputs = _verify_cmd()
    assert (code, outputs) == (0, {"exit-code": "0", "verdict": "advisory"})
