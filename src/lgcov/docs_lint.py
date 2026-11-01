"""T116: the docs lint that pre-commit and CI run as `python -m lgcov.docs_lint`.

Two checks over the repo's tracked files:

- No phrase listed in docs/banned-phrases.txt appears in any of them, in any case, with or
  without its hyphen. The list itself is the one file exempt; binary files are skipped.
- README.md's opening paragraph has docs/why-us.md's tagline, the first sentence of that
  file's opening paragraph, word for word (line breaks don't count).
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PHRASES = "docs/banned-phrases.txt"
README, WHY_US = "README.md", "docs/why-us.md"
# Where a phrase has a hyphen or a space: a hyphen, a Unicode hyphen or dash (U+2010 to
# U+2015), whitespace (a line break too), or nothing.
_JOIN = r"[-‐-―\s]*"


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """The phrase as whole words, in any case, each hyphen or space in it written any
    _JOIN way."""
    words = [re.escape(w) for w in re.split(r"[-\s]+", phrase.strip()) if w]
    return re.compile(rf"\b{_JOIN.join(words)}\b", re.IGNORECASE)


def read_phrases(text: str) -> list[str]:
    """One phrase per line; blank lines and # comments don't count."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def find(text: str, patterns: Sequence[re.Pattern[str]]) -> list[tuple[int, str]]:
    """Each hit's line number and text, in the order they appear."""
    found = sorted((m.start(), m.group()) for p in patterns for m in p.finditer(text))
    return [(text.count("\n", 0, start) + 1, hit) for start, hit in found]


def _opening(markdown: str) -> str:
    """The first paragraph under the title, its whitespace collapsed."""
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.strip() and not line.startswith("#"):
            lines.append(line)
        elif lines:
            break
    return " ".join(" ".join(lines).split())


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", paragraph) if s]


def tagline(markdown: str) -> str:
    """The first sentence of the opening paragraph; '' if there is none."""
    return next(iter(_sentences(_opening(markdown))), "")


def tagline_problem(readme: str, why_us: str) -> str:
    """'' when the README's opening paragraph has why-us.md's tagline, word for word; else
    both versions, the README's being its sentence closest to the tagline."""
    want, opening = tagline(why_us), _opening(readme)
    if not want:
        return f"{WHY_US}: no opening paragraph to take the tagline from"
    if want in opening:
        return ""
    closest = max(
        _sentences(opening),
        key=lambda s: difflib.SequenceMatcher(None, s, want).ratio(),
        default="",
    )
    return (
        f"{README}: its opening paragraph doesn't have {WHY_US}'s tagline, word for word\n"
        f"  {WHY_US + ':':<16}{want}\n"
        f"  {README + ':':<16}{closest}"
    )


def lint(root: Path, files: Sequence[str]) -> list[str]:
    """The problems in these tracked files (paths relative to root), one line each."""
    phrases = read_phrases((root / PHRASES).read_text(encoding="utf-8"))
    patterns = [phrase_pattern(p) for p in phrases]
    problems: list[str] = []
    for path in files:
        file = root / path
        if path == PHRASES or not file.is_file():
            continue  # the list itself is the one exemption; a deleted file has no text
        data = file.read_bytes()
        if b"\0" in data:
            continue  # binary
        for line, hit in find(data.decode("utf-8", errors="replace"), patterns):
            problems.append(f"{path}:{line}: banned phrase {hit!r} ({PHRASES})")
    docs = {p: (root / p).read_text(encoding="utf-8") for p in (README, WHY_US)}
    mismatch = tagline_problem(docs[README], docs[WHY_US])
    return [*problems, *([mismatch] if mismatch else [])]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    """Lint the checkout this runs in: exit 1 on any problem, 2 when it can't run."""
    try:
        root = Path(_git("rev-parse", "--show-toplevel").strip())
        files = [f for f in _git("-C", str(root), "ls-files", "-z").split("\0") if f]
        problems = lint(root, files)
    except (OSError, subprocess.CalledProcessError) as exc:  # no checkout, list or docs
        print(f"docs lint: can't run: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("\n".join(problems))
        return 1
    print(f"docs lint: {len(files)} files, no banned phrase; {README}'s tagline is {WHY_US}'s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
