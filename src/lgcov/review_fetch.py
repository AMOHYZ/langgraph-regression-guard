"""T186: `lgcov review fetch`, the half of a review command that holds the token and only reads.

It parses the comment, makes docs/review.md's checks through GitHub's API, and writes what the
command needs into one folder: `command.json`, `candidate.json`, the PR's files under `pr/` and
the base's under `base/`, each read as data through the API, so nothing of the PR is checked out.
On a refusal it writes only `reply.md`. `apply` and `commit` take it from there.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import yaml

from lgcov.github_api import GitHub, GitHubError
from lgcov.review import unwritable
from lgcov.review_command import (
    Accept,
    Command,
    CommandError,
    CommentEvent,
    FetchContext,
    Pull,
    Refusal,
    Reject,
    Report,
    command_to_json,
    local_commands,
    may_command,
    parse_command,
    refusal,
)

CANDIDATE = "lgcov-candidate"
CANDIDATE_BYTES = 50 * 2**20  # the artifact's zip, and candidate.json inside it
FILE_BYTES = 10 * 2**20
FILES = 500  # the most files fetch reads from one folder
CHANGED_PAGES = 30  # GitHub lists at most 3,000 of a PR's files, 100 a page
_PLAIN = ("100644", "100755")  # a tree entry's mode for a plain file; 120000 is a symlink


class Refused(Exception):
    """A reason the bot won't go on, found while reading the PR's files."""


@dataclass(frozen=True)
class Fetched:
    """What fetch decided: 0 when the folder is ready or a refusal is written, 2 when GitHub
    couldn't be reached or answered wrong, which is written as the reply too."""

    code: int


def _text(raw: object, *path: str) -> str:
    for key in path:
        raw = raw.get(key) if isinstance(raw, dict) else None
    return raw if isinstance(raw, str) else ""


def _pull(raw: object, number: int) -> Pull:
    return Pull(
        number=number,
        state=_text(raw, "state"),
        merged=isinstance(raw, dict) and raw.get("merged") is True,
        head_repo=_text(raw, "head", "repo", "full_name"),
        head_ref=_text(raw, "head", "ref"),
        head_sha=_text(raw, "head", "sha"),
        base_sha=_text(raw, "base", "sha"),
        default_branch=_text(raw, "base", "repo", "default_branch"),
    )


def _changed(gh: GitHub, repo: str, pull: int, count: object) -> tuple[tuple[str, ...], bool]:
    files, complete = gh.pages(f"/repos/{repo}/pulls/{pull}/files", pages=CHANGED_PAGES)
    paths = [_text(f, key) for f in files for key in ("filename", "previous_filename")]
    return tuple(p for p in paths if p), complete and len(files) == count


def _report(gh: GitHub, repo: str, pull: Pull) -> Report | None:
    """The newest candidate file for the head, and its run; None when there is none."""
    artifacts, _ = gh.pages(
        f"/repos/{repo}/actions/artifacts", key="artifacts", params={"name": CANDIDATE}, pages=3
    )
    ours = [a for a in artifacts if _text(a, "workflow_run", "head_sha") == pull.head_sha]
    if not ours:
        return None
    newest = max(ours, key=lambda a: _text(a, "updated_at"))
    assert isinstance(newest, dict)  # _text found a key in it
    run_id = newest.get("workflow_run", {}).get("id")
    run = gh.get(f"/repos/{repo}/actions/runs/{int(run_id)}")
    prs = run.get("pull_requests", []) if isinstance(run, dict) else []
    base = next((_text(p, "base", "sha") for p in prs if _number(p) == pull.number), "")
    return Report(
        artifact_id=int(newest["id"]),
        expired=newest.get("expired") is not False,
        artifact_updated=_text(newest, "updated_at"),
        run_status=_text(run, "status"),
        run_event=_text(run, "event"),
        run_head_sha=_text(run, "head_sha") if base else "",
        run_updated=_text(run, "updated_at"),
        base_sha=base,
    )


def _number(raw: object) -> int | None:
    value = raw.get("number") if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _permission(gh: GitHub, repo: str, login: str) -> str:
    try:
        raw = gh.get(f"/repos/{repo}/collaborators/{quote(login, safe='')}/permission")
    except GitHubError as exc:
        if exc.status == 404:
            return "none"
        raise
    return _text(raw, "role_name") or _text(raw, "permission") or "none"


def _body_now(gh: GitHub, repo: str, comment_id: int) -> str | None:
    try:
        return _text(gh.get(f"/repos/{repo}/issues/comments/{comment_id}"), "body")
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise


def _tree(gh: GitHub, repo: str, sha: str) -> list[object]:
    raw = gh.get(f"/repos/{repo}/git/trees/{sha}", {"recursive": 1})
    if not isinstance(raw, dict) or raw.get("truncated") is not False:
        raise Refused("GitHub's list of the commit's files was cut short")
    entries = raw.get("tree")
    return entries if isinstance(entries, list) else []


def _blob(gh: GitHub, repo: str, sha: str) -> bytes:
    raw = gh.get(f"/repos/{repo}/git/blobs/{sha}")
    if _text(raw, "encoding") != "base64":
        raise GitHubError(200, f"GET blob {sha[:7]} (not base64)")
    return base64.b64decode(_text(raw, "content"))


def _folder(gh: GitHub, repo: str, tree: list[object], folder: str, into: Path) -> dict[str, str]:
    """Each plain file under folder, written into `into` at its path; {path: blob sha}."""
    shas: dict[str, str] = {}
    for entry in tree:
        path = _text(entry, "path")
        if not path.startswith(f"{folder}/") or _text(entry, "type") == "tree":
            continue
        if _text(entry, "mode") not in _PLAIN or _text(entry, "type") != "blob":
            raise Refused(f"`{folder}` holds a symlink or a submodule, which the bot won't touch")
        if unwritable(path):
            raise Refused(f"`{folder}` holds a file the bot may not write")
        size = entry.get("size") if isinstance(entry, dict) else None
        if not isinstance(size, int) or size > FILE_BYTES or len(shas) >= FILES:
            raise Refused(f"`{folder}` holds more than the bot reads")
        target = into / PurePosixPath(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_blob(gh, repo, _text(entry, "sha")))
        shas[path] = _text(entry, "sha")
    return shas


def _cassette_dir(config: bytes | None, override: str | None) -> str:
    """The folder the base's lgcov.yaml names, as the gate reads it there."""
    if override:
        folder = override
    else:
        raw = yaml.safe_load(config.decode("utf-8")) if config else None
        named = raw.get("cassette_dir") if isinstance(raw, dict) else None
        folder = named if isinstance(named, str) and named else "cassettes"
    folder = PurePosixPath(folder).as_posix()
    if unwritable(folder):
        raise Refused("the base's `cassette_dir` is a folder the bot may not write")
    return folder


def _candidate(gh: GitHub, repo: str, artifact: int) -> bytes:
    data = gh.download(f"/repos/{repo}/actions/artifacts/{artifact}/zip", CANDIDATE_BYTES)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("candidate.json")
            if info.file_size > CANDIDATE_BYTES:
                raise Refused("the report's file is larger than the bot reads")
            return archive.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise Refused("the report's artifact holds no candidate.json") from None


def _answering(out: Path, event: CommentEvent) -> None:
    """Where `commit` posts the reply, written once there is something to answer."""
    meta = {"pr": event.pr, "comment_id": event.comment_id}
    (out / "event.json").write_text(json.dumps(meta), encoding="utf-8")


def _reply(out: Path, text: str) -> None:
    (out / "reply.md").write_text(text + "\n", encoding="utf-8")


def fetch(gh: GitHub, event: CommentEvent, out: Path, cassette_dir: str | None = None) -> Fetched:
    """Make the checks and fill `out`, which must exist and be empty."""
    if not may_command(event) or event.pr is None:
        return Fetched(0)  # the workflow's `if` keeps these out; nothing to say to anyone
    try:
        command = parse_command(event.body)
    except CommandError as exc:
        _answering(out, event)
        why = str(exc)
        _reply(out, f"Not run: {why[:1].lower()}{why[1:]}. The commands are in docs/review.md.")
        return Fetched(0)
    if command is None:
        return Fetched(0)
    _answering(out, event)
    repo = event.repo
    try:
        return _checked(gh, event, command, repo, out, cassette_dir)
    except Refused as exc:
        _reply(out, f"Not run: {exc}.")
        return Fetched(0)
    except (GitHubError, ValueError, KeyError, TypeError) as exc:
        _reply(out, f"Not run: {exc}. Nothing was committed; comment again to retry.")
        return Fetched(2)


def _checked(
    gh: GitHub, event: CommentEvent, command: Command, repo: str, out: Path, override: str | None
) -> Fetched:
    assert event.pr is not None
    raw = gh.get(f"/repos/{repo}/pulls/{event.pr}")
    pull = _pull(raw, event.pr)
    changed, complete = _changed(gh, repo, event.pr, _number_of(raw, "changed_files"))
    report = None if isinstance(command, Reject) else _report(gh, repo, pull)
    context = FetchContext(
        event=event,
        command=command,
        repo=repo,
        permission=_permission(gh, repo, event.author),
        body_now=_body_now(gh, repo, event.comment_id),
        pull=pull,
        changed=changed,
        changed_complete=complete,
        report=report,
    )
    refused: Refusal | None = refusal(context)
    if refused:
        local = f"\n\n{local_commands(command)}" if refused.local else ""
        _reply(out, f"Not run: {refused.reason}.{local}")
        return Fetched(0)
    base_sha = pull.base_sha if report is None else report.base_sha
    base_tree = _tree(gh, repo, base_sha)
    config = next((e for e in base_tree if _text(e, "path") == "lgcov.yaml"), None)
    config_bytes = _blob(gh, repo, _text(config, "sha")) if config else None
    if config_bytes is not None:
        (out / "base").mkdir()
        (out / "base" / "lgcov.yaml").write_bytes(config_bytes)
    folder = _cassette_dir(config_bytes, override)
    fetched = _folder(gh, repo, _tree(gh, repo, pull.head_sha), folder, out / "pr")
    if isinstance(command, Reject):
        _folder(gh, repo, base_tree, folder, out / "base")
    if isinstance(command, Accept):
        assert report is not None
        (out / "candidate.json").write_bytes(_candidate(gh, repo, report.artifact_id))
    meta = {
        "command": command_to_json(command),
        "pr": pull.number,
        "head_sha": pull.head_sha,
        "head_ref": pull.head_ref,
        "base_sha": base_sha,
        "cassette_dir": folder,
        "reviewer": event.author,
        "comment_id": event.comment_id,
        "comment_url": event.url,
    }
    (out / "command.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (out / "fetched.json").write_text(json.dumps(fetched, indent=2) + "\n", encoding="utf-8")
    return Fetched(0)


def _number_of(raw: object, key: str) -> int:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else -1
