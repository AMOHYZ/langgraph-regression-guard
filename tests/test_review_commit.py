"""T186: `lgcov review commit`, the step that holds the token and writes. Against a fake GitHub:
the exact Git Data calls, the branch moved, each path the allowlist refuses, and the reply."""

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lgcov.cli import _review_commit
from lgcov.github_api import API_URL
from lgcov.review_command import NO_GATE_RESULT

REPO = "octo-org/agent"
H, B = "a" * 40, "b" * 40
URL = f"https://github.com/{REPO}/pull/12#issuecomment-123"


@dataclass
class Answer:
    status_code: int = 200
    body: object = None
    content: bytes = b""

    def json(self) -> object:
        return self.body


@dataclass
class Api:
    """A fake GitHub that records each call and answers the Git Data API."""

    fail: dict[tuple[str, str], int] = field(default_factory=dict)  # (method, path) -> status
    calls: list[tuple[str, str, object]] = field(default_factory=list)

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
        path = url.removeprefix(API_URL)
        self.calls.append((method, path, json))
        if (method, path) in self.fail:
            return Answer(self.fail[(method, path)])
        answers: dict[tuple[str, str], object] = {
            ("GET", f"/repos/{REPO}/git/commits/{H}"): {"tree": {"sha": "t" * 40}},
            ("POST", f"/repos/{REPO}/git/blobs"): {"sha": "1" * 40},
            ("POST", f"/repos/{REPO}/git/trees"): {"sha": "2" * 40},
            ("POST", f"/repos/{REPO}/git/commits"): {"sha": "c" * 40},
            ("PATCH", f"/repos/{REPO}/git/refs/heads/feat/x"): {"object": {"sha": "c" * 40}},
            ("POST", f"/repos/{REPO}/issues/12/comments"): {"id": 1},
        }
        key = (method, path)
        return Answer(body=answers[key]) if key in answers else Answer(404)

    def git_calls(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.calls if "/git/" in p]

    def reply(self) -> str:
        bodies = [body for m, p, body in self.calls if p.endswith("/issues/12/comments")]
        assert len(bodies) == 1, self.calls
        assert isinstance(bodies[0], dict)
        text = bodies[0]["body"]
        assert isinstance(text, str)
        return text


def _folder(
    tmp_path: Path,
    command: dict[str, object],
    write: Mapping[str, bytes],
    remove: tuple[str, ...] = (),
) -> Path:
    """The folder as apply leaves it after a command that changed files."""
    out = tmp_path / "review"
    for path, data in write.items():
        target = out / "pr" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    job = {
        "command": command,
        "pr": 12,
        "head_sha": H,
        "head_ref": "feat/x",
        "base_sha": B,
        "cassette_dir": "cassettes",
        "reviewer": "maintainer",
        "comment_id": 123,
        "comment_url": URL,
    }
    changes = {"write": sorted(write), "remove": list(remove), "rows": [["b", "111", "222"]]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "event.json").write_text(json.dumps({"pr": 12, "comment_id": 123}), encoding="utf-8")
    (out / "command.json").write_text(json.dumps(job), encoding="utf-8")
    (out / "changes.json").write_text(json.dumps(changes), encoding="utf-8")
    return out


ACCEPT: dict[str, object] = {"verb": "accept", "nodes": ["b"]}
REJECT: dict[str, object] = {"verb": "reject"}


def _commit(out: Path, api: Api, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    return _review_commit(["--dir", str(out)], api)


def test_accept_commits_on_the_head_moves_the_branch_and_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _folder(tmp_path, ACCEPT, {"cassettes/baseline.json": b'{"runs": []}\n'})
    api = Api()
    assert _commit(out, api, monkeypatch) == 0
    assert api.git_calls() == [
        ("GET", f"/repos/{REPO}/git/commits/{H}"),
        ("POST", f"/repos/{REPO}/git/blobs"),
        ("POST", f"/repos/{REPO}/git/trees"),
        ("POST", f"/repos/{REPO}/git/commits"),
        ("PATCH", f"/repos/{REPO}/git/refs/heads/feat/x"),
    ]
    bodies = {(m, p): body for m, p, body in api.calls}
    blob = bodies[("POST", f"/repos/{REPO}/git/blobs")]
    assert blob == {"content": base64.b64encode(b'{"runs": []}\n').decode(), "encoding": "base64"}
    tree = bodies[("POST", f"/repos/{REPO}/git/trees")]
    assert tree == {
        "base_tree": "t" * 40,
        "tree": [
            {"path": "cassettes/baseline.json", "mode": "100644", "type": "blob", "sha": "1" * 40}
        ],
    }
    commit = bodies[("POST", f"/repos/{REPO}/git/commits")]
    assert isinstance(commit, dict)
    assert commit["parents"] == [H] and commit["tree"] == "2" * 40
    message = commit["message"]
    assert isinstance(message, str)
    assert message.splitlines()[0] == "lgcov: accept b, from a review comment"
    assert f"Lgcov-Review: {URL}" in message and "Requested-by: @maintainer" in message
    assert bodies[("PATCH", f"/repos/{REPO}/git/refs/heads/feat/x")] == {
        "sha": "c" * 40,
        "force": False,
    }
    reply = api.reply()
    assert reply.splitlines()[0] == NO_GATE_RESULT
    assert "`cccccccccccc`" in reply and "| `b` | `111` | `222` |" in reply


def test_reject_removes_files_with_a_null_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _folder(tmp_path, REJECT, {"cassettes/baseline.json": b"{}\n"}, ("cassettes/x.json",))
    api = Api()
    assert _commit(out, api, monkeypatch) == 0
    tree = {(m, p): body for m, p, body in api.calls}[("POST", f"/repos/{REPO}/git/trees")]
    assert isinstance(tree, dict)
    assert tree["tree"][1] == {
        "path": "cassettes/x.json",
        "mode": "100644",
        "type": "blob",
        "sha": None,
    }
    assert "Removed: `cassettes/x.json`." in api.reply()


def test_a_branch_that_moved_commits_nothing_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _folder(tmp_path, ACCEPT, {"cassettes/baseline.json": b"{}\n"})
    api = Api(fail={("PATCH", f"/repos/{REPO}/git/refs/heads/feat/x"): 422})
    assert _commit(out, api, monkeypatch) == 0
    reply = api.reply()
    assert reply.startswith("Not run: the PR's branch moved while the command ran")
    assert NO_GATE_RESULT not in reply


@pytest.mark.parametrize(
    ("command", "path"),
    [
        (ACCEPT, "cassettes/other.json"),
        (ACCEPT, "graph.py"),
        (REJECT, "graph.py"),
        (REJECT, "cassettes/.github/workflows/x.yml"),
        (REJECT, "cassettes/sub/action.yml"),
        (REJECT, "cassettes/../.github/workflows/x.yml"),
        ({"verb": "override", "kpi": "tone", "case": "run-0/x", "label": "fail"}, "cassettes/a"),
    ],
)
def test_a_path_outside_the_command_s_row_refuses_the_whole_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: dict[str, object], path: str
) -> None:
    write = {"cassettes/baseline.json": b"{}\n"} if command is ACCEPT else {}
    out = _folder(tmp_path, command, write)
    changes = json.loads((out / "changes.json").read_text(encoding="utf-8"))
    changes["write" if "action" in path or "graph" in path else "remove"].append(path)
    (out / "changes.json").write_text(json.dumps(changes), encoding="utf-8")
    api = Api()
    assert _commit(out, api, monkeypatch) == 0
    assert api.git_calls() == []
    assert api.reply().startswith("Not run: the command changed a file it may not write")


def test_a_reply_already_written_is_posted_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "event.json").write_text('{"pr": 12, "comment_id": 123}', encoding="utf-8")
    (tmp_path / "reply.md").write_text("Not run: the PR is closed.\n", encoding="utf-8")
    api = Api()
    assert _commit(tmp_path, api, monkeypatch) == 0
    assert api.git_calls() == [] and api.reply() == "Not run: the PR is closed.\n"


def test_a_comment_that_wasn_t_a_command_gets_no_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api()
    assert _commit(tmp_path, api, monkeypatch) == 0
    assert api.calls == []


def test_github_trouble_exits_2_and_tries_to_say_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _folder(tmp_path, ACCEPT, {"cassettes/baseline.json": b"{}\n"})
    api = Api(fail={("POST", f"/repos/{REPO}/git/blobs"): 502})
    assert _commit(out, api, monkeypatch) == 2
    assert api.reply().startswith("Not run: `GitHub answered 502 to POST /repos/octo-org/agent/")
    assert ("PATCH", f"/repos/{REPO}/git/refs/heads/feat/x") not in api.git_calls()


def test_the_command_line_needs_a_token_and_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert _review_commit(["--dir", str(tmp_path)], Api()) == 2
    assert "GH_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert _review_commit(["--dir", str(tmp_path)], Api()) == 2
    assert "GITHUB_REPOSITORY" in capsys.readouterr().err
