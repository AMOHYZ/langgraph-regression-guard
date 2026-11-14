"""`lgcov round new` and `lgcov guard`: a round's plan is fixed before its candidates see the
gate's cases, and what saw each case outlives the round. Real git in tmp repos, offline and away
from your git config."""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from lgcov.cli import lgcov_main

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = (
    "graph_entrypoint: graph.py:graph\n"
    "cassette_dir: cassettes\n"
    "taxonomy_path: taxonomy.yaml\n"
    "jev_model: jev-test-1\n"
)
BASELINE = "cassettes/baseline.json"
ROUND = "lgcov-round.yaml"
LEDGER = "exposure.json"
TAXONOMY = "taxonomy.yaml"
OTHER_SHA = "b" * 40


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _project(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A project at path, the working directory, committed to a new repo; its HEAD."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)  # CI's own event isn't this test's
    path.mkdir(exist_ok=True)
    monkeypatch.chdir(path)
    Path("lgcov.yaml").write_text(CONFIG, encoding="utf-8")
    shutil.copy(FIXTURES / "demo_taxonomy.yaml", TAXONOMY)
    Path("cassettes").mkdir()
    shutil.copy(FIXTURES / "demo_baseline.json", BASELINE)
    _git(path, "init", "-q")
    return _commit(path, "start")


def _draft(*candidates: str) -> None:
    """The fields a person writes in the round file."""
    draft: dict[str, object] = {
        "version": 1,
        "gate_cases": {"path": BASELINE},
        "kpis": [{"name": "respond_runs", "tags": ["flow"]}],
        "delta_1": 0.0,
        "prior_rho": 0.6,
        "candidates": list(candidates),
        "stop_rules": {"max_cases": 800},
    }
    Path(ROUND).write_text(yaml.safe_dump(draft), encoding="utf-8")


def _locked(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A project whose HEAD is the one candidate of a locked round; that HEAD."""
    head = _project(path, monkeypatch)
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    return head


def _round() -> dict[str, object]:
    doc: dict[str, object] = yaml.safe_load(Path(ROUND).read_text(encoding="utf-8"))
    return doc


def _edit_round(**fields: object) -> None:
    Path(ROUND).write_text(yaml.safe_dump({**_round(), **fields}), encoding="utf-8")


def _cases() -> dict[str, dict[str, list[object]]]:
    cases: dict[str, dict[str, list[object]]] = json.loads(
        Path(LEDGER).read_text(encoding="utf-8")
    )["cases"]
    return cases


def _edit_baseline(change: Callable[[dict[str, list[dict[str, object]]]], None]) -> None:
    doc = json.loads(Path(BASELINE).read_text(encoding="utf-8"))
    change(doc)
    Path(BASELINE).write_text(json.dumps(doc), encoding="utf-8")


def _replace(path: str, old: str, new: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert old in text
    Path(path).write_text(text.replace(old, new), encoding="utf-8")


def _edit_a_case() -> None:
    def change(doc: dict[str, list[dict[str, object]]]) -> None:
        doc["runs"][0]["inputs"] = {"question": "Where is my refund?"}

    _edit_baseline(change)


def _add_a_case() -> None:
    def change(doc: dict[str, list[dict[str, object]]]) -> None:
        doc["runs"].append({"inputs": {"question": "A new case"}, "steps": []})

    _edit_baseline(change)


def _drop_a_case() -> None:
    def change(doc: dict[str, list[dict[str, object]]]) -> None:
        del doc["runs"][0]

    _edit_baseline(change)


def _edit_a_rubric() -> None:
    _replace(TAXONOMY, "Did the run visit respond?", "Did the run end by responding?")


def _edit_the_model() -> None:
    _replace("lgcov.yaml", "jev-test-1", "jev-test-2")


def _make_the_fake_kpi_block() -> None:
    """A band for the KPI that has none, which takes K_gate from 1 to 2."""
    bands = {"judges": {"answer_states_order_status": {"band": "blocking"}}}
    Path("calibration").mkdir()
    Path("calibration/bands.json").write_text(json.dumps(bands), encoding="utf-8")


# --- The three the request names: a changed hash, an unlocked SHA, exposure across rounds.


@pytest.mark.parametrize(
    ("edit", "named"),
    [
        (_edit_a_case, "gate case set"),
        (_add_a_case, "gate case set"),
        (_drop_a_case, "gate case set"),
        (_edit_a_rubric, "judge prompt hash"),
        (_edit_the_model, "judge model"),
    ],
    ids=["case edited", "case added", "case dropped", "rubric", "model"],
)
def test_guard_exits_2_when_a_locked_hash_changed(
    edit: Callable[[], None],
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _locked(tmp_path, monkeypatch)
    assert lgcov_main(["guard"]) == 0
    edit()
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 2
    assert f"- {named}: locked " in capsys.readouterr().err


def test_guard_exits_2_when_lgcov_itself_changed_the_judge_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    monkeypatch.setattr("lgcov.cli.version", lambda _: "9.9.9")
    assert lgcov_main(["guard"]) == 2
    assert ", now 9.9.9" in capsys.readouterr().err


def test_guard_names_every_problem_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    _edit_a_case()
    _edit_the_model()
    assert lgcov_main(["guard", "--candidate", OTHER_SHA]) == 2
    err = capsys.readouterr().err
    assert "- gate case set: locked " in err
    assert "- judge model: locked jev-test-1, now jev-test-2" in err
    assert "- candidate `bbbbbbb` is not locked in round 1" in err


def test_guard_exits_2_when_head_is_not_a_locked_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    locked = _locked(tmp_path, monkeypatch)
    pushed = _commit(tmp_path, "a push after the lock")
    assert lgcov_main(["guard"]) == 2
    err = capsys.readouterr().err
    assert f"candidate `{pushed[:7]}` is not locked in round 1 (locked: `{locked[:7]}`)" in err


def test_guard_exits_2_when_a_candidate_it_is_given_is_not_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = _locked(tmp_path, monkeypatch)
    _commit(tmp_path, "main moved on")
    assert lgcov_main(["guard", "--candidate", OTHER_SHA]) == 2
    assert lgcov_main(["guard", "--candidate", locked]) == 0


def test_a_short_candidate_is_refused_before_anything_is_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        lgcov_main(["guard", "--candidate", "abc1234"])
    assert exc.value.code == 2
    assert "'abc1234' isn't a full commit SHA" in capsys.readouterr().err


def test_on_a_pr_guard_checks_the_prs_head_not_githubs_merge_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = _locked(tmp_path, monkeypatch)
    _commit(tmp_path, "the merge commit GitHub checks out on a pull_request event")
    event = tmp_path.parent / "event.json"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    event.write_text(json.dumps({"pull_request": {"head": {"sha": locked}}}), encoding="utf-8")
    assert lgcov_main(["guard"]) == 0
    event.write_text(json.dumps({"pull_request": {"head": {"sha": OTHER_SHA}}}), encoding="utf-8")
    assert lgcov_main(["guard"]) == 2


def test_guard_exits_2_when_it_cant_tell_which_commit_this_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / ".git")
    assert lgcov_main(["guard"]) == 2
    assert "pass --candidate" in capsys.readouterr().err


def test_exposure_survives_a_new_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _locked(tmp_path, monkeypatch)
    assert lgcov_main(["guard", "--record"]) == 0
    written = Path(LEDGER).read_bytes()
    before = _cases()
    assert [c["candidates"] for c in before.values()] == [[first], [first]]
    assert [c["rounds"] for c in before.values()] == [[1], [1]]

    # The next round: a new candidate, and a case set with one case replaced.
    _edit_a_case()
    second = _commit(tmp_path, "the next candidate")
    _edit_round(candidates=[second])
    assert lgcov_main(["round", "new"]) == 0
    assert _round()["round"] == 2
    assert Path(LEDGER).read_bytes() == written

    assert lgcov_main(["guard", "--record"]) == 0
    after = _cases()
    replaced = [c for c, seen in after.items() if seen["rounds"] == [1]]
    kept = [c for c, seen in after.items() if seen["rounds"] == [1, 2]]
    added = [c for c, seen in after.items() if seen["rounds"] == [2]]
    assert len(replaced) == len(kept) == len(added) == 1
    assert after[replaced[0]] == before[replaced[0]]  # no longer in the set, and not forgotten
    assert after[kept[0]]["candidates"] == sorted([first, second])
    assert after[added[0]]["candidates"] == [second]


def _delete_the_ledger() -> None:
    Path(LEDGER).unlink()


def _reset_the_ledger() -> None:
    Path(LEDGER).write_text('{"version": 1, "cases": {}}\n', encoding="utf-8")


@pytest.mark.parametrize("damage", [_delete_the_ledger, _reset_the_ledger], ids=["gone", "reset"])
def test_a_lost_ledger_exits_2_and_a_new_round_cant_start_over_it(
    damage: Callable[[], None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _locked(tmp_path, monkeypatch)
    assert lgcov_main(["guard", "--record"]) == 0
    assert lgcov_main(["round", "new"]) == 0
    assert lgcov_main(["guard"]) == 0
    locked = Path(ROUND).read_bytes()
    damage()
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 2
    assert "- exposure history lost: " in capsys.readouterr().err
    assert lgcov_main(["round", "new"]) == 2
    assert "exposure history lost: " in capsys.readouterr().err
    assert Path(ROUND).read_bytes() == locked


# --- What else the lock holds.


def test_guard_passes_while_the_round_holds_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _locked(tmp_path, monkeypatch)
    files = Path(ROUND).read_bytes(), Path(LEDGER).read_bytes()
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 0
    out = capsys.readouterr().out
    assert f"🔒 Locked for this run: candidate `{head[:7]}`." in out
    assert "Round 1 holds: 2 cases, K_gate = 1, judge jev-test-1@" in out
    assert (Path(ROUND).read_bytes(), Path(LEDGER).read_bytes()) == files


def test_round_new_computes_the_plan_from_the_gates_own_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _project(tmp_path, monkeypatch)
    Path("lgcov.yaml").write_text(
        CONFIG + "gate:\n  alpha: 0.01\n  power: 0.9\n  margin: 0.03\n", encoding="utf-8"
    )
    _replace(TAXONOMY, "    judge: code\n", "    judge: code\n    margin: 0.02\n")
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    doc = _round()
    assert doc["round"] == 1
    assert (doc["alpha"], doc["power"], doc["k_gate"]) == (0.01, 0.9, 1)
    assert doc["kpis"] == [
        {"name": "respond_runs", "tags": ["flow"], "margin": 0.02},
        {"name": "answer_states_order_status", "tags": [], "margin": 0.03},
    ]
    assert (doc["delta_1"], doc["prior_rho"]) == (0.0, 0.6)
    assert doc["candidates"] == [head]
    assert doc["stop_rules"] == {"max_cases": 800}
    assert doc["exposure_floor"] == 0
    assert json.loads(Path(LEDGER).read_text(encoding="utf-8")) == {"version": 1, "cases": {}}


def test_a_margin_of_zero_is_locked_as_zero_as_the_gate_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _project(tmp_path, monkeypatch)
    _replace(TAXONOMY, "    judge: code\n", "    judge: code\n    margin: 0\n")
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    assert _round()["kpis"] == [
        {"name": "respond_runs", "tags": ["flow"], "margin": 0.0},
        {"name": "answer_states_order_status", "tags": [], "margin": 0.05},
    ]


@pytest.mark.parametrize(
    "fields",
    [{"delta_1": 0.5}, {"prior_rho": 0.1}, {"stop_rules": {"max_cases": 10}}, {"alpha": 0.5}],
    ids=["delta_1", "prior_rho", "stop_rules", "alpha"],
)
def test_a_hand_edit_to_the_round_file_exits_2(
    fields: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _locked(tmp_path, monkeypatch)
    _edit_round(**fields)
    assert lgcov_main(["guard"]) == 2
    assert "its digest doesn't match" in capsys.readouterr().err


def test_a_candidate_added_by_hand_isnt_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _locked(tmp_path, monkeypatch)
    _edit_round(candidates=[head, OTHER_SHA])
    assert lgcov_main(["guard", "--candidate", OTHER_SHA]) == 2
    assert "its digest doesn't match" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("setting", "named"),
    [
        ("alpha: 0.1", "- alpha: locked 0.05, now 0.1"),
        ("power: 0.9", "- power: locked 0.8, now 0.9"),
        ("margin: 0.01", "- KPI respond_runs margin: locked 0.05, now 0.01"),
        ("advisory_kpis: [respond_runs]", "- K_gate: locked 1, now 0"),
    ],
    ids=["alpha", "power", "margin", "k_gate"],
)
def test_guard_exits_2_when_lgcov_yaml_moved_a_locked_number(
    setting: str,
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _locked(tmp_path, monkeypatch)
    Path("lgcov.yaml").write_text(CONFIG + f"gate:\n  {setting}\n", encoding="utf-8")
    assert lgcov_main(["guard"]) == 2
    assert named in capsys.readouterr().err


def test_guard_exits_2_when_a_kpis_own_margin_moved_and_its_prompt_didnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    _replace(TAXONOMY, "    judge: code\n", "    judge: code\n    margin: 0.01\n")
    assert lgcov_main(["guard"]) == 2
    err = capsys.readouterr().err
    assert "- KPI respond_runs margin: locked 0.05, now 0.01" in err
    assert "prompt" not in err


def test_guard_exits_2_when_a_new_band_moved_k_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    _make_the_fake_kpi_block()
    assert lgcov_main(["guard"]) == 2
    assert "- K_gate: locked 1, now 2" in capsys.readouterr().err


def test_a_kpi_added_or_removed_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    _replace(TAXONOMY, "- name: respond_runs", "- name: respond_always_runs")
    assert lgcov_main(["guard"]) == 2
    err = capsys.readouterr().err
    assert "- KPI respond_runs: in the round, not in the taxonomy" in err
    assert "- KPI respond_always_runs: in the taxonomy, not in the round" in err


@pytest.mark.parametrize(
    ("setting", "blocking"),
    [
        ("", 1),
        ("gate:\n  advisory_kpis: [respond_runs]\n", 0),
        ("gate:\n  uncalibrated: flag\n", 2),
    ],
    ids=["code judge only", "none", "flag band counts"],
)
def test_k_gate_is_the_number_lgcov_judge_prints(
    setting: str,
    blocking: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    head = _project(tmp_path, monkeypatch)
    shutil.copy(FIXTURES / "demo_graph.py", "graph.py")
    Path("lgcov.yaml").write_text(CONFIG + setting, encoding="utf-8")
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    assert _round()["k_gate"] == blocking
    capsys.readouterr()
    assert lgcov_main(["judge"]) == 0
    assert set(re.findall(r"K_gate = (\d+)", capsys.readouterr().out)) == {str(blocking)}


def test_a_rebaseline_or_a_new_order_of_the_same_cases_doesnt_trip_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _locked(tmp_path, monkeypatch)

    def rebaseline(doc: dict[str, list[dict[str, object]]]) -> None:
        doc["runs"][0]["steps"] = []

    def reorder(doc: dict[str, list[dict[str, object]]]) -> None:
        doc["runs"].reverse()

    _edit_baseline(rebaseline)
    assert lgcov_main(["guard"]) == 0
    _edit_baseline(reorder)
    assert lgcov_main(["guard"]) == 0


def test_guard_record_is_the_one_thing_that_writes_the_ledger_and_repeating_it_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _locked(tmp_path, monkeypatch)
    assert lgcov_main(["guard"]) == 0
    assert _cases() == {}
    capsys.readouterr()
    assert lgcov_main(["guard", "--record"]) == 0
    assert f"{LEDGER}: candidate `{head[:7]}` and judge jev-test-1@" in capsys.readouterr().out
    once = Path(LEDGER).read_bytes()
    assert lgcov_main(["guard", "--record"]) == 0
    assert Path(LEDGER).read_bytes() == once
    judges = {j for c in _cases().values() for j in c["judges"]}
    assert len(judges) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_guard_records_nothing_when_it_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _locked(tmp_path, monkeypatch)
    assert lgcov_main(["guard", "--record", "--candidate", OTHER_SHA]) == 2
    assert _cases() == {}


# --- On a PR: the round and the policy come from the base, the cases from the PR's checkout.


def _base_and_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A base checkout holding a locked round, whose candidate is the PR checkout's HEAD. The PR
    checkout is the working directory; returns the base."""
    head = _project(tmp_path / "pr", monkeypatch)
    base = tmp_path / "base"
    shutil.copytree(tmp_path / "pr", base, ignore=shutil.ignore_patterns(".git"))
    monkeypatch.chdir(base)
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    monkeypatch.chdir(tmp_path / "pr")
    return base


def test_policy_dir_is_where_the_round_and_policy_come_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 0
    assert not Path(ROUND).exists()
    # A PR's edits to its copy of the policy don't apply until it merges, as in `lgcov ci`.
    _edit_the_model()
    _edit_a_rubric()
    _make_the_fake_kpi_block()
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 0


def test_policy_dir_reads_the_bases_bands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    monkeypatch.chdir(base)
    _make_the_fake_kpi_block()
    monkeypatch.chdir(tmp_path / "pr")
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 2
    assert "- K_gate: locked 1, now 2" in capsys.readouterr().err


def test_a_prs_own_round_file_cant_lock_it_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    pushed = _commit(tmp_path / "pr", "a push the base's round doesn't name")
    _draft(pushed)
    assert lgcov_main(["round", "new"]) == 0  # the PR locks itself, in its own copy
    assert lgcov_main(["guard"]) == 0
    capsys.readouterr()
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 2
    assert f"candidate `{pushed[:7]}` is not locked" in capsys.readouterr().err


@pytest.mark.parametrize("missing", [ROUND, "lgcov.yaml", TAXONOMY])
def test_policy_dir_has_no_fallback_to_the_prs_files(
    missing: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    shutil.copy(base / ROUND, ROUND)  # the PR's copies are all there, and aren't used
    (base / missing).unlink()
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 2
    assert missing in capsys.readouterr().err


def test_policy_dir_still_catches_a_prs_edit_to_the_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    _edit_a_case()
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 2
    assert "- gate case set: locked " in capsys.readouterr().err


def test_policy_dir_reads_the_bases_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    candidate = str(_round_of(base)["candidates"][0])
    monkeypatch.chdir(base)
    assert lgcov_main(["guard", "--record", "--candidate", candidate]) == 0
    assert lgcov_main(["round", "new"]) == 0
    monkeypatch.chdir(tmp_path / "pr")
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 0
    assert not Path(LEDGER).exists()
    (base / LEDGER).unlink()
    capsys.readouterr()
    assert lgcov_main(["guard", "--policy-dir", str(base)]) == 2
    assert "- exposure history lost: " in capsys.readouterr().err


def _round_of(base: Path) -> dict[str, list[object]]:
    doc: dict[str, list[object]] = yaml.safe_load((base / ROUND).read_text(encoding="utf-8"))
    return doc


def test_record_refuses_a_checkout_that_will_be_thrown_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_and_pr(tmp_path, monkeypatch)
    ledger = (base / LEDGER).read_bytes()
    assert lgcov_main(["guard", "--record", "--policy-dir", str(base)]) == 2
    assert "--record can't run with --policy-dir" in capsys.readouterr().err
    assert (base / LEDGER).read_bytes() == ledger
    assert not Path(LEDGER).exists()


# --- `round new` refuses a plan it can't lock.


@pytest.mark.parametrize("config", ["", "jev_model: jev-latest\n"], ids=["none", "latest"])
def test_round_new_refuses_a_jev_judge_with_no_pinned_model(
    config: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    head = _project(tmp_path, monkeypatch)
    _replace(TAXONOMY, "judge: fake", "judge: jev")
    Path("lgcov.yaml").write_text(
        CONFIG.replace("jev_model: jev-test-1\n", config), encoding="utf-8"
    )
    _draft(head)
    draft = Path(ROUND).read_bytes()
    assert lgcov_main(["round", "new"]) == 2
    err = capsys.readouterr().err
    assert "answer_states_order_status" in err
    assert "pin jev_model" in err
    assert Path(ROUND).read_bytes() == draft
    assert not Path(LEDGER).exists()


def test_round_new_needs_no_model_when_no_kpi_uses_jev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _project(tmp_path, monkeypatch)
    Path("lgcov.yaml").write_text(CONFIG.replace("jev_model: jev-test-1\n", ""), encoding="utf-8")
    _draft(head)
    assert lgcov_main(["round", "new"]) == 0
    assert lgcov_main(["guard"]) == 0


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"candidates": []}, "candidates"),
        ({"candidates": ["abc1234"]}, "candidates"),
        ({"kpis": [{"name": "no_such_kpi"}]}, "no_such_kpi"),
        ({"gate_cases": {"path": "cassettes/none.json"}}, "cassettes/none.json"),
        ({"prior_rho": 2}, "prior_rho"),
    ],
    ids=["no candidates", "short sha", "unknown kpi", "no cases file", "rho out of range"],
)
def test_round_new_refuses_a_bad_draft(
    fields: dict[str, object],
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    head = _project(tmp_path, monkeypatch)
    _draft(head)
    _edit_round(**fields)
    assert lgcov_main(["round", "new"]) == 2
    assert named in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["guard"], ["round", "new"]], ids=["guard", "round new"])
def test_no_round_file_or_a_broken_one_exits_2(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _project(tmp_path, monkeypatch)
    assert lgcov_main(argv) == 2
    assert ROUND in capsys.readouterr().err
    Path(ROUND).write_text("version: [", encoding="utf-8")
    assert lgcov_main(argv) == 2
    assert f"{ROUND}: malformed YAML" in capsys.readouterr().err


def test_a_broken_ledger_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)
    Path(LEDGER).write_text("[]", encoding="utf-8")
    assert lgcov_main(["guard"]) == 2
    assert LEDGER in capsys.readouterr().err


def test_a_round_needs_a_taxonomy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _project(tmp_path, monkeypatch)
    Path("lgcov.yaml").write_text(CONFIG.replace("taxonomy_path: taxonomy.yaml\n", ""))
    _draft(head)
    assert lgcov_main(["round", "new"]) == 2
    assert "needs a taxonomy" in capsys.readouterr().err
    assert lgcov_main(["round", "new", "--taxonomy", TAXONOMY]) == 0
    assert lgcov_main(["guard", "--taxonomy", TAXONOMY]) == 0


def test_guard_exits_2_when_the_reference_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T158: round 2 points at round 1's digest, round 3 keeps it, and a hand edit exits 2."""
    _locked(tmp_path, monkeypatch)
    first = _round()
    assert "reference" not in first
    for number in (2, 3):
        assert lgcov_main(["round", "new"]) == 0
        assert _round()["round"] == number
        assert _round()["reference"] == {"round": 1, "digest": first["digest"]}
    assert lgcov_main(["guard"]) == 0
    _edit_round(reference={"round": 2, "digest": first["digest"]})
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 2
    assert "its digest doesn't match" in capsys.readouterr().err


def test_lgcov_round_alone_says_what_it_takes(capsys: pytest.CaptureFixture[str]) -> None:
    assert lgcov_main(["round"]) == 2
    assert "lgcov round new" in capsys.readouterr().err


def test_guard_warns_of_uncommitted_changes_the_lock_cant_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _locked(tmp_path, monkeypatch)  # the round file itself is uncommitted here
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 0
    assert "⚠️ uncommitted changes, not covered by the lock" in capsys.readouterr().out
