"""T186: the review commands a maintainer posts on a PR. parse_command against the two tables of
example lines in docs/review.md, hostile first lines, and the issue_comment event it reads."""

import copy
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from lgcov.review_command import (
    ASSOCIATIONS,
    MAX_LINE,
    MAX_NODES,
    Accept,
    Command,
    CommandError,
    CommentEvent,
    FetchContext,
    Override,
    Pull,
    Refusal,
    Reject,
    Report,
    command_from_json,
    command_to_json,
    comment_event,
    local_commands,
    may_command,
    parse_command,
    refusal,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "review.md"

# What each line of the doc's "Lines the bot runs" table parses to, in the table's order.
RUNS: dict[str, Command] = {
    "/lgcov accept respond": Accept(("respond",)),
    "/lgcov accept respond,tool": Accept(("respond", "tool")),
    "/lgcov accept research.summarize": Accept(("research.summarize",)),
    "/lgcov accept all": Accept(None),
    "/lgcov override --kpi answer_states_order_status --case run-0/respond --label fail": Override(
        "answer_states_order_status", "run-0/respond", "fail"
    ),
    "/lgcov override --label=pass --case=run-2/respond --kpi=tone": Override(
        "tone", "run-2/respond", "pass"
    ),
    "/lgcov reject": Reject(),
}


def _doc_table(heading: str) -> list[tuple[str, str]]:
    """Each row of the table under a `###` heading in docs/review.md: its line, its second cell."""
    section = DOC.read_text(encoding="utf-8").split(f"### {heading}\n", 1)[1].split("\n#", 1)[0]
    rows = (re.fullmatch(r"\| `(/lgcov[^`]*)` \| (.+) \|", line) for line in section.splitlines())
    return [(row[1], row[2]) for row in rows if row]


def test_the_doc_s_tables_are_the_ones_checked_here() -> None:
    assert [line for line, _ in _doc_table("Lines the bot runs")] == list(RUNS)
    assert len(_doc_table("Lines the bot refuses")) == 12


@pytest.mark.parametrize("line", list(RUNS))
def test_each_line_the_doc_runs_parses_as_it_says(line: str) -> None:
    assert parse_command(line) == RUNS[line]


@pytest.mark.parametrize(("line", "why"), _doc_table("Lines the bot refuses"))
def test_each_line_the_doc_refuses_is_refused_for_its_reason(line: str, why: str) -> None:
    with pytest.raises(CommandError) as refused:
        parse_command(line)
    assert str(refused.value) == why


def test_the_doc_s_workflow_admits_the_associations_checked_here() -> None:
    found = re.search(r"fromJSON\('(\[[^]]*\])'\)", DOC.read_text(encoding="utf-8"))
    assert found
    assert re.findall(r'"([A-Z_]+)"', found[1]) == list(ASSOCIATIONS)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "LGTM",
        "/lgcov",  # no space after it, so the workflow doesn't start either
        "/lgcov\r\naccept all",
        "/lgcovaccept all",
        "/LGCOV accept all",
        " /lgcov accept all",  # not at the very start of the comment
        "> /lgcov accept all",  # a quote of someone else's comment
        "<!-- /lgcov accept all -->",
        "```\n/lgcov accept all\n```",
        "Please run this:\n/lgcov accept all",  # a command on line 2
    ],
)
def test_a_comment_that_doesn_t_start_with_the_prefix_is_no_command(body: str) -> None:
    assert parse_command(body) is None


def test_only_the_first_line_counts() -> None:
    assert parse_command("/lgcov reject\n/lgcov accept all") == Reject()
    assert parse_command("/lgcov accept respond\r\nThe tone change is wanted.") == Accept(
        ("respond",)
    )


def test_spaces_between_words_can_repeat_and_trail() -> None:
    assert parse_command("/lgcov  accept   respond  ") == Accept(("respond",))


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("/lgcov accept $(id)", "A node's name is"),
        ("/lgcov accept `id`", "A node's name is"),
        ("/lgcov accept respond;rm", "A node's name is"),
        ("/lgcov accept ../../.github/workflows/x", "A node's name is"),
        ("/lgcov accept .hidden", "A node's name is"),
        ("/lgcov accept respond,,tool", "A name in the list is empty"),
        ("/lgcov accept respond,", "A name in the list is empty"),
        ("/lgcov accept respond tool", "A space inside the list"),
        ("/lgcov accept a\tb", "It holds a tab or another control character"),
        ("/lgcov accept a\rb", "It holds a tab or another control character"),
        ("/lgcov accept a\x00", "It holds a tab or another control character"),
        ("/lgcov accept a​b", "It isn't ASCII"),  # a zero-width space
        ("/lgcov accept аll", "It isn't ASCII"),  # a Cyrillic а
        ("/lgcov accept a /lgcov accept all", "It isn't ASCII"),  # not a line break here
        ("/lgcov ", "It names no command"),
        ("/lgcov   ", "It names no command"),
        ("/lgcov REJECT", "Commands are lowercase"),
        ("/lgcov reject --all", "`reject` takes nothing"),
        ("/lgcov override", "There is no `--kpi`"),
        ("/lgcov override --kpi tone --label fail", "There is no `--case`"),
        ("/lgcov override tone run-0/respond fail", "`override` takes `--kpi`, `--case`"),
        ("/lgcov override --node respond --kpi tone", "`override` takes `--kpi`, `--case`"),
        ("/lgcov override -k tone", "`override` takes `--kpi`, `--case`"),
        ("/lgcov override --kpi", "`--kpi` has no value"),
        ("/lgcov override --kpi= --case run-0/respond --label fail", "`--kpi` has no value"),
        ("/lgcov override --kpi --case run-0/respond --label fail", "`--kpi` has no value"),
        ("/lgcov override --label=a --label b --kpi t --case run-0/r", "`--label` is given twice"),
        ("/lgcov override --kpi ../x --case run-0/respond --label fail", "`--kpi` takes a name"),
        ("/lgcov override --kpi tone --case run-0/respond --label -1", "`--label` takes a name"),
        ("/lgcov override --kpi=a=b --case run-0/respond --label fail", "`--kpi` takes a name"),
        (
            "/lgcov override --kpi t --case run-00/respond --label fail",
            "A case is `run-<N>/<node>`",
        ),
        ("/lgcov override --kpi t --case run-0/../x --label fail", "A case is `run-<N>/<node>`"),
        ("/lgcov override --kpi t --case run-0 --label fail", "A case is `run-<N>/<node>`"),
    ],
)
def test_hostile_and_malformed_lines_are_refused(line: str, why: str) -> None:
    with pytest.raises(CommandError) as refused:
        parse_command(line)
    assert str(refused.value).startswith(why)


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("/lgcov accept a`b", "a`b"),
        ("/lgcov accept @org/team", "@org"),
        ("/lgcov accept ![x](http://e.example/p.png)", "http"),
        ("/lgcov override --kpi t --case run-0/r --label `x`", "`x`"),
        ("/lgcov override --kpi=@team --case run-0/r --label fail", "@team"),
        ("/lgcov override --kpi t --case @team/x --label fail", "@team"),
        ("/lgcov override --@team t", "@team"),
    ],
)
def test_a_refusal_never_repeats_what_isn_t_a_valid_name(line: str, fragment: str) -> None:
    """The reply quotes the reason on the PR, so a mention or a link in it would land there."""
    with pytest.raises(CommandError) as refused:
        parse_command(line)
    assert fragment not in str(refused.value)


def test_names_take_up_to_100_characters() -> None:
    assert parse_command(f"/lgcov accept {'n' * 100}") == Accept(("n" * 100,))
    with pytest.raises(CommandError, match="A node's name is"):
        parse_command(f"/lgcov accept {'n' * 101}")


def test_accept_takes_up_to_20_nodes() -> None:
    nodes = [f"n{i}" for i in range(MAX_NODES)]
    assert parse_command("/lgcov accept " + ",".join(nodes)) == Accept(tuple(nodes))
    with pytest.raises(CommandError, match=f"At most {MAX_NODES} nodes"):
        parse_command("/lgcov accept " + ",".join([*nodes, "one_more"]))


def test_the_first_line_takes_up_to_300_characters() -> None:
    padded = "/lgcov reject" + " " * (MAX_LINE - len("/lgcov reject"))
    assert parse_command(padded) == Reject()
    with pytest.raises(CommandError, match=f"longer than {MAX_LINE} characters"):
        parse_command(padded + " ")


def test_an_override_s_flags_come_in_any_order_and_either_form() -> None:
    expected = Override("tone", "run-12/research.summarize", "fail")
    for line in (
        "/lgcov override --kpi tone --case run-12/research.summarize --label fail",
        "/lgcov override --case=run-12/research.summarize --label fail --kpi=tone",
        "/lgcov override --label=fail --kpi tone --case=run-12/research.summarize",
    ):
        assert parse_command(line) == expected


def _event(
    *,
    action: str = "created",
    association: str = "OWNER",
    user_type: str = "User",
    on_pr: bool = True,
) -> dict[str, object]:
    issue: dict[str, object] = {"number": 12}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/octo-org/agent/pulls/12"}
    return {
        "action": action,
        "repository": {"full_name": "octo-org/agent"},
        "issue": issue,
        "comment": {
            "id": 123,
            "html_url": "https://github.com/octo-org/agent/pull/12#issuecomment-123",
            "body": "/lgcov accept respond",
            "created_at": "2026-11-17T10:25:30Z",
            "author_association": association,
            "user": {"login": "maintainer", "type": user_type},
        },
    }


def test_a_comment_event_reads_every_field_the_bot_needs() -> None:
    assert comment_event(_event()) == CommentEvent(
        action="created",
        repo="octo-org/agent",
        pr=12,
        comment_id=123,
        url="https://github.com/octo-org/agent/pull/12#issuecomment-123",
        body="/lgcov accept respond",
        created_at="2026-11-17T10:25:30Z",
        author="maintainer",
        author_type="User",
        association="OWNER",
    )


GONE = object()  # for _changed: take the field out


def _changed(path: tuple[str, ...], value: object) -> dict[str, object]:
    """The event with the field at path set to value, or taken out for GONE."""
    event = copy.deepcopy(_event())
    parent: object = event
    for key in path[:-1]:
        assert isinstance(parent, dict)
        parent = parent[key]
    assert isinstance(parent, dict)
    if value is GONE:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    return event


@pytest.mark.parametrize("value", [GONE, None, "", "https://api.github.com/repos/o/r/pulls/12"])
def test_a_comment_whose_issue_has_no_pr_object_is_on_an_issue(value: object) -> None:
    """As the job's `if` reads `github.event.issue.pull_request`: only an object is a PR."""
    assert comment_event(_changed(("issue", "pull_request"), value)).pr is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("action",), GONE),
        (("repository",), GONE),
        (("issue",), GONE),
        (("issue", "number"), "12"),
        (("comment", "id"), GONE),
        (("comment", "id"), "123"),
        (("comment", "id"), True),
        (("comment", "body"), 7),
        (("comment", "body"), None),
        (("comment", "user"), "maintainer"),
        (("comment", "user", "login"), GONE),
        (("comment", "author_association"), GONE),
        (("comment",), []),
    ],
)
def test_an_event_missing_a_field_or_with_a_wrong_type_is_refused(
    path: tuple[str, ...], value: object
) -> None:
    with pytest.raises(ValueError, match="event"):
        comment_event(_changed(path, value))


@pytest.mark.parametrize("raw", [None, [], "event", {}])
def test_what_isn_t_an_event_is_refused(raw: object) -> None:
    with pytest.raises(ValueError, match="event"):
        comment_event(raw)


@pytest.mark.parametrize("association", ASSOCIATIONS)
def test_an_owner_member_or_collaborator_may_start_a_command(association: str) -> None:
    assert may_command(comment_event(_event(association=association)))


@pytest.mark.parametrize(
    "association", ["CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE"]
)
def test_anyone_else_may_not(association: str) -> None:
    assert not may_command(comment_event(_event(association=association)))


@pytest.mark.parametrize(
    "event",
    [
        _event(user_type="Bot"),
        _event(on_pr=False),
        _event(action="edited"),
        _event(action="deleted"),
    ],
    ids=["a bot", "an issue", "an edit", "a deletion"],
)
def test_a_bot_an_issue_an_edit_or_a_deletion_starts_nothing(event: dict[str, object]) -> None:
    assert not may_command(comment_event(event))


# T186, PR 6: what fetch checks before it reads anything else (docs/review.md, "When the bot
# refuses"). A context is the API's answers, already read; refusal is pure.
H = "a" * 40
PULL = Pull(
    number=12,
    state="open",
    merged=False,
    head_repo="octo-org/agent",
    head_ref="feat/x",
    head_sha=H,
    base_sha="b" * 40,
    default_branch="main",
)
REPORT = Report(
    artifact_id=7,
    expired=False,
    artifact_updated="2026-11-17T10:20:00Z",
    run_status="completed",
    run_event="pull_request",
    run_head_sha=H,
    run_updated="2026-11-17T10:21:00Z",
    base_sha="b" * 40,
)


ACCEPT = Accept(("respond",))


def _context(
    command: Command = ACCEPT, changes: Mapping[str, object] | None = None
) -> FetchContext:
    context = FetchContext(
        event=comment_event(_event()),  # posted 10:25:30
        command=command,
        repo="octo-org/agent",
        permission="write",
        body_now="/lgcov accept respond",
        pull=PULL,
        changed=("graph.py", "cassettes/baseline.json"),
        changed_complete=True,
        report=REPORT,
    )
    # Any: replace() can't check a mapping's values against the fields; each test names real ones
    return replace(context, **(changes or {}))  # type: ignore[arg-type]


@pytest.mark.parametrize("command", [Accept(("respond",)), Accept(None), Reject()])
def test_a_maintainer_s_fresh_command_on_a_reported_head_goes_ahead(command: Command) -> None:
    assert refusal(_context(command)) is None


def test_reject_needs_no_report() -> None:
    assert refusal(_context(Reject(), {"report": None})) is None


@pytest.mark.parametrize(
    ("changes", "why", "local"),
    [
        ({"permission": "read"}, "can't push to this repo", False),
        ({"permission": "triage"}, "can't push to this repo", False),
        ({"body_now": None}, "was deleted", False),
        ({"body_now": "/lgcov accept all"}, "was edited after it was posted", False),
        ({"pull": replace(PULL, state="closed")}, "is closed", False),
        ({"pull": replace(PULL, merged=True)}, "is closed", False),
        ({"pull": replace(PULL, head_repo="someone/fork")}, "comes from a fork", True),
        ({"pull": replace(PULL, head_repo="")}, "comes from a fork", True),
        ({"pull": replace(PULL, head_ref="main")}, "head is the default branch", False),
        ({"changed": (".github/workflows/x.yml",)}, "touches .github/ or an action file", True),
        ({"changed": ("deep/dir/action.yaml",)}, "touches .github/ or an action file", True),
        ({"changed_complete": False}, "more files than GitHub lists", True),
        ({"report": None}, "no lgcov report for `aaaaaaa`", False),
        ({"report": replace(REPORT, expired=True)}, "has expired", False),
        ({"report": replace(REPORT, run_status="in_progress")}, "no finished report", False),
        ({"report": replace(REPORT, run_event="push")}, "no finished report", False),
        ({"report": replace(REPORT, run_head_sha="c" * 40)}, "no lgcov report for", False),
        (
            {"report": replace(REPORT, run_updated="2026-11-17T10:26:00Z")},
            "changed after your comment",
            False,
        ),
        (
            {"report": replace(REPORT, artifact_updated="2026-11-17T10:25:31Z")},
            "changed after your comment",
            False,
        ),
    ],
)
def test_each_reason_to_refuse(changes: dict[str, object], why: str, local: bool) -> None:
    refused = refusal(_context(changes=changes))
    assert refused is not None and why in refused.reason, refused
    assert refused.local == local


def test_a_refusal_never_names_a_path_the_pr_chose() -> None:
    refused = refusal(_context(changes={"changed": (".github/@team![x](http://e.example).yml",)}))
    assert refused is not None and "@team" not in refused.reason and "http" not in refused.reason


@pytest.mark.parametrize(
    ("command", "lines"),
    [
        (Accept(("respond", "tool")), ["lgcov cassette accept --nodes respond,tool"]),
        (Accept(None), ["lgcov cassette accept --all-drifted"]),
        (Reject(), ["lgcov review reject --base-dir <checkout of the base>"]),
    ],
)
def test_the_local_commands_for_a_refused_command(command: Command, lines: list[str]) -> None:
    text = local_commands(command)
    assert all(f"`{line}`" in text for line in lines)


@pytest.mark.parametrize("command", [*RUNS.values()])
def test_a_command_survives_the_round_trip(command: Command) -> None:
    assert command_from_json(command_to_json(command)) == command


def test_refusal_is_a_reason_and_whether_to_give_the_local_commands() -> None:
    assert Refusal("x") == Refusal("x", local=False)
