"""T186: the thin GitHub client the review commands use, against a fake: no network."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx
import pytest

from lgcov.github_api import API_URL, GitHub, GitHubError


@dataclass
class Answer:
    status_code: int = 200
    body: object = None
    content: bytes = b""

    def json(self) -> object:
        return self.body


@dataclass
class Fake:
    """Answers by (method, URL); records each call."""

    answers: dict[tuple[str, str], list[Answer]]
    calls: list[tuple[str, str, Mapping[str, str | int] | None, object, bool]] = field(
        default_factory=list
    )
    headers: list[Mapping[str, str]] = field(default_factory=list)
    raise_with: Exception | None = None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
        follow_redirects: bool,
    ) -> Answer:
        self.calls.append((method, url, params, json, follow_redirects))
        self.headers.append(headers)
        if self.raise_with:
            raise self.raise_with
        queue = self.answers[(method, url)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def test_a_get_sends_the_token_and_returns_the_json() -> None:
    fake = Fake({("GET", f"{API_URL}/repos/o/r/pulls/1"): [Answer(body={"number": 1})]})
    assert GitHub("tok", client=fake).get("/repos/o/r/pulls/1") == {"number": 1}
    assert fake.headers[0]["Authorization"] == "Bearer tok"
    assert fake.headers[0]["Accept"] == "application/vnd.github+json"


def test_the_token_never_shows_in_the_client_s_repr() -> None:
    assert "tok" not in repr(GitHub("tok"))


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_an_error_status_raises_with_it_and_no_token(status: int) -> None:
    fake = Fake({("GET", f"{API_URL}/x"): [Answer(status, {"message": "no"})]})
    with pytest.raises(GitHubError) as raised:
        GitHub("tok", client=fake).get("/x")
    assert raised.value.status == status
    assert "tok" not in str(raised.value) and f"{status}" in str(raised.value)


def test_a_transport_error_is_status_0() -> None:
    fake = Fake({}, raise_with=httpx.ConnectTimeout("slow"))
    with pytest.raises(GitHubError) as raised:
        GitHub("tok", client=fake).get("/x")
    assert raised.value.status == 0


def test_pages_reads_until_a_short_page_and_says_it_has_them_all() -> None:
    url = f"{API_URL}/list"
    full = [{"i": i} for i in range(100)]
    fake = Fake({("GET", url): [Answer(body=full), Answer(body=[{"i": 100}])]})
    items, complete = GitHub("t", client=fake).pages("/list")
    assert len(items) == 101 and complete
    assert [c[2] for c in fake.calls] == [
        {"per_page": 100, "page": 1},
        {"per_page": 100, "page": 2},
    ]


def test_pages_stops_at_its_cap_and_says_it_may_have_missed_some() -> None:
    full = [{"i": i} for i in range(100)]
    fake = Fake({("GET", f"{API_URL}/list"): [Answer(body=full)]})
    items, complete = GitHub("t", client=fake).pages("/list", pages=3)
    assert len(items) == 300 and not complete


def test_pages_reads_a_list_under_a_key() -> None:
    url = f"{API_URL}/repos/o/r/actions/artifacts"
    fake = Fake({("GET", url): [Answer(body={"total_count": 1, "artifacts": [{"id": 7}]})]})
    items, complete = GitHub("t", client=fake).pages(
        "/repos/o/r/actions/artifacts", key="artifacts", params={"name": "lgcov-candidate"}
    )
    assert items == [{"id": 7}] and complete
    assert fake.calls[0][2] == {"name": "lgcov-candidate", "per_page": 100, "page": 1}


def test_a_page_that_isn_t_a_list_raises() -> None:
    fake = Fake({("GET", f"{API_URL}/list"): [Answer(body={"oops": 1})]})
    with pytest.raises(GitHubError):
        GitHub("t", client=fake).pages("/list")


def test_download_follows_the_redirect_and_caps_the_size() -> None:
    url = f"{API_URL}/repos/o/r/actions/artifacts/7/zip"
    fake = Fake({("GET", url): [Answer(content=b"zip bytes")]})
    assert GitHub("t", client=fake).download("/repos/o/r/actions/artifacts/7/zip", 100) == (
        b"zip bytes"
    )
    assert fake.calls[0][4] is True  # follow_redirects
    with pytest.raises(GitHubError, match="more than 4 bytes"):
        GitHub("t", client=fake).download("/repos/o/r/actions/artifacts/7/zip", 4)


def test_post_and_patch_send_their_body() -> None:
    fake = Fake(
        {
            ("POST", f"{API_URL}/a"): [Answer(201, {"sha": "s"})],
            ("PATCH", f"{API_URL}/b"): [Answer(200, {"ok": True})],
        }
    )
    gh = GitHub("t", client=fake)
    assert gh.post("/a", {"x": 1}) == {"sha": "s"}
    assert gh.patch("/b", {"force": False}) == {"ok": True}
    assert [(c[0], c[3]) for c in fake.calls] == [("POST", {"x": 1}), ("PATCH", {"force": False})]


def test_another_api_url_is_used_for_github_enterprise() -> None:
    fake = Fake({("GET", "https://ghe.example/api/v3/x"): [Answer(body=1)]})
    assert GitHub("t", api="https://ghe.example/api/v3", client=fake).get("/x") == 1
