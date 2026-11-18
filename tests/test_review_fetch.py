"""T186: `lgcov review fetch` against a fake GitHub API: what it reads, what it writes, and every
reason it refuses (docs/review.md, "When the bot refuses"). Nothing touches the network."""

import base64
import hashlib
import io
import json
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lgcov import review_fetch
from lgcov.cli import _review_fetch
from lgcov.github_api import API_URL, GitHub
from lgcov.review_command import comment_event
from lgcov.review_fetch import fetch

REPO = "octo-org/agent"
H, B = "a" * 40, "b" * 40
CONFIG = b"graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
RECORDED, ACCEPTED = b'{"runs": ["recorded"]}\n', b'{"runs": ["accepted"]}\n'
CANDIDATE = b'{"schema": 2}\n'


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@dataclass
class Answer:
    status_code: int = 200
    body: object = None
    content: bytes = b""

    def json(self) -> object:
        return self.body


@dataclass
class Api:
    """A fake GitHub: answers by path, built from the fields below; records each GET path."""

    permission: str = "write"
    body_now: str | None = "/lgcov accept respond"
    pull: dict[str, object] = field(
        default_factory=lambda: {
            "state": "open",
            "merged": False,
            "changed_files": 2,
            "head": {"ref": "feat/x", "sha": H, "repo": {"full_name": REPO}},
            "base": {"sha": B, "repo": {"default_branch": "main"}},
        }
    )
    changed: list[dict[str, object]] = field(
        default_factory=lambda: [{"filename": "graph.py"}, {"filename": "cassettes/baseline.json"}]
    )
    artifacts: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "id": 7,
                "expired": False,
                "updated_at": "2026-11-17T10:20:00Z",
                "workflow_run": {"id": 70, "head_sha": H},
            }
        ]
    )
    run: dict[str, object] = field(
        default_factory=lambda: {
            "status": "completed",
            "event": "pull_request",
            "head_sha": H,
            "updated_at": "2026-11-17T10:21:00Z",
            "pull_requests": [{"number": 12, "base": {"sha": B}}],
        }
    )
    base_files: dict[str, tuple[str, bytes]] = field(
        default_factory=lambda: {
            "lgcov.yaml": ("100644", CONFIG),
            "cassettes/baseline.json": ("100644", RECORDED),
        }
    )
    head_files: dict[str, tuple[str, bytes]] = field(
        default_factory=lambda: {
            "lgcov.yaml": ("100644", CONFIG),
            "cassettes/baseline.json": ("100644", ACCEPTED),
            "graph.py": ("100644", b"graph = 1\n"),
        }
    )
    truncated: bool = False
    archive: bytes = field(default_factory=lambda: _zip({"candidate.json": CANDIDATE}))
    fail: dict[str, int] = field(default_factory=dict)  # path -> status
    gets: list[str] = field(default_factory=list)

    def _tree(self, files: dict[str, tuple[str, bytes]]) -> dict[str, object]:
        entries = [
            {
                "path": p,
                "mode": m,
                "type": "commit" if m == "160000" else "blob",
                "sha": _sha(d),
                "size": len(d),
            }
            for p, (m, d) in files.items()
        ]
        return {
            "truncated": self.truncated,
            "tree": [{"path": "cassettes", "type": "tree"}, *entries],
        }

    def _routes(self) -> dict[str, Callable[[], object]]:
        blobs = {
            _sha(d): d for files in (self.base_files, self.head_files) for _, d in files.values()
        }
        routes: dict[str, Callable[[], object]] = {
            f"/repos/{REPO}/pulls/12": lambda: self.pull,
            f"/repos/{REPO}/pulls/12/files": lambda: self.changed,
            f"/repos/{REPO}/collaborators/maintainer/permission": lambda: {
                "role_name": self.permission
            },
            f"/repos/{REPO}/issues/comments/123": lambda: {"body": self.body_now},
            f"/repos/{REPO}/actions/artifacts": lambda: {"artifacts": self.artifacts},
            f"/repos/{REPO}/actions/runs/70": lambda: self.run,
            f"/repos/{REPO}/git/trees/{B}": lambda: self._tree(self.base_files),
            f"/repos/{REPO}/git/trees/{H}": lambda: self._tree(self.head_files),
        }
        for sha, data in blobs.items():
            routes[f"/repos/{REPO}/git/blobs/{sha}"] = lambda d=data: {  # type: ignore[misc]  # a default binds each blob
                "encoding": "base64",
                "content": base64.b64encode(d).decode(),
            }
        return routes

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
        assert method == "GET", "fetch only reads"
        self.gets.append(path)
        if path in self.fail:
            return Answer(self.fail[path])
        if path == f"/repos/{REPO}/actions/artifacts/7/zip":
            return Answer(content=self.archive)
        if path == f"/repos/{REPO}/issues/comments/123" and self.body_now is None:
            return Answer(404)
        route = self._routes().get(path)
        return Answer(body=route()) if route else Answer(404)


def _event(body: str = "/lgcov accept respond", association: str = "OWNER") -> dict[str, object]:
    return {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": 12, "pull_request": {"url": "u"}},
        "comment": {
            "id": 123,
            "html_url": f"https://github.com/{REPO}/pull/12#issuecomment-123",
            "body": body,
            "created_at": "2026-11-17T10:25:30Z",
            "author_association": association,
            "user": {"login": "maintainer", "type": "User"},
        },
    }


def _fetch(api: Api, tmp_path: Path, body: str = "/lgcov accept respond") -> tuple[int, Path]:
    out = tmp_path / "out"
    out.mkdir()
    code = fetch(GitHub("tok", client=api), comment_event(_event(body)), out).code
    return code, out


def _reply(out: Path) -> str:
    return (out / "reply.md").read_text(encoding="utf-8")


def test_accept_fetches_the_command_the_candidate_file_and_both_sides_files(
    tmp_path: Path,
) -> None:
    code, out = _fetch(Api(), tmp_path)
    assert code == 0 and not (out / "reply.md").exists()
    meta = json.loads((out / "command.json").read_text(encoding="utf-8"))
    assert meta == {
        "command": {"verb": "accept", "nodes": ["respond"]},
        "pr": 12,
        "head_sha": H,
        "head_ref": "feat/x",
        "base_sha": B,
        "cassette_dir": "cassettes",
        "reviewer": "maintainer",
        "comment_id": 123,
        "comment_url": f"https://github.com/{REPO}/pull/12#issuecomment-123",
    }
    assert (out / "candidate.json").read_bytes() == CANDIDATE
    assert (out / "pr" / "cassettes" / "baseline.json").read_bytes() == ACCEPTED
    assert not (out / "pr" / "graph.py").exists()  # only the folder the command reads
    assert (out / "base" / "lgcov.yaml").read_bytes() == CONFIG
    assert json.loads((out / "fetched.json").read_text(encoding="utf-8")) == {
        "cassettes/baseline.json": _sha(ACCEPTED)
    }


def test_reject_fetches_both_folders_and_no_report(tmp_path: Path) -> None:
    api = Api(body_now="/lgcov reject")
    code, out = _fetch(api, tmp_path, "/lgcov reject")
    assert code == 0 and not (out / "reply.md").exists()
    assert (out / "base" / "cassettes" / "baseline.json").read_bytes() == RECORDED
    assert (out / "pr" / "cassettes" / "baseline.json").read_bytes() == ACCEPTED
    assert not (out / "candidate.json").exists()
    assert not any("actions" in path for path in api.gets)


def test_a_comment_that_isn_t_a_command_writes_nothing(tmp_path: Path) -> None:
    api = Api()
    code, out = _fetch(api, tmp_path, "LGTM")
    assert code == 0 and list(out.iterdir()) == [] and api.gets == []


def test_a_non_maintainer_s_comment_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    event = comment_event(_event(association="CONTRIBUTOR"))
    assert fetch(GitHub("tok", client=Api()), event, out).code == 0
    assert list(out.iterdir()) == []


def test_a_line_that_doesn_t_parse_is_answered_with_its_reason(tmp_path: Path) -> None:
    api = Api()
    code, out = _fetch(api, tmp_path, "/lgcov accept")
    assert code == 0 and _reply(out).startswith("Not run: it names no node.")
    assert api.gets == []  # refused before asking GitHub anything


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda a: setattr(a, "permission", "read"), "can't push to this repo"),
        (lambda a: setattr(a, "body_now", None), "was deleted"),
        (lambda a: setattr(a, "body_now", "/lgcov accept all"), "was edited"),
        (lambda a: a.pull.update(state="closed"), "is closed"),
        (lambda a: a.changed.__setitem__(0, {"filename": ".github/workflows/x.yml"}), "touches"),
        (
            lambda a: a.changed.__setitem__(
                0, {"filename": "b.py", "previous_filename": "x/action.yml"}
            ),
            "touches .github/",
        ),
        (lambda a: a.pull.update(changed_files=3000), "more files than GitHub lists"),
        (lambda a: setattr(a, "artifacts", []), "no lgcov report"),
        (lambda a: a.artifacts[0].update(expired=True), "has expired"),
        (lambda a: a.run.update(updated_at="2026-11-17T10:30:00Z"), "changed after your comment"),
        (lambda a: a.run.update(pull_requests=[]), "no lgcov report"),
    ],
)
def test_each_check_refuses_with_a_reply_and_no_command_file(
    tmp_path: Path, change: Callable[[Api], object], why: str
) -> None:
    api = Api()
    change(api)
    code, out = _fetch(api, tmp_path)
    assert code == 0 and why in _reply(out), _reply(out)
    assert not (out / "command.json").exists()


def test_a_fork_is_refused_with_the_local_commands(tmp_path: Path) -> None:
    api = Api()
    api.pull["head"] = {"ref": "feat/x", "sha": H, "repo": {"full_name": "someone/agent"}}
    code, out = _fetch(api, tmp_path)
    assert "comes from a fork" in _reply(out)
    assert "`lgcov cassette accept --nodes respond`" in _reply(out)


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_a_symlink_or_a_submodule_in_the_folder_is_refused(tmp_path: Path, mode: str) -> None:
    api = Api()
    api.head_files["cassettes/linked.json"] = (mode, b"/etc/hosts")
    code, out = _fetch(api, tmp_path)
    assert code == 0 and "symlink or a submodule" in _reply(out)
    assert not (out / "command.json").exists()


def test_an_action_file_in_the_folder_is_refused(tmp_path: Path) -> None:
    api = Api()
    api.head_files["cassettes/action.yml"] = ("100644", b"runs: {}\n")
    code, out = _fetch(api, tmp_path)
    assert "a file the bot may not write" in _reply(out)


def test_a_cut_short_tree_is_refused(tmp_path: Path) -> None:
    api = Api(truncated=True)
    code, out = _fetch(api, tmp_path)
    assert "cut short" in _reply(out)


@pytest.mark.parametrize(("cap", "value"), [("FILE_BYTES", 8), ("FILES", 1)])
def test_a_folder_past_a_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: str, value: int
) -> None:
    monkeypatch.setattr(review_fetch, cap, value)
    api = Api()
    api.head_files["cassettes/extra.json"] = ("100644", b'{"more": "than eight bytes"}')
    code, out = _fetch(api, tmp_path)
    assert code == 0 and "holds more than the bot reads" in _reply(out)
    assert not (out / "command.json").exists()


def test_a_cassette_dir_the_bot_may_not_write_is_refused(tmp_path: Path) -> None:
    api = Api()
    api.base_files["lgcov.yaml"] = ("100644", b"cassette_dir: .github/cassettes\n")
    code, out = _fetch(api, tmp_path)
    assert "may not write" in _reply(out)


def test_a_base_with_no_lgcov_yaml_reads_the_default_folder(tmp_path: Path) -> None:
    api = Api()
    del api.base_files["lgcov.yaml"]
    code, out = _fetch(api, tmp_path)
    assert code == 0 and (out / "pr" / "cassettes" / "baseline.json").exists()
    assert json.loads((out / "command.json").read_text())["cassette_dir"] == "cassettes"


@pytest.mark.parametrize(
    "members",
    [{"other.json": b"{}"}, {"../candidate.json": b"{}"}],
    ids=["no candidate.json", "a path out of the folder"],
)
def test_an_artifact_without_candidate_json_is_refused(
    tmp_path: Path, members: dict[str, bytes]
) -> None:
    api = Api(archive=_zip(members))
    code, out = _fetch(api, tmp_path)
    assert "holds no candidate.json" in _reply(out)
    assert not (tmp_path / "candidate.json").exists()


def test_github_trouble_exits_2_and_says_so(tmp_path: Path) -> None:
    api = Api(fail={f"/repos/{REPO}/pulls/12": 502})
    code, out = _fetch(api, tmp_path)
    assert code == 2
    assert _reply(out).startswith("Not run: GitHub answered 502 to GET /repos/octo-org/agent/")
    assert "tok" not in _reply(out)


def test_the_command_line_needs_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(_event()), encoding="utf-8")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert _review_fetch(["--event", str(event), "--out", str(tmp_path / "out")]) == 2
    assert "GH_TOKEN isn't set" in capsys.readouterr().err


def test_the_command_line_fills_the_folder_and_prints_only_its_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(_event()), encoding="utf-8")
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    out = tmp_path / "out"
    assert _review_fetch(["--event", str(event), "--out", str(out)], client=Api()) == 0
    assert capsys.readouterr().out == "lgcov review fetch: ready\n"
    assert (out / "command.json").exists()
