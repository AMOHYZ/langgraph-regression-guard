"""T186: the pure half of `lgcov review apply` and `lgcov review commit`.

Fetch leaves `command.json` and the files it read; apply runs the command on them and leaves
`changes.json` for commit (docs/review.md, "How it runs"). This module reads and writes those two
files, and works out what changed, so both steps agree on their shape.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from lgcov.review import unwritable
from lgcov.review_command import Accept, Command, Override, Reject, Row, command_from_json

_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Job:
    """command.json: the command and what fetch learned about the PR."""

    command: Command
    pr: int
    head_sha: str
    head_ref: str
    base_sha: str
    cassette_dir: str
    reviewer: str
    comment_id: int
    comment_url: str
    judge_label: str = ""  # override only: what the judge said, from the report's copy


def job_from_json(text: str) -> Job:
    """ValueError for anything fetch didn't write."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("command.json isn't an object")

    def _str(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"command.json: {key} isn't text")
        return value

    def _int(key: str) -> int:
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"command.json: {key} isn't a number")
        return value

    command = command_from_json(raw.get("command"))
    job = Job(
        command=command,
        pr=_int("pr"),
        head_sha=_str("head_sha"),
        head_ref=_str("head_ref"),
        base_sha=_str("base_sha"),
        cassette_dir=_str("cassette_dir"),
        reviewer=_str("reviewer"),
        comment_id=_int("comment_id"),
        comment_url=_str("comment_url"),
        judge_label=_str("judge_label") if isinstance(command, Override) else "",
    )
    if not _SHA.fullmatch(job.head_sha) or not _SHA.fullmatch(job.base_sha):
        raise ValueError("command.json: a commit isn't a full SHA")
    why = unwritable(job.cassette_dir)
    if why:
        raise ValueError(f"command.json: cassette_dir {why}")
    return job


def blob_sha(data: bytes) -> str:
    """The SHA git gives a file's bytes, as the trees API lists it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class FileChanges:
    """changes.json: paths from the repo's root to write from `pr/` or remove, and the rows."""

    write: tuple[str, ...]
    remove: tuple[str, ...]
    rows: tuple[Row, ...]


def file_changes(
    job: Job, fetched: Mapping[str, str], after: Mapping[str, bytes], folder: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What the command changed under `folder`: (write, remove), from the repo's root.
    `fetched` is each file fetch read, with its blob sha; `after` each file in the folder now, by
    its path under it. Accept changes only baseline.json: the accept.md it writes is a summary."""
    now = {f"{folder}/{path}": data for path, data in after.items()}
    fetched = {path: sha for path, sha in fetched.items() if path.startswith(f"{folder}/")}
    if isinstance(job.command, Accept):
        keep = f"{folder}/baseline.json"
        now = {path: data for path, data in now.items() if path == keep}
        fetched = {path: sha for path, sha in fetched.items() if path == keep}
    write = tuple(sorted(p for p, data in now.items() if fetched.get(p) != blob_sha(data)))
    remove = tuple(sorted(p for p in fetched if p not in now))
    return write, remove


def node_rows(before: Mapping[str, str] | None, after: Mapping[str, str] | None) -> tuple[Row, ...]:
    """Each node whose recorded output hashes differ, with both; none when a side is unreadable."""
    if before is None or after is None:
        return ()
    nodes = sorted(n for n in {*before, *after} if before.get(n) != after.get(n))
    return tuple((n, before.get(n, "—"), after.get(n, "—")) for n in nodes)


def changes_to_json(changes: FileChanges) -> str:
    body = {"write": changes.write, "remove": changes.remove, "rows": changes.rows}
    return json.dumps(body, indent=2) + "\n"


def changes_from_json(text: str) -> FileChanges:
    """ValueError for anything changes_to_json didn't write."""
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.keys() != {"write", "remove", "rows"}:
        raise ValueError("changes.json isn't apply's")
    write, remove, rows = raw["write"], raw["remove"], raw["rows"]
    if not all(isinstance(v, list) and all(isinstance(p, str) for p in v) for v in (write, remove)):
        raise ValueError("changes.json: a path isn't text")
    if not isinstance(rows, list) or not all(
        isinstance(r, list) and len(r) == 3 and all(isinstance(x, str) for x in r) for r in rows
    ):
        raise ValueError("changes.json: a row isn't three texts")
    return FileChanges(tuple(write), tuple(remove), tuple((r[0], r[1], r[2]) for r in rows))


def allowed(job: Job, path: str) -> bool:
    """docs/review.md's table: whether this command's commit may change the path."""
    if unwritable(path):
        return False
    command = job.command
    if isinstance(command, Accept):
        return path == f"{job.cassette_dir}/baseline.json"
    if isinstance(command, Reject):
        return path.startswith(f"{job.cassette_dir}/")
    calibration = ("overrides.jsonl", f"cases/{command.kpi}.jsonl", "bands.json")
    return path in {f"calibration/{name}" for name in calibration}
