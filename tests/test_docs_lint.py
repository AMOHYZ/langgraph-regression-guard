"""T116: the docs lint: banned phrases anywhere in the repo, and the README's tagline."""

import subprocess
from pathlib import Path

import pytest

from lgcov.docs_lint import PHRASES, find, lint, main, phrase_pattern, tagline, tagline_problem

# Spelled in pieces: the repo-wide scan reads this file too, and must find nothing in it.
SELF, IMPROVING = "self", "improving"
PHRASE = f"{SELF}-{IMPROVING}"
TAGLINE = (
    "lgcov tells you which LangGraph node a PR actually changed and which ones only "
    "inherited the damage."
)


def _tree(root: Path, readme: str) -> list[str]:
    """The pattern file, docs/why-us.md and a README at root; the paths written."""
    (root / "docs").mkdir()
    (root / PHRASES).write_text(f"# the one file the lint exempts\n{PHRASE}\n", encoding="utf-8")
    (root / "docs" / "why-us.md").write_text(f"# Why\n\n{TAGLINE} More.\n", encoding="utf-8")
    (root / "README.md").write_text(readme, encoding="utf-8")
    return [PHRASES, "docs/why-us.md", "README.md"]


def test_a_phrase_matches_in_any_case_with_or_without_its_hyphen() -> None:
    pattern = phrase_pattern(PHRASE)
    for text in (
        PHRASE.title(),
        f"{SELF} {IMPROVING}".upper(),
        f"{SELF}{IMPROVING}",
        f"{SELF}\u2011{IMPROVING}",  # a non-breaking hyphen
        f"{SELF}\u2013{IMPROVING}",  # an en dash
        f"a {SELF}-\n{IMPROVING} agent",  # split by a line wrap
    ):
        assert pattern.search(text), text
    for text in (f"my{SELF} {IMPROVING}", f"{SELF}-improvement", f"{SELF}_{IMPROVING}"):
        assert not pattern.search(text), text


def test_find_gives_each_hit_its_line() -> None:
    spaced = f"{SELF} {IMPROVING}".upper()
    text = f"one\ntwo {PHRASE}\nthree {spaced}\n"
    assert find(text, [phrase_pattern(PHRASE)]) == [(2, PHRASE), (3, spaced)]


def test_lint_flags_the_phrase_anywhere_but_its_own_list_and_binary_files(tmp_path: Path) -> None:
    files = _tree(tmp_path, f"# x\n\nIntro. {TAGLINE}\n\nAn {PHRASE.title()} loop.\n")
    (tmp_path / "a.py").write_text(f"x = '{SELF} {IMPROVING}'\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\0" + PHRASE.encode())
    assert lint(tmp_path, [*files, "a.py", "logo.png"]) == [
        f"README.md:5: banned phrase {PHRASE.title()!r} ({PHRASES})",
        f"a.py:1: banned phrase {f'{SELF} {IMPROVING}'!r} ({PHRASES})",
    ]


def test_the_readme_must_have_why_us_tagline_word_for_word() -> None:
    why_us = f"# Why lgcov\n\n{TAGLINE} First report in 2\nminutes.\n\n## The problem\n"
    assert tagline(why_us) == TAGLINE  # the first sentence, not the whole paragraph
    wrapped = TAGLINE.replace(" only ", "\nonly ")
    assert tagline_problem(f"# r\n\nCatch regressions. {wrapped} An Action.\n", why_us) == ""
    drifted = TAGLINE.replace("LangGraph node", "node")
    problem = tagline_problem(f"# r\n\nCatch regressions. {drifted} An Action.\n", why_us)
    assert problem.splitlines() == [
        "README.md: its opening paragraph doesn't have docs/why-us.md's tagline, word for word",
        f"  docs/why-us.md: {TAGLINE}",
        f"  README.md:      {drifted}",
    ]


def test_main_lints_only_tracked_files_and_exits_1_on_a_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main() == 2  # not a git checkout: it can't say what's in the repo
    _tree(tmp_path, f"# x\n\nIntro. {TAGLINE}\n")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "add", "-A"], check=True)
    assert main() == 0
    (tmp_path / "notes.md").write_text(f"{PHRASE}\n", encoding="utf-8")
    assert main() == 0  # untracked, so not in the repo yet
    subprocess.run(["git", "add", "notes.md"], check=True)
    assert main() == 1
    assert f"notes.md:1: banned phrase {PHRASE!r} ({PHRASES})" in capsys.readouterr().out
