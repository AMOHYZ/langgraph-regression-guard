"""T103: read-only LangSmith interop, against a fake client that records every GET."""

import httpx
import pytest
from fixtures.langsmith_api import EXAMPLES, KEY, Fake, Reply

from lgcov.langsmith import API_URL, LangSmith, LangSmithError, cases, from_env


def test_examples_page_through_the_dataset_with_gets_carrying_the_key() -> None:
    first, second = [EXAMPLES[0], EXAMPLES[1]], [{**EXAMPLES[0], "id": "ex-3"}]
    fake = Fake(
        {
            "/datasets": [Reply(200, [{"id": "d1", "name": "support-qa"}])],
            "/examples": [Reply(200, first), Reply(200, second)],
        }
    )
    ls = LangSmith(KEY, client=fake, page=2)
    assert ls.dataset_id("support-qa") == "d1"
    assert ls.examples("d1") == [*first, *second]
    assert [(path, params) for path, params, _ in fake.calls] == [
        ("/datasets", {"name": "support-qa", "limit": 1}),
        ("/examples", {"dataset": "d1", "offset": 0, "limit": 2}),
        ("/examples", {"dataset": "d1", "offset": 2, "limit": 2}),  # a short page: the end
    ]
    assert all(headers == {"x-api-key": KEY} for _, _, headers in fake.calls)


def test_limit_and_split_go_to_every_page_and_it_asks_only_for_what_is_left() -> None:
    fake = Fake({"/examples": [Reply(200, EXAMPLES), Reply(200, EXAMPLES[:1])]})
    got = LangSmith(KEY, client=fake, page=2).examples("d1", limit=3, split="test")
    assert len(got) == 3
    assert [params for _, params, _ in fake.calls] == [
        {"dataset": "d1", "splits": "test", "offset": 0, "limit": 2},
        {"dataset": "d1", "splits": "test", "offset": 2, "limit": 1},
    ]


def test_cases_are_the_inputs_oldest_first_with_ids_and_outputs_beside_them() -> None:
    inputs, sidecar = cases(EXAMPLES)
    assert inputs == [{"question": "Where is my order?"}, {"question": "What are your hours?"}]
    assert sidecar == [
        {"example_id": "ex-1", "outputs": {"answer": "order #42: shipped"}},
        {"example_id": "ex-2", "outputs": None},
    ]
    with pytest.raises(LangSmithError, match="example ex-9: its inputs aren't an object"):
        cases([{"id": "ex-9", "inputs": "hi"}])


def test_a_bad_key_fails_at_once_and_no_message_holds_the_key() -> None:
    fake = Fake({"/datasets": [Reply(401)]})
    with pytest.raises(LangSmithError) as raised:
        LangSmith(KEY, client=fake).dataset_id("support-qa")
    message = str(raised.value)
    assert "HTTP 401 on /datasets" in message and "LANGSMITH_API_KEY" in message
    assert KEY not in message and KEY not in repr(LangSmith(KEY))
    assert len(fake.calls) == 1  # a bad key won't fix itself: no retry


@pytest.mark.parametrize("first", [Reply(503), httpx.ConnectError("no route to host")])
def test_a_server_or_transport_error_is_retried_once(first: Reply | Exception) -> None:
    fake = Fake({"/datasets": [first, Reply(200, [{"id": "d1"}])]})
    assert LangSmith(KEY, client=fake).dataset_id("support-qa") == "d1"
    down = Fake({"/datasets": [Reply(503), Reply(503)]})
    with pytest.raises(LangSmithError, match="unreachable after 2 attempts"):
        LangSmith(KEY, client=down).dataset_id("support-qa")


def test_an_unknown_dataset_is_named() -> None:
    fake = Fake({"/datasets": [Reply(200, [])]})
    with pytest.raises(LangSmithError, match="no LangSmith dataset named `nope`"):
        LangSmith(KEY, client=fake).dataset_id("nope")


def test_from_env_needs_the_key_and_honours_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    assert from_env() is None
    monkeypatch.setenv("LANGSMITH_API_KEY", KEY)
    ls = from_env()
    assert ls is not None and (ls.key, ls.url) == (KEY, API_URL)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")
    eu = from_env()
    assert eu is not None and eu.url == "https://eu.api.smith.langchain.com"
