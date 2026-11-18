"""T154: workflows can't hand a PR's code a way to grade itself."""

from pathlib import Path

import pytest
import yaml

from lgcov.workflow_lint import workflow_problems

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
CANDIDATE = """\
on: pull_request
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def _problems(text: str) -> list[str]:
    return workflow_problems(yaml.safe_load(text))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_this_repo_s_workflows_pass(path: Path) -> None:
    assert _problems(path.read_text(encoding="utf-8")) == []


def test_there_are_workflows_to_lint() -> None:
    assert {p.name for p in WORKFLOWS} >= {
        "ci.yml",
        "graph-diff.yml",
        "lgcov-review.yml",
        "release.yml",
    }


def test_a_candidate_job_with_no_secrets_passes() -> None:
    assert _problems(CANDIDATE) == []


@pytest.mark.parametrize(
    "trigger",
    [
        "pull_request_target",
        "[push, pull_request_target]",
        "{pull_request_target: {types: [opened]}}",
    ],
    ids=["string", "list", "mapping"],
)
def test_pull_request_target_fails_in_every_form(trigger: str) -> None:
    text = CANDIDATE.replace("on: pull_request", f"on: {trigger}")
    assert any("pull_request_target" in p for p in _problems(text))


@pytest.mark.parametrize(
    "extra",
    [
        "        env: {KEY: '${{ secrets.KEY }}'}\n",
        "        with: {token: '${{ secrets.KEY }}'}\n",
        "        with: {all: '${{ toJSON(secrets) }}'}\n",
        "      - run: echo ${{ secrets['KEY'] }}\n",
    ],
    ids=["env", "with", "toJSON", "run"],
)
def test_a_secret_in_a_job_that_runs_pr_code_fails(extra: str) -> None:
    assert _problems(CANDIDATE + extra) == [
        "job gate runs the PR's code and can read a secret; that code could send it anywhere"
    ]


def test_a_job_level_env_secret_fails() -> None:
    text = CANDIDATE.replace("    steps:", "    env: {KEY: '${{ secrets.KEY }}'}\n    steps:")
    assert len(_problems(text)) == 1


def test_a_workflow_level_env_secret_reaches_the_candidate_job() -> None:
    assert len(_problems("env: {KEY: '${{ secrets.KEY }}'}\n" + CANDIDATE)) == 1


def test_secrets_inherit_fails_in_a_job_that_runs_pr_code() -> None:
    text = """\
on: pull_request
jobs:
  gate:
    uses: ./.github/workflows/gate.yml
    secrets: inherit
"""
    assert len(_problems(text)) == 1


@pytest.mark.parametrize(
    "uses",
    ["./", "AMOHYZ/langgraph-regression-guard@v0"],
    ids=["this checkout", "the published Action"],
)
def test_a_job_that_runs_the_action_counts_as_running_pr_code(uses: str) -> None:
    text = f"""\
on: pull_request
jobs:
  gate:
    steps:
      - uses: {uses}
        env: {{KEY: '${{{{ secrets.KEY }}}}'}}
"""
    assert len(_problems(text)) == 1


def test_a_job_on_the_base_commit_may_hold_a_secret() -> None:
    text = """\
on: pull_request
jobs:
  evaluate:
    steps:
      - uses: actions/checkout@v4
        with: {ref: '${{ github.event.pull_request.base.sha }}'}
      - run: lgcov judge
        env: {KEY: '${{ secrets.KEY }}'}
"""
    assert _problems(text) == []


def test_a_workflow_no_pr_can_trigger_may_hold_a_secret() -> None:
    text = "on: {push: {tags: ['v*']}}\n" + CANDIDATE.split("\n", 1)[1]
    assert _problems(text + "      - run: echo ${{ secrets.KEY }}\n") == []


def test_the_word_secrets_in_a_script_is_not_a_secret() -> None:
    assert _problems(CANDIDATE + "      - run: echo 'no secrets here'\n") == []


# T179: the two-job layout. The candidate job runs the PR's code, so it holds nothing worth taking,
# and the job that decides reads no secret from a checkout of the PR.
TWO_JOBS = """\
on: pull_request
permissions:
  contents: read
jobs:
  candidate:
    steps:
      - uses: actions/checkout@v4
        with: {persist-credentials: false}
      - uses: AMOHYZ/langgraph-regression-guard@v0
        with: {phase: candidate}
  verdict:
    needs: candidate
    if: ${{ !cancelled() }}
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with: {ref: '${{ github.event.pull_request.base.sha }}'}
      - uses: AMOHYZ/langgraph-regression-guard@v0
        with: {phase: evaluate}
        env: {KEY: '${{ secrets.KEY }}'}
"""


def test_the_two_job_layout_passes() -> None:
    assert _problems(TWO_JOBS) == []


def test_a_secret_in_the_candidate_job_fails() -> None:
    text = TWO_JOBS.replace(
        "with: {phase: candidate}\n",
        "with: {phase: candidate}\n        env: {KEY: '${{ secrets.KEY }}'}\n",
    )
    assert _problems(text) == [
        "job candidate runs the PR's code and can read a secret; that code could send it anywhere"
    ]


def test_a_secret_in_the_job_that_decides_fails_on_a_checkout_of_the_pr() -> None:
    text = TWO_JOBS.replace("with: {ref: '${{ github.event.pull_request.base.sha }}'}\n", "\n")
    assert _problems(text) == [
        "job verdict runs the PR's code and can read a secret; that code could send it anywhere"
    ]


def test_a_candidate_job_that_keeps_its_token_in_the_checkout_fails() -> None:
    text = TWO_JOBS.replace("        with: {persist-credentials: false}\n", "")
    assert _problems(text) == [
        "job candidate leaves its token in .git/config, where the PR's code can read it; "
        "set persist-credentials: false"
    ]


@pytest.mark.parametrize(
    "permissions",
    [
        "permissions:\n  contents: read\n  pull-requests: write\n",
        "permissions: write-all\n",
        "",
    ],
    ids=["a write scope", "write-all", "the repo's default"],
)
def test_a_candidate_job_that_can_hold_a_write_token_fails(permissions: str) -> None:
    text = TWO_JOBS.replace("permissions:\n  contents: read\njobs:", f"{permissions}jobs:")
    assert _problems(text) == [
        "job candidate runs the PR's code and can hold a write token; "
        "set permissions: contents: read"
    ]


def test_a_job_level_read_scope_is_enough_for_the_candidate() -> None:
    text = TWO_JOBS.replace("permissions:\n  contents: read\njobs:", "jobs:").replace(
        "  candidate:\n", "  candidate:\n    permissions: {contents: read}\n"
    )
    assert _problems(text) == []


@pytest.mark.parametrize(
    "condition",
    ["", "    if: ${{ success() }}\n"],
    ids=["no condition", "only on success"],
)
def test_a_job_that_decides_and_can_be_skipped_fails(condition: str) -> None:
    # GitHub counts a skipped required check as passing, so a killed candidate job would pass it.
    text = TWO_JOBS.replace("    if: ${{ !cancelled() }}\n", condition)
    assert _problems(text) == [
        "job verdict decides the PR but is skipped if the candidate job fails, and GitHub counts "
        "a skipped check as passing; add if: ${{ !cancelled() }}"
    ]


def test_always_also_runs_the_job_that_decides() -> None:
    assert _problems(TWO_JOBS.replace("!cancelled()", "always()")) == []


def test_this_rule_is_not_for_one_job() -> None:
    assert _problems(CANDIDATE + "      - uses: ./\n") == []


def test_the_readme_s_two_job_layout_passes() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = [b.split("```", 1)[0] for b in readme.split("```yaml\n")[1:]]
    layouts = [b for b in blocks if "phase: candidate" in b]
    assert len(layouts) == 1
    assert _problems(layouts[0]) == []


# T186: a workflow that runs on `issue_comment` holds a write token and runs from the default
# branch, so it must start only for a maintainer, read the comment only in an `if`, and check out
# and run nothing of the PR (docs/review.md, "What the lint checks").
REVIEW_DOC = (ROOT / "docs" / "review.md").read_text(encoding="utf-8")
REVIEW = REVIEW_DOC.split("```yaml\n", 1)[1].split("```", 1)[0]


def test_the_doc_s_review_workflow_passes() -> None:
    assert "issue_comment" in REVIEW
    assert _problems(REVIEW) == []


def _with(old: str, new: str) -> str:
    assert old in REVIEW
    return REVIEW.replace(old, new)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (_with("    types: [created]\n", ""), "issue_comment: types must be exactly [created]"),
        (_with("[created]", "[created, edited]"), "types must be exactly [created]"),
        (_with("issue_comment:\n    types: [created]", "issue_comment:"), "exactly [created]"),
        (_with("permissions: {}\n\n", ""), "sets no top-level permissions"),
        (
            _with("      github.event.issue.pull_request &&\n", ""),
            "job review's `if` must read github.event.issue.pull_request",
        ),
        (
            _with("github.event.comment.author_association)", "'OWNER')"),
            "job review's `if` must read github.event.comment.author_association",
        ),
        (
            _with("          persist-credentials: false\n", "          ref: main\n"),
            "job review checks out with `ref`",
        ),
        (
            _with("          persist-credentials: false\n", "          repository: o/r\n"),
            "job review checks out with `repository`",
        ),
        (
            _with("        with:\n          persist-credentials: false\n", ""),
            "job review leaves its token in .git/config",
        ),
        (_with("uses: ./review", "uses: ./"), "job review runs the gate Action (./)"),
        (_with("uses: ./review", "uses: ./."), "job review runs the gate Action (./.)"),
        (
            _with("uses: ./review", "uses: AMOHYZ/langgraph-regression-guard@v0.4.0"),
            "job review runs the gate Action (AMOHYZ/langgraph-regression-guard@v0.4.0)",
        ),
    ],
)
def test_a_review_workflow_that_breaks_a_rule_fails(text: str, why: str) -> None:
    assert any(why in problem for problem in _problems(text)), _problems(text)


@pytest.mark.parametrize(
    "where",
    [
        '      - run: echo "${{ github.event.comment.body }}"\n',
        "      - run: ./parse\n        env:\n          BODY: ${{ github.event.comment.body }}\n",
        "      - uses: actions/github-script@v7\n"
        "        with:\n          script: console.log(`${{ github.event.issue.title }}`)\n",
        "      - run: echo ok\n        name: ${{ github.event.issue.body }}\n",
    ],
    ids=["run", "env", "script", "name"],
)
def test_the_comment_read_anywhere_but_an_if_fails(where: str) -> None:
    problems = _problems(REVIEW + where)
    assert any("reads the comment outside an `if`" in p for p in problems), problems


def test_the_comment_read_in_a_step_s_if_passes() -> None:
    step = "      - if: startsWith(github.event.comment.body, '/lgcov ')\n        run: echo ok\n"
    assert _problems(REVIEW + step) == []


@pytest.mark.parametrize(
    "uses",
    [
        "./review/",
        "AMOHYZ/langgraph-regression-guard/review@v0.5.0",
        "actions/setup-python@v5",
    ],
)
def test_the_review_action_and_other_actions_pass(uses: str) -> None:
    assert _problems(_with("uses: ./review", f"uses: {uses}")) == []


def test_a_job_that_calls_a_reusable_workflow_fails() -> None:
    text = (
        REVIEW + "  other:\n    if: ${{ github.event.issue.pull_request && "
        "github.event.comment.author_association == 'OWNER' }}\n"
        "    uses: ./.github/workflows/reusable.yml\n"
    )
    assert any("job other calls a reusable workflow" in p for p in _problems(text))


def test_a_workflow_on_another_event_is_left_to_its_own_rules() -> None:
    assert _problems(CANDIDATE.replace("on: pull_request", "on: push") + "      - uses: ./\n") == []
