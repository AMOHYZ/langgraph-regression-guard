"""T103: read-only LangSmith interop.

`lgcov import langsmith` turns a dataset's examples into lgcov cases, the graph inputs
`lgcov record --inputs` takes, and a sidecar of each example's id and reference outputs.
`lgcov ci` links the experiment on lgcov.yaml's dataset that ran on this commit: the
newest whose metadata.git.commit, which the SDK's evaluate() records, is a commit it names.

Read-only by construction: the client has get() and nothing else, on the paths the
langsmith SDK (0.14.12) reads. The key comes from LANGSMITH_API_KEY and only ever travels
as the x-api-key header: no message, log, repr or exception holds it.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

API_URL = "https://api.smith.langchain.com"
TIMEOUT_S = 10.0
ATTEMPTS = 2  # one try + one retry; only transport errors, timeouts and 5xx retry
EXPERIMENTS = 1000  # the most experiments the report reads on a dataset
Params = Mapping[str, str | int]
Item = Mapping[str, object]


class LangSmithError(Exception):
    """LangSmith said no, or couldn't be reached. The message never holds the key."""


class _Response(Protocol):
    status_code: int

    def json(self) -> object: ...


class GetClient(Protocol):
    def get(
        self, url: str, *, params: Params, headers: Mapping[str, str], timeout: float
    ) -> _Response: ...


@dataclass(frozen=True)
class LangSmith:
    """The LangSmith API, read-only: GETs and nothing else."""

    key: str = field(repr=False)
    url: str = API_URL
    client: GetClient | None = None  # None: an httpx.Client per call; tests pass a fake
    page: int = 100  # items per request, the API's most

    def _get(self, path: str, params: Params) -> object:
        headers = {"x-api-key": self.key}
        if self.client is not None:
            return self._request(self.client, path, params, headers)
        with httpx.Client() as client:
            return self._request(client, path, params, headers)

    def _request(
        self, client: GetClient, path: str, params: Params, headers: Mapping[str, str]
    ) -> object:
        last = ""
        for _ in range(ATTEMPTS):
            try:
                url = f"{self.url.rstrip('/')}{path}"
                response = client.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
            except httpx.HTTPError as exc:  # transport errors and timeouts: retry
                last = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code >= 500:
                last = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:  # a bad key or request won't fix itself
                raise LangSmithError(
                    f"LangSmith: HTTP {response.status_code} on {path} "
                    "(check LANGSMITH_API_KEY and LANGSMITH_ENDPOINT)"
                )
            return response.json()
        raise LangSmithError(f"LangSmith: {path} unreachable after {ATTEMPTS} attempts ({last})")

    def _items(self, path: str, params: Params, limit: int | None = None) -> Iterator[Item]:
        """A paginated list's items, up to limit, asking for no more than is left."""
        offset, left = 0, limit
        while left is None or left > 0:
            size = self.page if left is None else min(self.page, left)
            items = self._get(path, {**params, "offset": offset, "limit": size})
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise LangSmithError(f"LangSmith: {path} didn't return a list of objects")
            yield from items
            if len(items) < size:  # a short page: the end
                return
            offset += len(items)
            left = None if left is None else left - len(items)

    def dataset_id(self, name: str) -> str:
        found = self._get("/datasets", {"name": name, "limit": 1})
        if not isinstance(found, list):
            raise LangSmithError("LangSmith: /datasets didn't return a list")
        dataset_id = found[0].get("id") if found and isinstance(found[0], dict) else None
        if not isinstance(dataset_id, str):
            raise LangSmithError(f"no LangSmith dataset named `{name}`")
        return dataset_id

    def examples(
        self, dataset_id: str, limit: int | None = None, split: str | None = None
    ) -> list[Item]:
        params: dict[str, str | int] = {"dataset": dataset_id}
        if split:
            params["splits"] = split
        return list(self._items("/examples", params, limit))

    def experiments(self, dataset_id: str) -> list[Item]:
        """The dataset's experiments, LangSmith's sessions against it, up to EXPERIMENTS."""
        return list(self._items("/sessions", {"reference_dataset": dataset_id}, EXPERIMENTS))


def from_env(client: GetClient | None = None) -> LangSmith | None:
    """The API that LANGSMITH_API_KEY and LANGSMITH_ENDPOINT name; None without a key."""
    key = os.environ.get("LANGSMITH_API_KEY")
    if not key:
        return None
    return LangSmith(key, os.environ.get("LANGSMITH_ENDPOINT") or API_URL, client)


def cases(examples: Sequence[Item]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """The graph inputs `lgcov record --inputs` takes, oldest example first, and beside them
    each example's id and reference outputs, in the same order (pure)."""
    order = sorted(examples, key=lambda e: (str(e.get("created_at", "")), str(e.get("id", ""))))
    inputs: list[dict[str, object]] = []
    sidecar: list[dict[str, object]] = []
    for example in order:
        given = example.get("inputs")
        if not isinstance(given, dict):
            name = example.get("id")
            raise LangSmithError(f"LangSmith example {name}: its inputs aren't an object")
        inputs.append(given)
        sidecar.append({"example_id": example.get("id"), "outputs": example.get("outputs")})
    return inputs, sidecar


def _commit(experiment: Item) -> str | None:
    """The git HEAD that evaluate() recorded in the experiment's metadata, if any."""
    extra = experiment.get("extra")
    metadata = extra.get("metadata") if isinstance(extra, dict) else None
    git = metadata.get("git") if isinstance(metadata, dict) else None
    commit = git.get("commit") if isinstance(git, dict) else None
    return commit if isinstance(commit, str) else None


def matching(experiments: Sequence[Item], commits: Collection[str]) -> Item | None:
    """The newest experiment run on one of these commits; None if none was (pure)."""
    hits = [e for e in experiments if _commit(e) in commits]
    return max(hits, key=lambda e: str(e.get("start_time", "")), default=None)


def web_url(api_url: str) -> str:
    """The LangSmith app for this API, as the SDK maps it: api.smith... -> smith...,
    eu.api.smith... -> eu.smith..., a self-hosted .../api -> its root (pure)."""
    parts = urlsplit(api_url.rstrip("/"))
    for suffix in ("/api/v1", "/api"):
        if parts.path.endswith(suffix):
            return urlunsplit(parts._replace(path=parts.path.removesuffix(suffix)))
    host = parts.netloc.replace("api.smith.langchain.com", "smith.langchain.com")
    return f"{parts.scheme}://{host}"


def experiment_url(api_url: str, experiment: Item) -> str:
    """Where the app compares this experiment on its dataset (pure)."""
    tenant, dataset = experiment.get("tenant_id"), experiment.get("reference_dataset_id")
    session = experiment.get("id")
    return f"{web_url(api_url)}/o/{tenant}/datasets/{dataset}/compare?selectedSessions={session}"
