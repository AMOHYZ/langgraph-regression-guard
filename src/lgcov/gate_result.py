"""T154: the gate's result, bound to what produced it: the base and head commits and the policy
files the gate read (pure). `lgcov ci --result-out` writes it, and `lgcov verify-result` checks it
before the PR comment is posted.

The binding catches a result that was edited, is stale or came from other policy. It is not a
signature: every value in it is public, so in one job the PR's code can write a matching file. With
two (the Action's `phase`) that code never runs where this is written: docs/trust-boundary.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass

SCHEMA = 1


@dataclass(frozen=True)
class GateResult:
    base_sha: str
    head_sha: str
    policy: dict[str, str]  # each policy file, by path: its hash, from file_hash
    exit_code: int
    verdict: str
    report_sha256: str


def file_hash(data: bytes | None) -> str:
    """A file's hash; '' for one that isn't there, so adding a file changes it too."""
    return "" if data is None else hashlib.sha256(data).hexdigest()


def _digest(result: GateResult) -> str:
    return file_hash(json.dumps(asdict(result), sort_keys=True).encode("utf-8"))


def result_to_json(result: GateResult) -> str:
    body = {**asdict(result), "schema": SCHEMA, "digest": _digest(result)}
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def parse_result(text: str) -> GateResult:
    """The result in a file. ValueError if it isn't one, or was edited without its digest."""
    raw: object = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ValueError(f"not a schema {SCHEMA} result")
    policy = raw.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("no policy hashes")
    try:
        result = GateResult(
            base_sha=str(raw["base_sha"]),
            head_sha=str(raw["head_sha"]),
            policy={str(k): str(v) for k, v in policy.items()},
            exit_code=int(raw["exit_code"]),
            verdict=str(raw["verdict"]),
            report_sha256=str(raw["report_sha256"]),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"a field is missing or isn't valid: {exc}") from None
    if raw.get("digest") != _digest(result):
        raise ValueError("its digest doesn't match, so it was edited")
    return result


def _short(sha: str) -> str:
    return f"`{sha[:7]}`" if sha else "unreadable"


def verify_result(
    result: GateResult, base_sha: str, head_sha: str, policy: Mapping[str, str], report: bytes
) -> str:
    """'' when the result binds to these; else why it gives no verdict."""
    if result.base_sha != base_sha:
        return f"it is for base {_short(result.base_sha)}, and this PR's is {_short(base_sha)}"
    if result.head_sha != head_sha:
        return f"it is for head {_short(result.head_sha)}, and this PR's is {_short(head_sha)}"
    if result.policy != policy:
        differ = sorted(
            f for f in result.policy.keys() | policy.keys() if result.policy.get(f) != policy.get(f)
        )
        return f"it wasn't graded on the base's policy: {', '.join(differ)}"
    if result.report_sha256 != file_hash(report):
        return "the report isn't the one it was written with"
    return ""
