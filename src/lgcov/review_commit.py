"""T186: `lgcov review commit`, the step that holds the token and writes.

It checks every path apply changed against the command's row of docs/review.md's table, commits
them through the Git Data API with the PR's head as the parent, moves the branch without force,
and replies. A reply fetch or apply already wrote is posted as it is.
"""

from __future__ import annotations

import base64
import contextlib
import json
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from lgcov.github_api import GitHub, GitHubError
from lgcov.review_apply import FileChanges, Job, allowed, changes_from_json, job_from_json
from lgcov.review_command import Accept, quoted, render_reply

MOVED = (
    "Not run: the PR's branch moved while the command ran, so nothing was committed. Comment "
    "again once the report for the new head has run."
)
REFUSED = "Not run: the command changed a file it may not write, so nothing was committed."


def _message(job: Job) -> str:
    command = job.command
    if isinstance(command, Accept):
        what = "every drifted node" if command.nodes is None else ", ".join(command.nodes)
        subject = f"lgcov: accept {what}"
    else:
        subject = "lgcov: reject, back to the base's cassettes"
    if len(subject) > 52:
        subject = subject[:51] + "…"
    trailers = f"Lgcov-Review: {job.comment_url}\nRequested-by: @{job.reviewer}"
    return f"{subject}, from a review comment\n\n{trailers}\n"


def _write(gh: GitHub, repo: str, job: Job, changes: FileChanges, pr: Path) -> str:
    """Blobs, a tree on the head's, a commit on the head, then the branch: the commit's sha."""
    head = gh.get(f"/repos/{repo}/git/commits/{job.head_sha}")
    tree = head.get("tree", {}).get("sha") if isinstance(head, dict) else None
    if not isinstance(tree, str):
        raise GitHubError(200, f"GET commit {job.head_sha[:7]} (no tree)")
    entries: list[dict[str, object]] = []
    for path in changes.write:
        data = (pr / PurePosixPath(path)).read_bytes()
        blob = gh.post(
            f"/repos/{repo}/git/blobs",
            {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
        )
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": _sha(blob)})
    for path in changes.remove:
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    new_tree = gh.post(f"/repos/{repo}/git/trees", {"base_tree": tree, "tree": entries})
    commit = gh.post(
        f"/repos/{repo}/git/commits",
        {"message": _message(job), "tree": _sha(new_tree), "parents": [job.head_sha]},
    )
    sha = _sha(commit)
    ref = quote(job.head_ref, safe="/")
    gh.patch(f"/repos/{repo}/git/refs/heads/{ref}", {"sha": sha, "force": False})
    return sha


def _sha(raw: object) -> str:
    sha = raw.get("sha") if isinstance(raw, dict) else None
    if not isinstance(sha, str):
        raise GitHubError(200, "a Git Data answer (no sha)")
    return sha


def commit(gh: GitHub, repo: str, out: Path) -> int:
    """0 when there was nothing to answer, or a reply was posted; 2 when GitHub said no."""
    if not (out / "event.json").exists():
        return 0  # not a command: fetch wrote nothing
    event = json.loads((out / "event.json").read_text(encoding="utf-8"))
    pr = event.get("pr") if isinstance(event, dict) else None
    if not isinstance(pr, int) or isinstance(pr, bool):
        raise ValueError("event.json names no PR")
    comments = f"/repos/{repo}/issues/{pr}/comments"
    if (out / "reply.md").exists():
        gh.post(comments, {"body": (out / "reply.md").read_text(encoding="utf-8")})
        return 0
    try:
        job = job_from_json((out / "command.json").read_text(encoding="utf-8"))
        changes = changes_from_json((out / "changes.json").read_text(encoding="utf-8"))
        if not all(allowed(job, path) for path in (*changes.write, *changes.remove)):
            gh.post(comments, {"body": REFUSED})
            return 0
        try:
            sha = _write(gh, repo, job, changes, out / "pr")
        except GitHubError as exc:
            if exc.status != 422 or "/git/refs/" not in str(exc):
                raise
            gh.post(comments, {"body": MOVED})
            return 0
    except (GitHubError, OSError, ValueError) as exc:
        why = f"Not run: {quoted(str(exc), 300)}. Nothing was committed; comment again to retry."
        with contextlib.suppress(GitHubError):  # then the job's own log says it failed
            gh.post(comments, {"body": why})
        return 2
    # the branch has moved: from here a failure must not say nothing was committed
    body = render_reply(job.command, job.head_sha, sha, changes.rows, changes.write, changes.remove)
    gh.post(comments, {"body": body})
    return 0
