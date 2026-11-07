"""T179: action.yml's steps, run for real. A small runner reads the file, works out each step's
`if` and `env`, and runs its script under GitHub's bash flags, with real git and the real lgcov, so
this covers what a test of one step can't: what one step hands the next, and what one job can and
can't do to another. Only uv and the actions the steps name in `uses:` are stand-ins. Each job gets
its own folder, as it gets its own machine, and only the artifact crosses."""

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
GRAPH = (FIXTURES / "demo_graph.py").read_text(encoding="utf-8")
BASELINE = (FIXTURES / "demo_baseline.json").read_text(encoding="utf-8")
CONFIG = "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
DRIFTED = GRAPH.replace("a concise", "a friendly")
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c"]
ACTION: dict[str, object] = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))


def _inputs() -> dict[str, str]:
    specs = ACTION["inputs"]
    assert isinstance(specs, dict)
    return {f"inputs.{name}": str(spec.get("default", "")) for name, spec in specs.items()}


def _steps() -> list[dict[str, object]]:
    runs = ACTION["runs"]
    assert isinstance(runs, dict)
    steps: list[dict[str, object]] = runs["steps"]
    return steps


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@dataclass
class Job:
    """One job on its own machine: what it left behind, once it has run."""

    temp: Path
    outputs: dict[str, dict[str, str]] = field(default_factory=dict)
    ran: list[str] = field(default_factory=list)
    comment: str = ""
    code: int = 0
    log: str = ""


@dataclass
class Repo:
    """A remote with a base commit and a PR's merge commit, and the runner's shared pieces."""

    root: Path
    origin: Path
    base: str
    head: str
    merge: str
    env: dict[str, str]

    def workspace(self, name: str, sha: str) -> Path:
        """A checkout as actions/checkout leaves one: this commit alone, and no credentials."""
        ws = self.root / name / "workspace"
        ws.mkdir(parents=True)
        _git(ws, "init", "-q")
        _git(ws, "remote", "add", "origin", f"file://{self.origin}")
        _git(ws, "fetch", "-q", "--no-tags", "--depth=1", "origin", sha)
        _git(ws, "checkout", "-q", "--detach", "FETCH_HEAD")
        return ws

    def job(self, name: str, ws: Path, phase: str, event: str = "pull_request") -> Job:
        job = Job(self.root / name / "temp")
        job.temp.mkdir(parents=True)
        pull = {"base": {"sha": self.base}, "head": {"sha": self.head}}
        payload = {"pull_request": pull} if event == "pull_request" else {}
        github = job.temp / "event.json"
        github.write_text(json.dumps(payload), encoding="utf-8")
        values = {
            **_inputs(),
            "inputs.phase": phase,
            "inputs.github-token": "s3cret",
            "github.action_path": str(ROOT),
            "github.event_name": event,
            "github.sha": self.merge if event == "pull_request" else self.base,
            "github.ref": "refs/pull/1/merge" if event == "pull_request" else "refs/heads/main",
            "github.repository": "o/r",
            "github.event.repository.default_branch": "main",
            "runner.temp": str(job.temp),
        }
        if event == "pull_request":
            values["github.event.pull_request.base.sha"] = self.base
            values["github.event.pull_request.head.sha"] = self.head
        for step in _steps():
            if not self._step(job, step, values, ws, github):
                break
        return job

    def _step(
        self,
        job: Job,
        step: dict[str, object],
        values: dict[str, str],
        ws: Path,
        event: Path,
    ) -> bool:
        def value(name: str) -> str:
            if name.startswith("steps."):
                _, step_id, _, key = name.split(".", 3)
                return job.outputs.get(step_id, {}).get(key, "")
            return values.get(name, "")

        def evaluate(expression: str) -> object:
            # The Actions expressions this file uses are Python once these are swapped. It only
            # ever reads action.yml.
            python = re.sub(
                r"\b(inputs|github|runner|steps)\.[\w.-]+",
                lambda m: f"value({m.group()!r})",
                expression.replace("&&", " and ").replace("||", " or "),
            )
            return eval(python, {"value": value, "format": lambda f, *a: f.format(*a)})

        def render(text: object) -> str:
            found = re.sub(r"\$\{\{(.+?)\}\}", lambda m: str(evaluate(m.group(1))), str(text))
            return found

        name = str(step["name"])
        if "if" in step and not evaluate(str(step["if"])):
            return True
        job.ran.append(name)
        if "uses" in step:
            return self._stand_in(job, step, render)
        env_spec = step.get("env") or {}
        assert isinstance(env_spec, dict)
        out = job.temp / "step_output"
        out.write_text("", encoding="utf-8")
        env = {
            **self.env,
            **{str(k): render(v) for k, v in env_spec.items()},
            "RUNNER_TEMP": str(job.temp),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_SHA": values["github.sha"],
        }
        cwd = render(step["working-directory"]) if "working-directory" in step else str(ws)
        done = subprocess.run(
            [*GITHUB_BASH, str(step["run"])], cwd=cwd, env=env, capture_output=True, text=True
        )
        job.log += f"--- {name}\n{done.stdout}{done.stderr}"
        if "id" in step:
            job.outputs[str(step["id"])] = _parsed(out.read_text(encoding="utf-8"))
        job.code = done.returncode
        return done.returncode == 0

    def _stand_in(self, job: Job, step: dict[str, object], render: Callable[[object], str]) -> bool:
        spec = step.get("with") or {}
        assert isinstance(spec, dict)
        uses = str(step["uses"])
        artifacts = self.root / "artifacts"
        if uses.startswith("actions/upload-artifact@"):
            source = Path(render(spec["path"]))
            if source.is_file():
                (artifacts / str(spec["name"])).mkdir(parents=True, exist_ok=True)
                shutil.copy(source, artifacts / str(spec["name"]) / source.name)
        elif uses.startswith("actions/download-artifact@"):
            source = artifacts / str(spec["name"])
            if source.is_dir():
                shutil.copytree(source, render(spec["path"]))
            else:
                assert step["continue-on-error"] is True
        elif uses.startswith("actions/github-script@"):
            env_spec = step["env"]
            assert isinstance(env_spec, dict)
            job.comment = Path(render(env_spec["REPORT_PATH"])).read_text(encoding="utf-8")
        else:
            assert uses.startswith("astral-sh/setup-uv@")
        return True


def _parsed(text: str) -> dict[str, str]:
    """What a step wrote to $GITHUB_OUTPUT, with `key<<DELIM` for lines that run on."""
    found: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line:
            key, delimiter = line.split("<<", 1)
            found[key] = "\n".join(iter(lambda: next(lines), delimiter))
        elif "=" in line:
            key, value = line.split("=", 1)
            found[key] = value
    return found


MakeRepo = Callable[[dict[str, str]], Repo]


@pytest.fixture
def make_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MakeRepo:
    """Make the remote: the demo on main, and a PR that writes `pr` over it. Its merge commit is
    what GitHub checks out, and the base has moved on since the PR branched."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")

    def make(pr: dict[str, str]) -> Repo:
        origin = tmp_path / "origin"
        origin.mkdir()
        _git(origin, "init", "-q", "-b", "main")
        _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        _commit(
            origin,
            {"graph.py": GRAPH, "cassettes/baseline.json": BASELINE, "lgcov.yaml": CONFIG},
            "start",
        )
        _git(origin, "checkout", "-q", "-b", "the-pr")
        head = _commit(origin, pr, "the PR")
        _git(origin, "checkout", "-q", "main")
        base = _commit(origin, {"unrelated.txt": "the base moved on\n"}, "after the PR branched")
        repo = Repo(tmp_path, origin, base, head, "", _runner_env(tmp_path))
        repo.merge = _merged(repo)
        return repo

    return make


def _merged(repo: Repo) -> str:
    """GitHub's merge commit for the PR. It leaves the remote's work tree on the base."""
    _git(repo.origin, "checkout", "-q", "--detach", repo.base)
    _git(repo.origin, "merge", "-q", "--no-ff", "-m", "GitHub's merge commit", "the-pr")
    merge = _git(repo.origin, "rev-parse", "HEAD")
    _git(repo.origin, "checkout", "-q", "--detach", repo.base)
    return merge


_NOT_ON_A_RUNNER = ("GITHUB_", "UV_", "VIRTUAL_ENV")


def _runner_env(tmp_path: Path) -> dict[str, str]:
    """A runner's environment: no git config of yours, and stand-ins for uv and uvx that lay out
    what they would install, with the real lgcov in it."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '  sync|venv) bin="${UV_PROJECT_ENVIRONMENT:-.venv}/bin"; mkdir -p "$bin"; '
        'ln -sf "$LGCOV_REAL" "$bin/lgcov" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (bin_dir / "uvx").write_text(
        '#!/usr/bin/env bash\nshift 2; shift; exec "$LGCOV_REAL" "$@"\n', encoding="utf-8"
    )
    for tool in ("uv", "uvx"):
        (bin_dir / tool).chmod(0o755)
    return {
        # Nothing of the shell that runs these tests: its UV_PROJECT_ENVIRONMENT would send the
        # stand-in for uv to overwrite the lgcov it links to.
        **{k: v for k, v in os.environ.items() if not k.startswith(_NOT_ON_A_RUNNER)},
        "GIT_TERMINAL_PROMPT": "0",
        "LGCOV_REAL": str(Path(sys.executable).parent / "lgcov"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }


def _two_jobs(repo: Repo) -> tuple[Job, Job]:
    candidate = repo.job("candidate", repo.workspace("candidate", repo.merge), "candidate")
    evaluate = repo.job("evaluate", repo.workspace("evaluate", repo.base), "evaluate")
    return candidate, evaluate


def _no_verdict(job: Job, why: str) -> None:
    assert job.code == 2, job.log
    assert job.outputs["lgcov"]["verdict"] == "error"
    assert job.comment.splitlines()[1] == "## ⚠️ lgcov: harness error, not a regression"
    assert "🟢" not in job.comment
    assert why in job.comment


def test_two_jobs_block_a_pr_that_drifts(make_repo: MakeRepo) -> None:
    candidate, evaluate = _two_jobs(make_repo({"graph.py": DRIFTED}))
    assert candidate.code == 0 and candidate.comment == ""
    assert candidate.ran[-1] == "Upload what this job did"
    assert evaluate.code == 1, evaluate.log
    assert evaluate.outputs["verify"] == {"exit-code": "1", "verdict": "fail"}
    assert "| `respond` | root | 2/2 |" in evaluate.comment
    assert "verdict: **fail**" in evaluate.comment


def test_two_jobs_pass_a_pr_that_changes_nothing(make_repo: MakeRepo) -> None:
    repo = make_repo({"notes.txt": "nothing to do with the graph\n"})
    candidate, evaluate = _two_jobs(repo)
    assert candidate.code == 0
    assert evaluate.code == 0, evaluate.log
    assert evaluate.outputs["verify"] == {"exit-code": "0", "verdict": "pass"}
    assert "🟢" in evaluate.comment
    assert f"base `{repo.base[:7]}`, candidate `{repo.merge[:7]}`" in evaluate.comment


def test_one_job_still_does_all_of_it(make_repo: MakeRepo) -> None:
    repo = make_repo({"graph.py": DRIFTED})
    job = repo.job("one", repo.workspace("one", repo.merge), "all")
    assert job.code == 1, job.log
    assert "Install your project and lgcov" in job.ran
    assert "| `respond` | root | 2/2 |" in job.comment


def test_a_fetch_in_the_job_that_runs_the_pr_s_code_comes_before_that_code(
    make_repo: MakeRepo,
) -> None:
    candidate, _ = _two_jobs(make_repo({"notes.txt": "x\n"}))
    assert candidate.ran.index("Check out the commits") < candidate.ran.index(
        "Install your project and lgcov"
    )


MARKS_ITS_JOB = """\
import os
from pathlib import Path

Path(os.environ["RUNNER_TEMP"], "the_pr_s_code_ran_here").touch()
"""


def test_the_pr_s_code_runs_in_one_job_only(make_repo: MakeRepo) -> None:
    candidate, evaluate = _two_jobs(make_repo({"graph.py": MARKS_ITS_JOB + GRAPH}))
    assert (candidate.temp / "the_pr_s_code_ran_here").exists(), candidate.log
    assert not (evaluate.temp / "the_pr_s_code_ran_here").exists()
    assert evaluate.code == 0, evaluate.log


ATTACKS_ITS_JOB = """\
import atexit
import os
from pathlib import Path


def _at_the_end() -> None:
    # What gets a PR past the gate when this code shares its job.
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.write("exit-code=0\\nverdict=pass\\n")
    Path(os.environ["RUNNER_TEMP"], "lgcov-base", "lgcov.yaml").write_text("tier: nightly\\n")
    Path(os.environ["RUNNER_TEMP"], "lgcov").mkdir(exist_ok=True)
    Path(os.environ["RUNNER_TEMP"], "lgcov", "result.json").write_text("{}")


atexit.register(_at_the_end)
"""


def test_what_the_pr_s_code_does_to_its_own_job_does_not_reach_the_other(
    make_repo: MakeRepo,
) -> None:
    candidate, evaluate = _two_jobs(make_repo({"graph.py": ATTACKS_ITS_JOB + DRIFTED}))
    assert candidate.outputs["lgcov"]["exit-code"] == "0"  # it did write them, over there
    assert evaluate.code == 1, evaluate.log
    assert evaluate.outputs["verify"] == {"exit-code": "1", "verdict": "fail"}


def test_a_candidate_job_that_uploads_nothing_gives_no_verdict(make_repo: MakeRepo) -> None:
    repo = make_repo({"graph.py": DRIFTED})
    evaluate = repo.job("evaluate", repo.workspace("evaluate", repo.base), "evaluate")
    _no_verdict(evaluate, "The candidate job left no result")


def test_a_forged_upload_gives_no_verdict(make_repo: MakeRepo) -> None:
    repo = make_repo({"graph.py": DRIFTED})
    candidate = repo.job("candidate", repo.workspace("candidate", repo.merge), "candidate")
    assert candidate.code == 0
    upload = repo.root / "artifacts" / "lgcov-candidate" / "candidate.json"
    body = json.loads(upload.read_text(encoding="utf-8"))
    upload.write_text(json.dumps({**body, "verdict": "pass"}), encoding="utf-8")
    evaluate = repo.job("evaluate", repo.workspace("evaluate", repo.base), "evaluate")
    _no_verdict(evaluate, "The candidate job's result can't be read")


def test_a_symlink_in_the_pr_can_t_read_this_job_s_files(
    make_repo: MakeRepo, tmp_path: Path
) -> None:
    secret = tmp_path / "a_secret_file.json"
    secret.write_text(json.dumps({"runs": [], "token": "s3cret"}), encoding="utf-8")
    repo = make_repo({"notes.txt": "x\n"})
    _git(repo.origin, "checkout", "-q", "the-pr")
    cassette = repo.origin / "cassettes" / "baseline.json"
    cassette.unlink()
    cassette.symlink_to(secret)
    repo.head = _commit(repo.origin, {}, "a symlink to this job's files")
    repo.merge = _merged(repo)
    _, evaluate = _two_jobs(repo)
    _no_verdict(evaluate, "cannot read baseline")
    assert "s3cret" not in evaluate.comment
    assert not (evaluate.temp / "lgcov-pr" / "cassettes" / "baseline.json").is_symlink()


def test_a_push_runs_both_jobs_with_no_base(make_repo: MakeRepo) -> None:
    repo = make_repo({"notes.txt": "x\n"})
    candidate = repo.job("candidate", repo.workspace("candidate", repo.base), "candidate", "push")
    evaluate = repo.job("evaluate", repo.workspace("evaluate", repo.base), "evaluate", "push")
    assert candidate.code == 0, candidate.log
    assert evaluate.code == 0, evaluate.log
    assert "Verify the result" not in evaluate.ran
    assert "Upload coverage for main" not in candidate.ran


def test_a_uv_setting_in_this_shell_does_not_reach_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "the-env-running-these-tests"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "the-env-running-these-tests"))
    env = _runner_env(tmp_path)
    assert "UV_PROJECT_ENVIRONMENT" not in env and "VIRTUAL_ENV" not in env
