"""T186: the review commands a maintainer posts on a PR (pure: a comment in, a command out).

docs/review.md is the grammar, and tests/test_review_command.py reads its two tables of example
lines, so the doc and this parser can't drift apart. A refusal's message is the reason the bot
gives on the PR, so it never repeats text that isn't a valid name: a mention or a link in it would
land in the reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from lgcov.review import unwritable

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


# T186, PR 6: what `lgcov review fetch` checks before it reads the PR's files. The context is the
# API's answers, already read, so the rules are pure (docs/review.md, "When the bot refuses").
PERMITTED = ("admin", "maintain", "write")  # role names that can push


@dataclass(frozen=True)
class Pull:
    number: int
    state: str  # open or closed
    merged: bool
    head_repo: str  # owner/name; "" when the fork is gone
    head_ref: str
    head_sha: str
    base_sha: str
    default_branch: str


@dataclass(frozen=True)
class Report:
    """The newest `lgcov-candidate` artifact for the PR's head, and the run that made it."""

    artifact_id: int
    expired: bool
    artifact_updated: str  # as GitHub writes times: 2026-11-17T10:25:30Z
    run_status: str
    run_event: str
    run_head_sha: str
    run_updated: str
    base_sha: str  # the base the run graded against, whose policy the command reads


@dataclass(frozen=True)
class FetchContext:
    event: CommentEvent
    command: Command
    repo: str  # owner/name, the repo the workflow runs in
    permission: str  # the commenter's role name: admin, maintain, write, triage, read, none
    body_now: str | None  # the comment as it reads now; None when it was deleted
    pull: Pull
    changed: tuple[str, ...]  # each changed file's path, and its old path for a rename
    changed_complete: bool  # False when GitHub's list stopped at its cap
    report: Report | None


@dataclass(frozen=True)
class Refusal:
    reason: str
    local: bool = False  # the reply should give the commands to run locally instead


def _after(time: str, than: str) -> bool:
    return datetime.fromisoformat(time) > datetime.fromisoformat(than)


def refusal(context: FetchContext) -> Refusal | None:
    """Why the bot won't act on this command, in the order docs/review.md lists them; None when
    it may go on. A reason never repeats a path or a name the PR chose."""
    event, pull, report = context.event, context.pull, context.report
    if context.permission not in PERMITTED:
        return Refusal("the commenter can't push to this repo, so the bot won't act for them")
    if context.body_now is None:
        return Refusal("the comment was deleted")
    if context.body_now != event.body:
        return Refusal("the comment was edited after it was posted: post a new one")
    if pull.state != "open" or pull.merged:
        return Refusal("the PR is closed")
    if pull.head_repo != context.repo:
        return Refusal("the PR comes from a fork, and the workflow's token can't push there", True)
    if pull.head_ref == pull.default_branch:
        return Refusal("the PR's head is the default branch")
    if not context.changed_complete:
        return Refusal(
            "the PR changes more files than GitHub lists, so the bot can't check them", True
        )
    if any(unwritable(path) for path in context.changed):
        return Refusal(
            "the PR touches .github/ or an action file, so its own runs could say anything", True
        )
    if isinstance(context.command, Reject):
        return None
    short = pull.head_sha[:7]
    if report is None or report.run_head_sha != pull.head_sha:
        return Refusal(f"there is no lgcov report for `{short}` yet: push, or re-run the report")
    if report.expired:
        return Refusal(f"the report's file for `{short}` has expired: re-run the report")
    if report.run_status != "completed" or report.run_event != "pull_request":
        return Refusal(f"there is no finished report run for `{short}` yet")
    if _after(report.run_updated, event.created_at) or _after(
        report.artifact_updated, event.created_at
    ):
        return Refusal(
            f"the report for `{short}` changed after your comment: read it again, then comment"
        )
    return None


def local_commands(command: Command) -> str:
    """What to run in a checkout of the PR instead, for a reply that can't act."""
    if isinstance(command, Accept):
        which = "--all-drifted" if command.nodes is None else "--nodes " + ",".join(command.nodes)
        line = f"lgcov cassette accept {which}"
    elif isinstance(command, Reject):
        line = "lgcov review reject --base-dir <checkout of the base>"
    else:
        line = (
            f"lgcov review override --kpi {command.kpi} --case-id {command.case} "
            f"--human-label {command.label} --judge-label <what the judge said> --reviewer <you>"
        )
    return f"Run it in a checkout of the PR instead, then push: `{line}`"


def command_to_json(command: Command) -> dict[str, object]:
    if isinstance(command, Accept):
        return {"verb": "accept", "nodes": None if command.nodes is None else list(command.nodes)}
    if isinstance(command, Override):
        return {
            "verb": "override",
            "kpi": command.kpi,
            "case": command.case,
            "label": command.label,
        }
    return {"verb": "reject"}


def command_from_json(raw: object) -> Command:
    """ValueError for anything command_to_json didn't write."""
    if not isinstance(raw, dict):
        raise ValueError("not a command")
    verb = raw.get("verb")
    if verb == "reject" and raw.keys() == {"verb"}:
        return Reject()
    if verb == "accept" and raw.keys() == {"verb", "nodes"}:
        nodes = raw["nodes"]
        if nodes is None:
            return Accept(None)
        if isinstance(nodes, list) and nodes and all(isinstance(n, str) for n in nodes):
            return Accept(tuple(nodes))
    if verb == "override" and raw.keys() == {"verb", "kpi", "case", "label"}:
        kpi, case, label = raw["kpi"], raw["case"], raw["label"]
        if isinstance(kpi, str) and isinstance(case, str) and isinstance(label, str):
            return Override(kpi, case, label)
    raise ValueError(f"not a command: {raw!r}")


NO_GATE_RESULT = (
    "⚠️ This commit has no lgcov gate result yet: push a commit, or close and reopen the PR, "
    "to run the gate."
)
QUOTED = 60  # characters of a name the PR chose, in a reply
LISTED = 20  # files a reply lists before "+N more"
Row = tuple[str, str, str]  # a node, its output hashes before and after


def quoted(text: str, limit: int = QUOTED) -> str:
    """Text the PR or GitHub chose, as a code span in a reply: no backticks, no line breaks, `@`
    defused with a word joiner, no HTML comment marker, and cut to `limit` characters."""
    flat = " ".join(text.replace("`", "").splitlines()).replace("<!--", "<!-")
    flat = flat.replace("@", "@⁠")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return f"`{flat}`" if flat.strip() else "`?`"


def _listed(label: str, paths: tuple[str, ...]) -> list[str]:
    if not paths:
        return []
    shown = ", ".join(quoted(path, 200) for path in paths[:LISTED])
    more = f", +{len(paths) - LISTED} more" if len(paths) > LISTED else ""
    return [f"{label}: {shown}{more}."]


def render_reply(
    command: Command,
    head_sha: str,
    commit_sha: str,
    rows: tuple[Row, ...],
    written: tuple[str, ...],
    removed: tuple[str, ...],
) -> str:
    """The reply once the bot has committed. Its first line says the commit has no gate result
    (docs/review.md); every status word in it is lgcov's own."""
    verb = "Accepted" if isinstance(command, Accept) else "Rejected"
    lines = [
        NO_GATE_RESULT,
        "",
        f"{verb} on head {quoted(head_sha[:12])}: {quoted(commit_sha[:12])}.",
    ]
    if rows:
        lines += ["", "| node | before | after |", "|---|---|---|"]
        lines += [f"| {quoted(n)} | {quoted(a, 200)} | {quoted(b, 200)} |" for n, a, b in rows]
    if isinstance(command, Reject):
        lines += ["", *_listed("Restored or put back", written), *_listed("Removed", removed)]
    return "\n".join(lines).rstrip("\n") + "\n"
