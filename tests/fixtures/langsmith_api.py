"""T103: a fake LangSmith for tests: it only answers GETs, from a queue of replies per path,
and records every request. No network."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from lgcov.langsmith import API_URL

KEY = "fake-langsmith-key-that-must-never-be-printed"
EXAMPLES: list[dict[str, object]] = [  # out of order on purpose: import sorts oldest first
    {
        "id": "ex-2",
        "created_at": "2026-10-02T09:00:00",
        "inputs": {"question": "What are your hours?"},
        "outputs": None,
    },
    {
        "id": "ex-1",
        "created_at": "2026-10-01T09:00:00",
        "inputs": {"question": "Where is my order?"},
        "outputs": {"answer": "order #42: shipped"},
    },
]


@dataclass
class Reply:
    status_code: int
    body: object = None

    def json(self) -> object:
        return self.body


@dataclass
class Fake:
    """Each GET to a path takes that path's next reply, or raises it if it's an exception."""

    routes: dict[str, list[Reply | Exception]]
    calls: list[tuple[str, dict[str, str | int], dict[str, str]]] = field(default_factory=list)

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Reply:
        path = url.removeprefix(API_URL)
        self.calls.append((path, dict(params), dict(headers)))
        reply = self.routes[path].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
