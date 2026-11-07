"""T114: the commits a gate run compares, locked from its start to its end (pure)."""

from lgcov.lock import Commits, locked_line, moved

A, B, C = "a" * 40, "b" * 40, "c" * 40


def test_nothing_moved_is_no_refusal_and_the_line_records_what_was_compared() -> None:
    start = Commits(A, B)
    assert moved(start, Commits(A, B)) == ""
    assert locked_line(start) == "🔒 Locked for this run: base `aaaaaaa`, candidate `bbbbbbb`."
    assert locked_line(Commits(None, B)) == "🔒 Locked for this run: candidate `bbbbbbb`."
    assert locked_line(Commits(None, None)) == "🔓 Not locked: not a git checkout."
    assert locked_line(Commits(A, B, dirty=True)) == (
        "🔒 Locked for this run: base `aaaaaaa`, candidate `bbbbbbb`. "
        "⚠️ uncommitted changes, not covered by the lock"
    )


def test_a_side_that_moved_or_went_unreadable_is_refused_by_name() -> None:
    start = Commits(A, B)
    assert moved(start, Commits(A, C)) == (
        "Locked commits moved during the run: the candidate was `bbbbbbb` at the start and "
        "is `ccccccc` now. The run's verdicts would mix two versions of the code, so it gives "
        "none; re-run on one commit."
    )
    base = moved(start, Commits(C, B))
    assert "the base was `aaaaaaa` at the start and is `ccccccc` now" in base
    gone = moved(start, Commits(A, None))
    assert "the candidate was `bbbbbbb` at the start and is unreadable now" in gone
    assert moved(start, Commits(A, B, dirty=True)) == ""  # uncommitted changes only warn
