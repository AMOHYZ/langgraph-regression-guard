"""T186: the review commands a maintainer posts on a PR (pure: a comment in, a command out).

docs/review.md is the grammar, and tests/test_review_command.py reads its two tables of example
lines, so the doc and this parser can't drift apart. A refusal's message is the reason the bot
gives on the PR, so it never repeats text that isn't a valid name: a mention or a link in it would
land in the reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "/lgcov "
MAX_LINE = 300  # characters in the first line
MAX_NODES = 20
VERBS = ("accept", "override", "reject")
FLAGS = ("--kpi", "--case", "--label")  # override's, in the order a missing one is named
# The first of two checks on who may command the bot, as the workflow's job `if` makes it. Each
# can include read-only people, so GitHub's permission check comes second.
ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")

_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_CASE = re.compile(r"run-(?:0|[1-9][0-9]{0,5})/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_NAME_RULE = "1 to 100 letters, digits, `_`, `.` or `-`, not starting with `.` or `-`"
_OVERRIDE_TAKES = "`override` takes `--kpi`, `--case` and `--label`"


@dataclass(frozen=True)
class Accept:
    """The named nodes' outputs on the PR's head become the baseline. None: every node that
    drifts, which the comment writes as `all`."""

    nodes: tuple[str, ...] | None


@dataclass(frozen=True)
class Override:
    """The judge was wrong on one case."""

    kpi: str
    case: str  # run-<N>/<node>, as the report names it
    label: str  # what the judge should have said


@dataclass(frozen=True)
class Reject:
    """The PR's cassette_dir goes back to the base's files."""


Command = Accept | Override | Reject


class CommandError(ValueError):
    """The first line starts `/lgcov ` but isn't a command. The message is the bot's reason."""


def parse_command(body: str) -> Command | None:
    """The command on a comment's first line. None if the comment doesn't start with `/lgcov `:
    that runs nothing and gets no reply. CommandError if it does, but isn't a command."""
    line = body.split("\n", 1)[0].removesuffix("\r")
    if not line.startswith(PREFIX):
        return None
    if not line.isascii():
        raise CommandError("It isn't ASCII")
    if not line.isprintable():
        raise CommandError("It holds a tab or another control character")
    if len(line) > MAX_LINE:
        raise CommandError(f"It is longer than {MAX_LINE} characters")
    words = line[len(PREFIX) :].split()
    if not words:
        raise CommandError("It names no command")
    verb, args = words[0], words[1:]
    if verb == "accept":
        return _accept(args)
    if verb == "override":
        return _override(args)
    if verb == "reject":
        if args:
            raise CommandError("`reject` takes nothing")
        return Reject()
    if verb.lower() in VERBS:
        raise CommandError("Commands are lowercase")
    raise CommandError("There is no such command")


def _accept(args: list[str]) -> Accept:
    if not args:
        raise CommandError("It names no node")
    if len(args) > 1:
        raise CommandError("A space inside the list")
    nodes = args[0].split(",")
    if nodes == ["all"]:
        return Accept(None)
    if "all" in nodes:
        raise CommandError("`all` stands on its own")
    if len(nodes) > MAX_NODES:
        raise CommandError(f"At most {MAX_NODES} nodes")
    for node in nodes:
        if not node:
            raise CommandError("A name in the list is empty")
        if node.startswith("-"):
            raise CommandError("A flag isn't a node's name")
        if not _NAME.fullmatch(node):
            raise CommandError(f"A node's name is {_NAME_RULE}")
    twice = next((node for i, node in enumerate(nodes) if node in nodes[:i]), None)
    if twice:
        raise CommandError(f"`{twice}` is named twice")
    return Accept(tuple(nodes))


def _override(args: list[str]) -> Override:
    given: dict[str, str] = {}
    words = iter(args)
    for word in words:
        flag, equals, value = word.partition("=")
        if flag not in FLAGS:
            raise CommandError(_OVERRIDE_TAKES)
        if flag in given:
            raise CommandError(f"`{flag}` is given twice")
        if not equals:
            value = next(words, "")
        if not value or value.startswith("--"):
            raise CommandError(f"`{flag}` has no value")
        given[flag] = value
    missing = next((flag for flag in FLAGS if flag not in given), None)
    if missing:
        raise CommandError(f"There is no `{missing}`")
    for flag in ("--kpi", "--label"):
        if not _NAME.fullmatch(given[flag]):
            raise CommandError(f"`{flag}` takes a name of {_NAME_RULE}")
    if not _CASE.fullmatch(given["--case"]):
        raise CommandError("A case is `run-<N>/<node>`")
    return Override(given["--kpi"], given["--case"], given["--label"])


@dataclass(frozen=True)
class CommentEvent:
    """What an `issue_comment` event says about its comment, read from $GITHUB_EVENT_PATH."""

    action: str  # created, edited or deleted
    repo: str  # owner/name
    pr: int | None  # the issue's number when it is a PR; None on an issue
    comment_id: int
    url: str  # the comment's page, which an override saves as its note
    body: str
    created_at: str  # as GitHub writes it: 2026-11-17T10:25:30Z
    author: str  # their login
    author_type: str  # User, Bot or Organization
    association: str  # OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE and the like


def _at(raw: object, path: str) -> object:
    for key in path.split("."):
        if not isinstance(raw, dict) or key not in raw:
            raise ValueError(f"the event has no {path}")
        raw = raw[key]
    return raw


def _text(raw: object, path: str) -> str:
    value = _at(raw, path)
    if not isinstance(value, str):
        raise ValueError(f"the event's {path} isn't text")
    return value


def _number(raw: object, path: str) -> int:
    value = _at(raw, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"the event's {path} isn't a whole number")
    return value


def comment_event(raw: object) -> CommentEvent:
    """ValueError unless raw is an `issue_comment` event with every field read here."""
    issue = _at(raw, "issue")
    # As the job's `if` reads it: an object makes it a PR, a missing key or null doesn't.
    on_pr = isinstance(issue, dict) and isinstance(issue.get("pull_request"), dict)
    return CommentEvent(
        action=_text(raw, "action"),
        repo=_text(raw, "repository.full_name"),
        pr=_number(raw, "issue.number") if on_pr else None,
        comment_id=_number(raw, "comment.id"),
        url=_text(raw, "comment.html_url"),
        body=_text(raw, "comment.body"),
        created_at=_text(raw, "comment.created_at"),
        author=_text(raw, "comment.user.login"),
        author_type=_text(raw, "comment.user.type"),
        association=_text(raw, "comment.author_association"),
    )


def may_command(event: CommentEvent) -> bool:
    """The first of two checks, as the workflow's job `if` makes it: a new comment on a PR by a
    person whose association can include push access. GitHub's permission check comes second."""
    return (
        event.action == "created"
        and event.pr is not None
        and event.author_type == "User"
        and event.association in ASSOCIATIONS
    )
