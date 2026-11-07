"""T119: a benchmark round's locked SHAs and its exposure ledger (pure). The fixture is the
open_deep_research #163 round: one candidate, two cases, one of them already seen."""

from pathlib import Path

import pytest

from lgcov.bench_round import (
    LedgerRow,
    Round,
    check,
    exposures,
    ledger_lines,
    lock_round,
    new_round,
    parse_ledger,
    parse_round,
    round_to_yaml,
    tree_state,
)
from lgcov.synthetic_score import replace_section

FIXTURE = Path(__file__).parent / "fixtures" / "rounds" / "odr-163"
ROUND = (FIXTURE / "lgcov-round.yaml").read_text(encoding="utf-8")
LEDGER = (FIXTURE / "exposure.jsonl").read_text(encoding="utf-8")
HEAD = "6532a4176a93cc9bb2102b3d825dcefa560c85d9"


def _rows() -> tuple[LedgerRow, ...]:
    return parse_ledger(LEDGER)


def test_a_clean_round_passes() -> None:
    assert check(parse_round(ROUND), parse_ledger(LEDGER)) == ()


def test_a_locked_sha_that_changed_fails() -> None:
    moved = parse_round(ROUND.replace(HEAD, "a" * 40))
    problems = check(moved, parse_ledger(LEDGER))
    assert len(problems) == 1
    assert "open_deep_research-163" in problems[0]
    assert HEAD in problems[0] and "a" * 40 in problems[0]


def test_a_case_already_exposed_to_the_candidate_in_an_earlier_round_fails() -> None:
    next_round = parse_round(ROUND.replace("id: t101-163", "id: t101-163-b"))
    lock = LEDGER.splitlines()[0].replace('"round": "t101-163"', '"round": "t101-163-b"')
    problems = check(next_round, parse_ledger(f"{LEDGER}{lock}\n"))
    assert len(problems) == 1
    assert "eu-ai-act" in problems[0] and "t101-163" in problems[0]


def _next_round(text: str = ROUND) -> tuple[Round, str]:
    """The same round under a new id, with its own lock row after the fixture's ledger."""
    lock = LEDGER.splitlines()[0].replace('"round": "t101-163"', '"round": "t101-163-b"')
    return parse_round(text.replace("id: t101-163", "id: t101-163-b")), f"{LEDGER}{lock}\n"


def test_a_round_that_isnt_frozen_fails() -> None:
    problems = check(parse_round(ROUND.replace("frozen: true", "frozen: false")), _rows())
    assert ["not frozen" in p for p in problems] == [True]


def test_a_candidate_with_no_lock_row_fails() -> None:
    problems = check(parse_round(ROUND), parse_ledger(""))
    assert ["no lock row" in p for p in problems] == [True]


def test_a_locked_candidate_gone_from_the_file_fails() -> None:
    gone = parse_round(ROUND).model_copy(update={"candidates": ()})
    problems = check(gone, _rows())
    assert any("open_deep_research-163" in p and "no longer" in p for p in problems)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("f95d200654b5c4733e87375be15b92822c6c4ff8", "b" * 40),
        ("repo: langchain-ai/open_deep_research", "repo: someone/fork"),
        ("locked_at: '2026-11-07'", "locked_at: '2026-11-08'"),
    ],
)
def test_any_locked_field_that_changed_fails(old: str, new: str) -> None:
    assert len(check(parse_round(ROUND.replace(old, new)), _rows())) == 1


def test_a_second_lock_row_cannot_replace_the_first() -> None:
    relock = LEDGER.splitlines()[0].replace(HEAD, "a" * 40)
    moved = parse_round(ROUND.replace(HEAD, "a" * 40))
    assert len(check(moved, parse_ledger(f"{LEDGER}{relock}\n"))) >= 1


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("- id: model-prices", "- id: another-case"),
        ("#cases[0]", "#cases[9]"),
    ],
)
def test_cases_changed_after_the_lock_fail(old: str, new: str) -> None:
    problems = check(parse_round(ROUND.replace(old, new)), _rows())
    assert ["cases changed" in p for p in problems] == [True]


def test_a_run_on_a_commit_other_than_the_locked_one_fails() -> None:
    row = LEDGER.splitlines()[1].replace(HEAD, "a" * 40)
    problems = check(parse_round(ROUND), parse_ledger(f"{LEDGER}{row}\n"))
    assert len(problems) == 1
    assert "a" * 40 in problems[0]


def test_a_run_of_a_case_outside_the_round_fails() -> None:
    row = LEDGER.splitlines()[1].replace("eu-ai-act", "stray-case")
    problems = check(parse_round(ROUND), parse_ledger(f"{LEDGER}{row}\n"))
    assert ["stray-case" in p for p in problems] == [True]


def test_a_repeat_run_inside_the_round_passes() -> None:
    row = LEDGER.splitlines()[1].replace('"pilot"', '"rerun"')
    assert check(parse_round(ROUND), parse_ledger(f"{LEDGER}{row}\n")) == ()


def test_a_new_sha_does_not_escape_re_exposure() -> None:
    next_round, ledger = _next_round(ROUND.replace(HEAD, "a" * 40))
    first, seen, lock = ledger.splitlines()
    rows = parse_ledger("\n".join([first, seen, lock.replace(HEAD, "a" * 40)]))
    problems = check(next_round, rows)
    assert ["eu-ai-act" in p for p in problems] == [True]


def test_a_rename_does_not_escape_re_exposure() -> None:
    next_round, ledger = _next_round()
    renamed = next_round.model_copy(
        update={
            "candidates": tuple(
                c.model_copy(update={"name": "renamed"}) for c in next_round.candidates
            )
        }
    )
    lock = ledger.splitlines()[2].replace("open_deep_research-163", "renamed")
    rows = parse_ledger("\n".join([*ledger.splitlines()[:2], lock]))
    assert any("eu-ai-act" in p for p in check(renamed, rows))


def test_a_later_round_on_only_the_unseen_case_passes() -> None:
    next_round, ledger = _next_round()
    unseen = next_round.model_copy(update={"cases": next_round.cases[:1]})
    lock = ledger.splitlines()[2].replace(
        ', {"id": "eu-ai-act", "source": "docs/benchmark/open_deep_research-163.yaml#cases[1]"}',
        "",
    )
    rows = parse_ledger("\n".join([*ledger.splitlines()[:2], lock]))
    assert check(unseen, rows) == ()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (HEAD, "6532a41"),  # a short SHA can stop meaning one commit
        (HEAD, HEAD.upper()),
        (HEAD, "1" * 40),  # unquoted, YAML reads it as a number: refused, never misread
        ("frozen: true", "frozen: 'yes'"),
        ("frozen: true", "frozen: true\nextra_key: 1"),
        ("created_at: '2026-11-07'", "created_at: last week"),
        ("- id: model-prices", "- id: eu-ai-act"),
    ],
)
def test_a_broken_round_is_refused(old: str, new: str) -> None:
    with pytest.raises(ValueError):
        parse_round(ROUND.replace(old, new))


@pytest.mark.parametrize("text", ["", "[]", "a: [", "id: x"])
def test_a_file_that_is_no_round_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        parse_round(text)


def test_an_unquoted_yaml_date_is_kept_as_text() -> None:
    assert parse_round(ROUND.replace("'2026-11-07'", "2026-11-07")) == parse_round(ROUND)


def test_a_broken_ledger_line_is_named() -> None:
    with pytest.raises(ValueError, match="line 3"):
        parse_ledger(f'{LEDGER}{{"event": "nonsense"}}\n')


def test_lock_freezes_the_round_and_writes_one_row_per_candidate() -> None:
    draft = parse_round(ROUND.replace("frozen: true", "frozen: false"))
    locked, rows = lock_round(draft, (), "2026-11-07")
    assert locked == parse_round(ROUND)
    assert rows == parse_ledger(LEDGER)[:1]
    assert check(locked, rows) == ()


@pytest.mark.parametrize(
    ("round_text", "ledger", "why"),
    [
        (ROUND, "", "already frozen"),
        (ROUND.replace("frozen: true", "frozen: false"), LEDGER, "already locked"),
    ],
)
def test_lock_refuses_a_locked_round(round_text: str, ledger: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        lock_round(parse_round(round_text), parse_ledger(ledger), "2026-11-07")


def test_lock_refuses_a_round_with_no_candidates_or_cases() -> None:
    with pytest.raises(ValueError, match="candidate"):
        lock_round(new_round("r1", "2026-11-07"), (), "2026-11-07")


def test_exposures_gives_one_row_per_candidate_and_case() -> None:
    rows = exposures(parse_round(ROUND), "run-2", "2026-11-08")
    assert [(r.candidate, r.case, r.run_id, r.head_sha, r.seen_at) for r in rows] == [
        ("open_deep_research-163", "model-prices", "run-2", HEAD, "2026-11-08"),
        ("open_deep_research-163", "eu-ai-act", "run-2", HEAD, "2026-11-08"),
    ]


def test_the_files_round_trip() -> None:
    assert parse_round(round_to_yaml(parse_round(ROUND))) == parse_round(ROUND)
    assert ledger_lines(parse_ledger(LEDGER)) == LEDGER


def test_tree_state_sorts_git_status_by_what_lock_does_with_each_file() -> None:
    status = "\0".join(
        [
            " M src/a.py",
            "R  new name.py",
            "old name.py",
            "?? rounds/r1/stray.txt",
            "?? rounds/r10/other.txt",
            "?? .DS_Store",
            "",
        ]
    )
    state = tree_state(status, "rounds/r1/")
    assert state.tracked_changes == ("src/a.py", "new name.py")
    assert state.untracked_in_folder == ("rounds/r1/stray.txt",)
    assert state.untracked_elsewhere == ("rounds/r10/other.txt", ".DS_Store")
    assert tree_state("?? .DS_Store\0", "").untracked_in_folder == (".DS_Store",)


def test_regenerating_the_synthetic_section_keeps_rounds_at_the_end_of_the_doc() -> None:
    doc = (FIXTURE.parents[2].parent / "docs" / "benchmark.md").read_text(encoding="utf-8")
    new = replace_section(doc, "## Synthetic\n\nnew\n")
    assert new.index("## Synthetic") < new.index("## Real PRs") < new.index("## Rounds")
    assert new.endswith(doc[doc.index("## Rounds") :])
