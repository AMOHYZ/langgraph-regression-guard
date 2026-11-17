"""T186: the GitHub REST calls the review commands make (I/O, kept thin).

Every call carries the token in its Authorization header and nowhere else, and no error message
holds it. httpx drops that header when a redirect leaves the API's host, as an artifact's download
does. Tests pass a fake client, so nothing here touches the network in them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import httpx

API_URL = "https://api.github.com"
TIMEOUT_S = 20.0
PAGES = 10  # the most pages a list is read for, 100 items each
Params = Mapping[str, str | int]


class GitHubError(Exception):
    """GitHub said no, or couldn't be reached (status 0). The message never holds the token."""

    def __init__(self, status: int, what: str) -> None:
        said = "couldn't be reached" if status == 0 else f"answered {status}"
        super().__init__(f"GitHub {said} to {what}")
        self.status = status


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def content(self) -> bytes: ...

    def json(self) -> object: ...


class Client(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Params | None,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
        follow_redirects: bool,
    ) -> _Response: ...


@dataclass(frozen=True)
class GitHub:
    """The REST API as the review commands use it. Paths start with `/`, as GitHub's docs give
    them; `api` is GITHUB_API_URL, so GitHub Enterprise works too."""

    token: str = field(repr=False)
    api: str = API_URL
    client: Client | None = None  # None: an httpx.Client per call; tests pass a fake

    def _call(
        self,
        method: str,
        path: str,
        params: Params | None = None,
        body: object = None,
        follow: bool = False,
    ) -> _Response:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url, what = f"{self.api}{path}", f"{method} {path}"
        try:
            if self.client is not None:
                answer = self.client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=TIMEOUT_S,
                    follow_redirects=follow,
                )
            else:
                with httpx.Client() as client:
                    answer = client.request(
                        method,
                        url,
                        params=params,
                        headers=headers,
                        json=body,
                        timeout=TIMEOUT_S,
                        follow_redirects=follow,
                    )
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"{what} ({type(exc).__name__})") from None
        if answer.status_code >= 400:
            raise GitHubError(answer.status_code, what)
        return answer

    def get(self, path: str, params: Params | None = None) -> object:
        return self._call("GET", path, params).json()

    def pages(
        self, path: str, key: str | None = None, params: Params | None = None, pages: int = PAGES
    ) -> tuple[list[object], bool]:
        """A list, page by page, and whether it is all of it: False when `pages` ran out first."""
        items: list[object] = []
        for page in range(1, pages + 1):
            body = self.get(path, {**(params or {}), "per_page": 100, "page": page})
            batch = body.get(key) if key and isinstance(body, dict) else body
            if not isinstance(batch, list):
                raise GitHubError(200, f"GET {path} (not a list)")
            items += batch
            if len(batch) < 100:
                return items, True
        return items, False

    def download(self, path: str, limit: int) -> bytes:
        """The bytes at path, following its redirect; GitHubError past `limit` bytes."""
        content = self._call("GET", path, follow=True).content
        if len(content) > limit:
            raise GitHubError(200, f"GET {path} (more than {limit} bytes)")
        return content

    def post(self, path: str, body: object) -> object:
        return self._call("POST", path, body=body).json()

    def patch(self, path: str, body: object) -> object:
        return self._call("PATCH", path, body=body).json()
