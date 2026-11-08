"""T119: one benchmark round, fixed before its candidates run, and the ledger of what each
candidate has seen (pure). docs/benchmark-rounds.md has the format and what it doesn't stop.
A gate round (docs/round.md, where it exists) is a different thing: this one is for `lgcov bench`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)


def _from_yaml_date(value: object) -> object:
    """YAML reads an unquoted 2026-11-05 as a date; a stamp is always kept as text."""
    return value.isoformat() if isinstance(value, date) else value


def _iso(value: str) -> str:
    datetime.fromisoformat(value)  # ValueError, which pydantic reports, if it isn't one
    return value


_Name = Annotated[StrictStr, StringConstraints(min_length=1)]
_Sha = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
_Stamp = Annotated[StrictStr, BeforeValidator(_from_yaml_date), AfterValidator(_iso)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Case(_Model):
    id: _Name
    source: _Name


class Candidate(_Model):
    name: _Name
    repo: _Name
    base_sha: _Sha
    head_sha: _Sha
    locked_at: _Stamp | None = None


class Round(_Model):
    id: _Name
    created_at: _Stamp
    frozen: StrictBool = False
    candidates: tuple[Candidate, ...] = ()
    cases: tuple[Case, ...] = ()

    @model_validator(mode="after")
    def _names_are_unique(self) -> Self:
        names = [c.name for c in self.candidates]
        ids = [c.id for c in self.cases]
        if len(set(names)) != len(names) or len(set(ids)) != len(ids):
            raise ValueError("candidate names and case ids must be unique")
        return self


class LockRow(_Model):
    event: Literal["lock"] = "lock"
    round: _Name
    candidate: _Name
    repo: _Name
    base_sha: _Sha
    head_sha: _Sha
    cases: tuple[Case, ...]
    at: _Stamp


class ExposureRow(_Model):
    event: Literal["exposure"] = "exposure"
    round: _Name
    candidate: _Name
    repo: _Name
    head_sha: _Sha
    case: _Name
    run_id: _Name
    seen_at: _Stamp


LedgerRow = LockRow | ExposureRow
_ROW: TypeAdapter[LedgerRow] = TypeAdapter(Annotated[LedgerRow, Field(discriminator="event")])


def parse_round(text: str) -> Round:
    """The round in a file. ValueError if it isn't one."""
    try:
        return Round.model_validate(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise ValueError(f"not YAML: {exc}") from exc


def parse_ledger(text: str) -> tuple[LedgerRow, ...]:
    """The rows of an exposure.jsonl. ValueError, naming the line, if one is broken."""
    rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_ROW.validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"exposure ledger line {number}: {exc}") from exc
    return tuple(rows)


def _drift(candidate: Candidate, lock: LockRow) -> list[str]:
    locked = (
        ("repo", lock.repo, candidate.repo),
        ("base_sha", lock.base_sha, candidate.base_sha),
        ("head_sha", lock.head_sha, candidate.head_sha),
        ("locked_at", lock.at, candidate.locked_at),
    )
    return [
        f"candidate {candidate.name}: {field} was locked as {was} and is {now} now"
        for field, was, now in locked
        if was != now
    ]


def _exposed_elsewhere(round_: Round, seen: tuple[ExposureRow, ...]) -> list[str]:
    """A new SHA or a new name is still the same candidate: either match counts."""
    return [
        f"candidate {candidate.name} already saw case {case.id} in round {earlier}"
        for candidate in round_.candidates
        for case in round_.cases
        for earlier in sorted(
            {
                row.round
                for row in seen
                if row.round != round_.id
                and row.case == case.id
                and (
                    row.candidate == candidate.name
                    or (row.repo, row.head_sha) == (candidate.repo, candidate.head_sha)
                )
            }
        )
    ]


def check(round_: Round, rows: Iterable[LedgerRow]) -> tuple[str, ...]:
    """Every reason this round can't be trusted; () when it's clean."""
    locks: dict[str, LockRow] = {}
    seen: list[ExposureRow] = []
    for row in rows:
        if isinstance(row, ExposureRow):
            seen.append(row)
        elif row.round == round_.id:
            locks.setdefault(row.candidate, row)  # the first wins: a later row can't move a lock
    problems = [] if round_.frozen else [f"round {round_.id} is not frozen; run `lgcov round lock`"]
    for candidate in round_.candidates:
        lock = locks.get(candidate.name)
        if lock is None:
            problems.append(f"candidate {candidate.name} has no lock row in the ledger")
        else:
            problems.extend(_drift(candidate, lock))
    if any(set(lock.cases) != set(round_.cases) for lock in locks.values()):
        problems.append(f"round {round_.id}: the cases changed after the lock")
    names = {candidate.name for candidate in round_.candidates}
    problems.extend(
        f"locked candidate {name} is no longer in the round" for name in locks if name not in names
    )
    ids = {case.id for case in round_.cases}
    for row in seen:
        if row.round != round_.id:
            continue
        lock = locks.get(row.candidate)
        if lock is None or lock.head_sha != row.head_sha:
            locked_sha = lock.head_sha if lock else "no locked commit"
            problems.append(
                f"run {row.run_id} saw candidate {row.candidate} at {row.head_sha}, "
                f"not {locked_sha}"
            )
        if row.case not in ids:
            problems.append(f"run {row.run_id} saw case {row.case}, which isn't in the round")
    problems.extend(_exposed_elsewhere(round_, tuple(seen)))
    return tuple(problems)


def new_round(round_id: str, created_at: str) -> Round:
    """What `lgcov round init` writes; a person fills in the candidates and cases."""
    return Round(id=round_id, created_at=created_at)


def lock_round(
    round_: Round, rows: Iterable[LedgerRow], at: str
) -> tuple[Round, tuple[LockRow, ...]]:
    """The frozen round and the rows that record its lock. ValueError if it can't be locked."""
    if round_.frozen:
        raise ValueError(f"round {round_.id} is already frozen")
    if any(isinstance(row, LockRow) and row.round == round_.id for row in rows):
        raise ValueError(f"round {round_.id} is already locked in the ledger")
    if not round_.candidates or not round_.cases:
        raise ValueError(f"round {round_.id} needs at least one candidate and one case")
    candidates = tuple(c.model_copy(update={"locked_at": at}) for c in round_.candidates)
    locked = round_.model_copy(update={"frozen": True, "candidates": candidates})
    return locked, tuple(
        LockRow(
            round=round_.id,
            candidate=c.name,
            repo=c.repo,
            base_sha=c.base_sha,
            head_sha=c.head_sha,
            cases=round_.cases,
            at=at,
        )
        for c in round_.candidates
    )


def exposures(round_: Round, run_id: str, seen_at: str) -> tuple[ExposureRow, ...]:
    """One row per candidate x case, for a run of this round."""
    return tuple(
        ExposureRow(
            round=round_.id,
            candidate=candidate.name,
            repo=candidate.repo,
            head_sha=candidate.head_sha,
            case=case.id,
            run_id=run_id,
            seen_at=seen_at,
        )
        for candidate in round_.candidates
        for case in round_.cases
    )


def round_to_yaml(round_: Round) -> str:
    return yaml.safe_dump(round_.model_dump(mode="json"), sort_keys=False)


def ledger_lines(rows: Iterable[LedgerRow]) -> str:
    return "".join(json.dumps(row.model_dump(mode="json")) + "\n" for row in rows)


@dataclass(frozen=True)
class TreeState:
    """What `lgcov round lock` makes of `git status`."""

    tracked_changes: tuple[str, ...]  # refuse: the lock would record a commit that isn't all there
    untracked_in_folder: tuple[str, ...]  # refuse: these could be part of the round
    untracked_elsewhere: tuple[str, ...]  # warn only: a stray file can't change the round


def tree_state(status: str, folder: str) -> TreeState:
    """Sort `git status --porcelain -z --untracked-files=all`. folder is the round's, relative to
    the repo's root with a trailing slash, as `git rev-parse --show-prefix` gives; '' is the root,
    which holds every file."""
    tracked, inside, elsewhere = [], [], []
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code != "??":
            tracked.append(path)
            if "R" in code or "C" in code:
                next(entries, None)  # -z gives a rename's old path as an entry of its own
        elif path.startswith(folder):
            inside.append(path)
        else:
            elsewhere.append(path)
    return TreeState(tuple(tracked), tuple(inside), tuple(elsewhere))
