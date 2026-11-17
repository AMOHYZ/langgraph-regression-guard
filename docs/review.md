# Review commands on a PR

**A commit made by a review command has no lgcov gate result.** Make the lgcov check required on
your default branch, or such a commit can be merged ungated. With two jobs, require the check of
the job that decides: `report` in this repo's own workflow.
[Why, and how](#the-bots-commit-has-no-gate-result).

**Not built yet.** This page is the design the next changes build to, and it says so here until
the workflow ships. Until then, review on the command line with `lgcov cassette accept` and
`lgcov review override`.

A maintainer answers lgcov's PR report with a comment, and a workflow turns the comment into one
commit on the PR branch:

| Comment | What the commit does | On the command line |
|---|---|---|
| `/lgcov accept respond` | The node's outputs on this PR become the baseline | `lgcov cassette accept` |
| `/lgcov override --kpi tone --case run-0/respond --label fail` | Saves a label where the judge was wrong, and recalculates the judge's band | `lgcov review override` |
| `/lgcov reject` | Puts the PR's cassettes back to the base's, undoing an accept | `lgcov review reject` |

None of the three runs the PR's code. They work from what lgcov's PR report already ran, and from
the PR's files, read as data.

## The commands

A command is the first line of a new comment in a PR's conversation. An inline review comment is
a different event, and runs nothing. A comment that doesn't start with `/lgcov ` runs nothing and
gets no reply.

The first line:

- starts with `/lgcov ` at the very start of the comment;
- holds printable ASCII only, at most 300 characters;
- writes `/lgcov`, the command and its flags in lowercase, with spaces between words;
- holds exactly one command. The rest of the comment is for people, and the bot ignores it.

A name, of a node, a KPI or a label, is 1 to 100 characters from `A-Z`, `a-z`, `0-9`, `_`, `.`
and `-`, and doesn't start with `.` or `-`. A node inside a subgraph, such as
`research.summarize`, fits. A name with any other character can only be used on the command line.

```text
/lgcov accept <node>[,<node>...]
/lgcov accept all
/lgcov override --kpi <kpi> --case run-<N>/<node> --label <label>
/lgcov reject
```

- `accept` takes 1 to 20 nodes, separated by commas with no spaces, each named once, or `all` on
  its own. `all` is reserved: a node named `all` can only be accepted on the command line.
- `override` takes each of its three flags once, in any order, written `--kpi tone` or
  `--kpi=tone`. A case is named as the report names it, `run-<N>/<node>`, where `<N>` is the run's
  number, counting from 0.
- `reject` takes nothing.

### Lines the bot runs

| First line | Means |
|---|---|
| `/lgcov accept respond` | Accept `respond` |
| `/lgcov accept respond,tool` | Accept `respond` and `tool` |
| `/lgcov accept research.summarize` | Accept a node inside the `research` subgraph |
| `/lgcov accept all` | Accept every node that drifts |
| `/lgcov override --kpi answer_states_order_status --case run-0/respond --label fail` | The judge should have said `fail` on `run-0/respond` |
| `/lgcov override --label=pass --case=run-2/respond --kpi=tone` | The same form, with `=` and in another order |
| `/lgcov reject` | Undo this PR's re-baseline |

### Lines the bot refuses

| First line | Why |
|---|---|
| `/lgcov accept` | It names no node |
| `/lgcov accept respond, tool` | A space inside the list |
| `/lgcov accept --all-drifted` | A flag isn't a node's name |
| `/lgcov accept respond,respond` | `respond` is named twice |
| `/lgcov accept all,respond` | `all` stands on its own |
| `/lgcov override --kpi tone --case run-0/respond` | There is no `--label` |
| `/lgcov override --kpi tone --kpi x --case run-0/respond --label fail` | `--kpi` is given twice |
| `/lgcov override --kpi tone --case 0 --label fail` | A case is `run-<N>/<node>` |
| `/lgcov reject respond` | `reject` takes nothing |
| `/lgcov Accept respond` | Commands are lowercase |
| `/lgcov accept réspond` | It isn't ASCII |
| `/lgcov merge` | There is no such command |

A line that parses can still be refused for a name that doesn't exist: a node that never ran, a
KPI missing from the base's taxonomy, a case the KPI doesn't have, or a label the KPI doesn't use.

## What each command does

Each acts on the PR's head commit as it stands when the command runs.

**`accept`** makes the named nodes' outputs on the PR's head the baseline, in
`<cassette_dir>/baseline.json`. The outputs come from the candidate job's file for that head, the
`lgcov-candidate` artifact. The report was worked out from those same steps, so you accept what
the report showed. `all` takes every node that drifts in any run. A named node that doesn't drift
is refused, and so is a run that crashed or stopped early, since nothing after the stop ran. On
the command line, this is `lgcov cassette accept --nodes <nodes>`, or `--all-drifted`, with
`--candidate-in <file>`. Afterwards the PR changes its own cassettes, so its next report can't read
SHIP: at best it reads "No decision: needs_human" ([verdict-actions.md](verdict-actions.md),
row 12).

**`override`** records that the judge was wrong on one case. `--label` is what it should have said,
one of the KPI's labels in the base's taxonomy. What the judge did say comes from the report's
list of judged cases for that head. If the judge gave mixed labels across its samples, the bot
refuses: the case is flaky, not wrong. The override is saved to `calibration/overrides.jsonl`.
When you disagree with the judge, the case also goes to `calibration/cases/<kpi>.jsonl`, so every
later PR re-checks the judge on it. Then `calibration/bands.json` is recalculated. The reviewer
saved with the override is the commenter's GitHub login, and its note is the comment's link. A
code check is deterministic, so it can't be overridden. On the command line, this is
`lgcov review override` with `--candidate-in <file>`. The new band applies once the PR merges,
since the gate reads bands from the base.

**`reject`** makes every file under the PR's `cassette_dir` the base's again. Files the PR changed
are restored, files it added are removed, and files it removed come back. That undoes an accept
made in this PR, by the bot or by hand, so the gate grades the PR against the base's recordings
again and the drift shows. If the PR doesn't change `cassette_dir`, nothing is committed, and the
bot says there is nothing to reject. On the command line, this is
`lgcov review reject --base-dir <checkout of the base>`.

## The bot's commit has no gate result

GitHub starts no workflow for a commit made with a workflow's own token. So lgcov's PR report
doesn't run on the bot's commit, and the PR has no lgcov result for its new head until someone
pushes a commit, or closes and reopens the PR. Until then the next `accept` or `override` is
refused too, since each needs a report for the head. `reject` isn't, so it can undo an accept at
once.

If the lgcov check is required, GitHub won't merge the PR while its head has no result for it. If
the check isn't required, nothing stops a merge. To require it, add the check under "Require
status checks to pass" in a ruleset or branch protection rule on the default branch. With two
jobs, add the job that decides, which is `report` in this repo's own workflow.

The review action takes a `github-token` input. A repo that passes a GitHub App's token there gets
the report on the bot's commits, as on any push.

## Who can run them

Only people who can push to the repo, with write, maintain or admin access. The bot only does what
the commenter could already do with `git push`.

1. The job starts only for a comment whose `author_association` is `OWNER`, `MEMBER` or
   `COLLABORATOR`, so no runner starts for anyone else.
2. Those values also cover people with read access only, such as an organization member or a
   collaborator invited with read. So the job then asks GitHub for the commenter's permission on
   the repo, and refuses anything below write. If it can't ask, it refuses.

A maintainer can run the commands on their own PR, as they could push the same commit. If a second
person must sign off a re-baseline or a label, protect `cassette_dir` and `calibration/` with
CODEOWNERS ([trust-boundary.md](trust-boundary.md#what-to-set-up)).

## When the bot refuses

A refusal is a reply on the PR, and nothing is committed. The bot refuses when:

- the PR is closed or merged, comes from a fork, or has the default branch as its head;
- the PR's diff touches `.github/`, or a file named `action.yml` or `action.yaml` at any depth. A
  PR that edits a workflow or an action can make its own runs say anything, so the bot trusts no
  run of it. A file's path counts, and so does its old path when it was renamed. A PR with more
  than 3,000 changed files is refused too: GitHub lists no more, so the list would be incomplete;
- the comment was edited or deleted after it was posted;
- for `accept` and `override`: no run of the PR report for the PR's current head finished before
  the comment was posted, or that run's `lgcov-candidate` artifact has expired, which it does after
  1 day. Re-run the report, or push, then comment again;
- the PR's head moves while the command runs;
- the line doesn't parse, or names something that doesn't exist.

For a fork, or a PR that touches `.github/` or an action file, the reply gives the commands to run
locally instead.

## How it runs

```yaml
name: lgcov review

on:
  issue_comment:
    types: [created]

permissions: {}

jobs:
  review:
    if: >-
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/lgcov ') &&
      contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    concurrency:
      group: lgcov-review-${{ github.event.issue.number }}
      cancel-in-progress: false
    permissions:
      contents: write
      pull-requests: write
      actions: read
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: ./review
        with:
          taxonomy: examples/demo_agent/taxonomy.yaml
```

1. On `issue_comment`, GitHub runs the workflow file from the default branch, never from the PR,
   and the checkout above is of the default branch. The review action, `review/action.yml`,
   installs lgcov from the Action's own `uv.lock`, with no uv config and no cache, as the job that
   decides does ([trust-boundary.md](trust-boundary.md#one-job-or-two)).
2. `lgcov review fetch` holds the token and only reads. It parses the first line and makes the
   checks above. For `accept` and `override`, it downloads `candidate.json` from the report run for
   the head. It reads the PR's files at the head, and the policy at the base commit that run used,
   all through GitHub's API, as data. For `reject`, the policy comes from the PR's current base.
   Nothing of the PR is checked out, so no hook, git filter or symlink in it is followed.
3. `lgcov review apply` holds no token. It runs the command on those files and writes the changed
   files and the reply.
4. `lgcov review commit` holds the token. It checks every changed path against the table below and
   commits through GitHub's API, with the PR's head as the parent. It then moves the branch without
   force, so a push in the meantime makes it fail, and replies.

The comment's text is only ever read by lgcov's Python, from the event file GitHub hands the job.
It never reaches a shell. `tests/test_workflow_lint.py` fails CI if a workflow here that runs on
`issue_comment` puts it anywhere but an `if`.

| Command | What the commit may change |
|---|---|
| `accept` | `<cassette_dir>/baseline.json` |
| `reject` | Regular files under `<cassette_dir>/`: restored, removed or put back. A symlink or submodule there, on either side, refuses |
| `override` | `calibration/overrides.jsonl`, `calibration/cases/<kpi>.jsonl` and `calibration/bands.json` |

`cassette_dir` comes from the base's `lgcov.yaml`. Nothing is written under `.github/` or to a file
named `action.yml` or `action.yaml`, whatever `cassette_dir` says.

When the bot commits, its reply's first line says the commit has no lgcov gate result, and how to
get one. The reply names the head the command acted on and the commit it made, and for `accept`,
each node's old and new output hash.

### What the lint checks

`tests/test_workflow_lint.py` runs `lgcov.workflow_lint` over this repo's workflows. For a workflow
with an `issue_comment` trigger, each of these is a problem:

1. `on.issue_comment.types` is missing, or is anything but exactly `[created]`.
2. There is no top-level `permissions`.
3. A job's `if` doesn't contain both `github.event.issue.pull_request` and
   `github.event.comment.author_association`.
4. An expression reads `github.event.comment.`, `github.event.issue.title` or
   `github.event.issue.body` anywhere but in a job's or a step's `if`.
5. An `actions/checkout` step sets `ref` or `repository`, or doesn't set
   `persist-credentials: false`.
6. A step's `uses` is the gate Action, which installs and runs the project in its default phase:
   `./` or `./.` exactly, or `AMOHYZ/langgraph-regression-guard@` followed by any ref. `./` is
   matched whole, not as a prefix, so the review action, `./review` (or `./review/`) or
   `AMOHYZ/langgraph-regression-guard/review@<ref>`, passes.
7. A job calls a reusable workflow (`jobs.<id>.uses`), which the lint can't see into.

The rules for `pull_request` workflows stay as they are.

## Every way to abuse it, and what stops it

| # | Who | Abuse | What stops it |
|---|---|---|---|
| 1 | Commenter | Runs a command without write access | The job starts only for `OWNER`, `MEMBER` or `COLLABORATOR`. Then the permission check refuses anything below write, and refuses if it can't check |
| 2 | Commenter | Edits a comment into a command, or edits one after posting | Only new comments run. The bot reads the comment again and refuses if it was edited or deleted |
| 3 | Commenter | Hides shell in the comment: `$(...)`, backticks, quotes | The text reaches no shell: only Python reads it, from the event file. The lint fails a workflow that puts it anywhere but an `if` |
| 4 | Commenter | Passes a flag as a name: `/lgcov accept --config=x`, or a node named `--all-drifted` | The grammar has no such name. Commands run inside lgcov's process, with no command line built from the comment. Each name must exist in the base's taxonomy or the recorded runs |
| 5 | Commenter | Climbs out of a folder through a name: `--kpi ../../.github/x` | A name can't hold `/` or start with `.`, and a KPI must be in the base's taxonomy. Every path the commit changes must be in the command's row of the table above |
| 6 | Commenter | Hides a command in an HTML comment, a quote, a code block or line 2, or writes it with zero-width or look-alike letters | Only the first line counts, from its first character, in printable ASCII. One command per comment |
| 7 | Commenter | Floods the PR with commands | No job starts without the right association. Commands on one PR run one at a time. The first line is capped at 300 characters, and the job at 10 minutes |
| 8 | Commenter | Comments on an issue | The job starts only for a PR |
| 9 | Anyone | Gets the bot's own reply or report to run a command | Comments made with a workflow's token start no workflow. The author must be someone who can push. Replies never start with `/lgcov` |
| 10 | PR author | Writes a false `candidate.json`, since the PR's code writes it | `accept` takes only the file from the run for the head that finished before the comment, so it commits what the report showed. A file not exactly in lgcov's format is refused. The commit lands on the PR's own branch, and the next report can't read SHIP. **Not stopped:** those outputs are the PR's code's word, as everywhere in the gate ([limit 1](trust-boundary.md#what-is-not-covered)) |
| 11 | PR author | Edits the report workflow or an action, so its run forges the artifact or the report ([limit 5](trust-boundary.md#what-is-not-covered)) | Every command is refused while the PR's diff touches `.github/` or a file named `action.yml` or `action.yaml`. The reply gives the local commands |
| 12 | PR author | Pushes new code after the maintainer read the report | `accept` and `override` act only on the current head, only if its report finished before the comment, and only with that run's file. The commit's parent is that head and the branch moves without force, so a push during the command makes it fail. `reject` needs no such check: it only makes the gate stricter |
| 13 | PR author with write access | Re-runs an old command's workflow run | A push after the comment has its report after the comment, so `accept` and `override` refuse. After the bot's commit, the head has no report at all. `reject` finds nothing left to undo |
| 14 | PR author | Plants hostile files: huge or deeply nested JSON, a symlink, git filters or hooks | Files are read through GitHub's API at the head, with no checkout, so nothing in them is run or followed. Regular files only, with size caps, read by lgcov's strict parsers |
| 15 | PR author | Plants a hostile artifact: a path out of the folder, a zip bomb, a huge file | Its size is checked before download. Only `candidate.json` is read, into memory, up to a cap. Nothing is unpacked to disk |
| 16 | PR author | Names a node `::add-mask::...` or `::stop-commands::...`, which the runner obeys in a job's output | Text from the PR goes into files and the reply, never into the job's output |
| 17 | PR author | Puts a line break in a name, to write `$GITHUB_OUTPUT` or `$GITHUB_ENV` | No step writes text from the PR or the comment there. Steps hand data on through files |
| 18 | PR author | Puts a mention, an image or a fake approval in the reply through a node's name | Names go in code spans, with backticks taken out and `@` defused, and are cut to length. Every status word in the reply is lgcov's own |
| 19 | PR author | Changes the policy the commands read: adds a label, sets `cassette_dir: .github`, widens `ignore_paths` | The policy comes from the base commit the report used, never from the PR. The table above refuses `.github/` and action files even if the base's `cassette_dir` points there |
| 20 | PR author | Edits the review workflow or the review action | Refused, as in row 11. Neither would run anyway: on `issue_comment`, both come from the default branch |
| 21 | PR author | Makes the bot push to the default branch or a protected branch | A PR whose head is the default branch is refused. Branch protection applies to the bot's push as to anyone's |
| 22 | PR author | Opens the PR from a fork | Commands are refused, since the token can't push to a fork. The reply gives the local commands |
| 23 | PR author | Poisons a cache the job restores | The job restores and saves no uv cache, and installs lgcov from the Action's own lock |
| 24 | PR author | Fakes the report's list of judged cases, to give `override` a false judge label | The list is read only from the bot's own report comment, only from its last line, which lgcov always writes, and only if it names the current head. A label must be one of the KPI's, and mixed labels are refused |
| 25 | Anyone | Merges the bot's commit before any gate has run on it | The reply's first line and this page's say the commit has no gate result. With the lgcov check required, GitHub blocks the merge until a report exists for that head |
| 26 | Maintainer | Accepts their own PR's drift | Allowed, as a push would be. The commit names who asked, and the next report can't read SHIP. Use CODEOWNERS if a second person must sign off |
| 27 | Maintainer | Accepts more than meant with `all` | The reply lists each node with its old and new output hash. A crashed or stopped run is refused. `/lgcov reject` undoes it |
| 28 | Maintainer | Overrides to bend a judge's band | The override saves the commenter's login, from GitHub rather than the comment, and the comment's link. A band change shows in the report and applies only after the PR merges |
| 29 | Anyone | Comments on a closed or merged PR | Refused |

The job's own token: the workflow grants nothing by default (`permissions: {}`), and the job holds
`contents: write`, `pull-requests: write` and `actions: read`, and no other secret. Only `fetch`
and `commit` see the token.

## What is not covered

1. **What the PR's code said it did.** `accept` commits the outputs the candidate job reported,
   which are that code's word ([limit 1](trust-boundary.md#what-is-not-covered)). The diff of the
   bot's commit shows them.
2. **A stale page.** A maintainer who comments from a page that hadn't refreshed, after a newer
   push's report landed, acts on the newer head. The reply names the head and each node's hashes,
   and `/lgcov reject` undoes an accept.
3. **The bot's commit has no gate result** until someone pushes, or closes and reopens the PR. It
   can be merged ungated only if the lgcov check isn't required.
4. **A changed node that calls a model, with no key in the candidate job.** It fails there, so
   there are no outputs to accept from a comment. Accept it on the command line.
5. **Where the commands can't run:** a fork PR, a PR that touches `.github/` or an action file, and
   names with characters outside the grammar. `accept` and `override` also need the two-job layout,
   since only the candidate job writes the file they read, and a report run less than a day old.
   Use the command line.
