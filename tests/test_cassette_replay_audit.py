"""T152: does replay ever serve a stale output when a node's input changed?

The oracle is `_audit`: a served replay must give the same outputs, unmasked, as a fully live
run of the same PR graph. It doesn't use the key logic it checks, so any stale hit shows up as
a difference. Results: docs/validation/replay-audit.md.

Each open finding is a strict xfail (`_finding`). Run with --runxfail to see them fail.
"""

import asyncio
import contextlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from fixtures.replay_audit import (
    async_chain,
    chain,
    interrupt,
    loop,
    messages,
    send_fanout,
    side_effect,
    subgraph,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Checkpointer, RetryPolicy

from lgcov.cassette import (
    Cassette,
    Halt,
    NodeError,
    Replay,
    Step,
    record,
    replay_run,
    runs_from_json,
    runs_to_json,
)
from lgcov.cli import _divergence_lines, _load_graph, main
from lgcov.messages import plain


def _finding(why: str, raises: type[BaseException] = AssertionError) -> pytest.MarkDecorator:
    """An open finding. Strict, so a fix fails the test until this comes off. `raises`, so any
    other failure, like a broken fixture, still fails."""
    return pytest.mark.xfail(strict=True, raises=raises, reason=f"T152: {why}")


def _recorded(graph: object, inputs: dict[str, object]) -> Cassette:
    return Cassette.from_json(record(graph, inputs).to_json())  # as the CLI reads it


def _how(fresh: Replay, node: str | None = None) -> list[tuple[str, str]]:
    return [(c.node, c.how) for c in fresh.calls if node in (None, c.node)]


def _outputs(steps: Iterable[Step]) -> list[tuple[str, str]]:
    return sorted((s.node, json.dumps(plain(s.output), sort_keys=True)) for s in steps)


def _audit(
    build: Callable[[], object], cassette: Cassette, spy: Mock, changed: str | None = None
) -> Replay:
    """Replay as `lgcov ci` does and hold it to the oracle. Afterwards `spy` has only the served
    run's calls. `changed`: the node the PR edited, which must have run live, or no input
    downstream changed and a finding would be xfailing for the wrong reason."""
    live = replay_run(build(), cassette)
    spy.reset_mock()
    fresh = replay_run(build(), cassette, serve=True)
    if changed is not None and (changed, "live") not in _how(fresh):
        pytest.fail(f"broken fixture: `{changed}` didn't run live")  # not caught by `_finding`
    assert _outputs(fresh.steps) == _outputs(live.steps)
    return fresh


# Fixture 1: X -> Y -> outcome.
QUESTION: dict[str, object] = {"question": "refund?"}


def test_y_reruns_when_x_s_prompt_changes_its_input() -> None:
    y = Mock()
    cassette = _recorded(chain.build("summarise", y), QUESTION)
    fresh = _audit(lambda: chain.build("summarise briefly", y), cassette, y)
    assert _how(fresh) == [("x", "live"), ("y", "diverged"), ("outcome", "after_divergence")]
    assert y.call_count == 1
    assert [d.node for d in fresh.drifts] == ["outcome", "x", "y"]


def test_y_is_served_when_x_s_prompt_changes_but_its_output_doesn_t() -> None:
    y = Mock()
    cassette = _recorded(chain.build("reword v1", y), QUESTION)
    fresh = _audit(lambda: chain.build("reword v2", y), cassette, y)
    assert _how(fresh) == [("x", "live"), ("y", "served"), ("outcome", "served")]
    assert y.call_count == 0
    assert fresh.drifts == ()


@pytest.mark.parametrize("change", ["ticket", "due", "customer"])
def test_y_is_not_served_when_its_input_changes_only_in_an_id_a_date_time_or_a_uuid(
    change: str,
) -> None:
    y = Mock()
    cassette = _recorded(chain.build(f"{change} v1", y), QUESTION)
    _audit(lambda: chain.build(f"{change} v2", y), cassette, y, "x")


def test_the_root_is_named_one_node_late_when_x_changes_only_in_an_id() -> None:
    # T177, a known limit: the key sees the new `id` but comparing still masks it, so X shows no
    # drift and Y is the first node that does.
    y = Mock()
    cassette = _recorded(chain.build("ticket v1", y), QUESTION)
    fresh = replay_run(chain.build("ticket v2", y), cassette, serve=True)
    assert [d.node for d in fresh.drifts] == ["outcome", "y"]


@pytest.mark.parametrize("change", ["ticket", "due", "customer"])
def test_mask_volatile_serves_y_when_its_input_changes_only_in_those(change: str) -> None:
    y = Mock()
    cassette = _recorded(chain.build(f"{change} v1", y), QUESTION)
    y.reset_mock()
    fresh = replay_run(chain.build(f"{change} v2", y), cassette, serve=True, mask_volatile=True)
    assert _how(fresh) == [("x", "live"), ("y", "served"), ("outcome", "served")]
    assert y.call_count == 0
    assert fresh.drifts == ()


# Fixture 2: a node visited three times, the second visit with a changed input.
TASKS = ("t1", "t2", "t3")


def test_a_changed_visit_and_every_later_one_rerun_when_state_accumulates() -> None:
    spy = Mock()
    cassette = _recorded(loop.build(TASKS, spy), {})
    fresh = _audit(lambda: loop.build(("t1", "T2", "t3"), spy), cassette, spy)
    assert [how for _, how in _how(fresh, "worker")] == ["served", "diverged", "after_divergence"]
    assert spy.call_args_list == [call("T2"), call("t3")]


def test_only_the_changed_visit_reruns_when_the_next_input_still_matches() -> None:
    spy = Mock()
    cassette = _recorded(loop.build(TASKS, spy, narrow=True), {})
    fresh = _audit(lambda: loop.build(("t1", "T2", "t3"), spy, narrow=True), cassette, spy)
    assert [how for _, how in _how(fresh, "worker")] == ["served", "diverged", "served"]
    assert spy.call_args_list == [call("T2")]


def test_a_visit_that_matches_another_recorded_visit_is_served_that_visit_s_output() -> None:
    # The key has no visit number, so this is right for a node whose output follows its input.
    spy = Mock()
    cassette = _recorded(loop.build(TASKS, spy, narrow=True), {})
    fresh = _audit(lambda: loop.build(("t1", "t3", "t3"), spy, narrow=True), cassette, spy)
    assert [how for _, how in _how(fresh, "worker")] == ["served", "served", "after_divergence"]
    assert spy.call_args_list == [call("t3")]


# Fixture 3: Send fan-out, per-task state differs between base and PR.
def _hows(fresh: Replay, node: str) -> Counter[str]:
    return Counter(how for _, how in _how(fresh, node))


@pytest.mark.parametrize("key", ["task", "task_id"])
def test_only_the_task_whose_payload_changed_reruns(key: str) -> None:
    spy = Mock()
    cassette = _recorded(send_fanout.build(("a", "b", "c"), spy, key), {})
    fresh = _audit(lambda: send_fanout.build(("a", "B", "c"), spy, key), cassette, spy)
    assert _hows(fresh, "worker") == {"served": 2, "diverged": 1}
    spy.assert_called_once_with("B")


def test_an_added_task_runs_live_and_the_recorded_ones_are_served() -> None:
    spy = Mock()
    cassette = _recorded(send_fanout.build(("a", "b"), spy), {})
    fresh = _audit(lambda: send_fanout.build(("a", "b", "c"), spy), cassette, spy)
    assert _hows(fresh, "worker") == {"served": 2, "diverged": 1}
    spy.assert_called_once_with("c")


def test_tasks_that_differ_only_in_id_are_not_served_recorded_outputs() -> None:
    spy = Mock()
    cassette = _recorded(send_fanout.build(("a", "b", "c"), spy, "id"), {})
    _audit(lambda: send_fanout.build(("x", "y", "z"), spy, "id"), cassette, spy, "plan")


def _a_pr_changing_every_task_s_id(tmp_path: Path) -> list[str]:
    base = record(send_fanout.build(("a", "b", "c"), key="id"), {})
    (tmp_path / "baseline.json").write_text(runs_to_json([base]), encoding="utf-8")
    (tmp_path / "graph.py").write_text(
        "from fixtures.replay_audit.send_fanout import build\n"
        "graph = build(('x', 'y', 'z'), key='id')\n",
        encoding="utf-8",
    )
    return ["replay", f"{tmp_path / 'graph.py'}:graph", str(tmp_path / "baseline.json")]


def test_lgcov_replay_flags_a_pr_that_changes_every_task_s_id(tmp_path: Path) -> None:
    assert main(_a_pr_changing_every_task_s_id(tmp_path)) == 1


def test_mask_volatile_in_key_gives_lgcov_replay_the_old_matching_back(tmp_path: Path) -> None:
    args = _a_pr_changing_every_task_s_id(tmp_path)
    (tmp_path / "lgcov.yaml").write_text("mask_volatile_in_key: true\n", encoding="utf-8")
    assert main([*args, "--config", str(tmp_path / "lgcov.yaml")]) == 0


# Fixture 4: a subgraph, with a checkpointer on it, on its parent, or neither.
VALUE: dict[str, object] = {"value": 1}
SUB_SETTINGS = pytest.mark.parametrize(
    "sub_checkpointer", [None, True], ids=["default", "checkpointer_true"]
)


@SUB_SETTINGS
def test_a_subgraph_runs_live_and_the_node_after_it_reruns(sub_checkpointer: Checkpointer) -> None:
    spy = Mock()
    cassette = _recorded(subgraph.build(2, spy, sub_checkpointer), VALUE)
    assert {s.node: s.code is None for s in cassette.steps} == {
        "prep": False,
        "sub": True,
        "after": False,
    }
    fresh = _audit(lambda: subgraph.build(3, spy, sub_checkpointer), cassette, spy)
    assert _how(fresh) == [("prep", "served"), ("after", "diverged")]
    assert spy.call_count == 1


@SUB_SETTINGS
def test_an_unchanged_subgraph_still_runs_live(sub_checkpointer: Checkpointer) -> None:
    spy = Mock()
    cassette = _recorded(subgraph.build(2, spy, sub_checkpointer), VALUE)
    fresh = _audit(lambda: subgraph.build(2, spy, sub_checkpointer), cassette, spy)
    assert _how(fresh) == [("prep", "served"), ("after", "served")]
    assert spy.call_count == 1
    assert fresh.drifts == ()


# T133 adds the `unsupported` result these should assert. Today they fail on LangGraph's own error.
NO_THREAD_ID = "no `unsupported` result (T133): `_run` passes no thread_id, so LangGraph raises"


@_finding(NO_THREAD_ID, raises=ValueError)
@SUB_SETTINGS
def test_a_graph_with_a_checkpointer_replays_or_says_unsupported(
    sub_checkpointer: Checkpointer,
) -> None:
    cassette = _recorded(subgraph.build(2, sub_checkpointer=sub_checkpointer), VALUE)
    pr = subgraph.build(2, sub_checkpointer=sub_checkpointer, parent_checkpointer=InMemorySaver())
    replay_run(pr, cassette, serve=True)


@_finding(NO_THREAD_ID, raises=ValueError)
def test_a_graph_with_a_checkpointer_records_or_says_unsupported() -> None:
    record(subgraph.build(2, parent_checkpointer=InMemorySaver()), VALUE)


# Fixture 5: reducer state, `add_messages`.
ASKED: dict[str, object] = {"messages": [HumanMessage("q", id="h1")]}
TOKENS_1 = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
TOKENS_2 = {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}


def _ai(text: str, message_id: str = "m1") -> AIMessage:
    return AIMessage(text, id=message_id)


def test_y_reruns_when_a_message_s_content_changes() -> None:
    spy = Mock()
    cassette = _recorded(messages.build((_ai("a"),), spy), ASKED)
    fresh = _audit(lambda: messages.build((_ai("b"),), spy), cassette, spy)
    assert _how(fresh) == [("x", "live"), ("y", "diverged")]
    assert spy.call_count == 1


def test_y_reruns_when_a_duplicate_id_no_longer_replaces_a_message() -> None:
    # With one id, `add_messages` keeps the second message. With two, it keeps both.
    spy = Mock()
    cassette = _recorded(messages.build((_ai("one", "dup"), _ai("two", "dup")), spy), ASKED)
    fresh = _audit(
        lambda: messages.build((_ai("one", "dup"), _ai("two", "new")), spy), cassette, spy
    )
    assert _how(fresh) == [("x", "live"), ("y", "diverged")]
    assert spy.call_count == 1


def test_y_is_served_when_a_duplicate_id_leaves_it_the_same_messages() -> None:
    # The first message's text changes, but the second replaces it: Y's input is what it was.
    spy = Mock()
    cassette = _recorded(messages.build((_ai("one", "dup"), _ai("two", "dup")), spy), ASKED)
    fresh = _audit(
        lambda: messages.build((_ai("uno", "dup"), _ai("two", "dup")), spy), cassette, spy
    )
    assert _how(fresh) == [("x", "live"), ("y", "served")]
    assert spy.call_count == 0


@pytest.mark.parametrize(
    ("read", "base", "pr"),
    [
        pytest.param(
            "artifact",
            ToolMessage("c", tool_call_id="t", id="m1", artifact=1),
            ToolMessage("c", tool_call_id="t", id="m1", artifact=2),
            id="artifact",
        ),
        pytest.param(
            "additional_kwargs",
            AIMessage("a", id="m1"),
            AIMessage("a", id="m1", additional_kwargs={"refusal": "no"}),
            id="additional_kwargs",
        ),
        pytest.param(
            "response_metadata",
            AIMessage("a", id="m1", response_metadata={"finish_reason": "stop"}),
            AIMessage("a", id="m1", response_metadata={"finish_reason": "length"}),
            id="finish_reason",
        ),
        pytest.param(
            "response_metadata",
            AIMessage("a", id="m1", response_metadata={"stop_reason": "end_turn"}),
            AIMessage("a", id="m1", response_metadata={"stop_reason": "max_tokens"}),
            id="stop_reason",
        ),
    ],
)
def test_y_is_not_served_when_a_message_changes_only_in_what_a_node_can_act_on(
    read: str, base: AnyMessage, pr: AnyMessage
) -> None:
    spy = Mock()
    cassette = _recorded(messages.build((base,), spy, read), ASKED)
    _audit(lambda: messages.build((pr,), spy, read), cassette, spy, "x")


@pytest.mark.parametrize(
    ("read", "base", "pr"),
    [
        pytest.param(
            "response_metadata",
            AIMessage("a", id="m1", response_metadata={"model_name": "one"}),
            AIMessage("a", id="m1", response_metadata={"model_name": "two"}),
            id="other_response_metadata",
        ),
        pytest.param(
            "usage_metadata",
            AIMessage("a", id="m1", usage_metadata=TOKENS_1),
            AIMessage("a", id="m1", usage_metadata=TOKENS_2),
            id="usage_metadata",
        ),
        pytest.param("id", _ai("a", "m1"), _ai("a", "m2"), id="message_id"),
    ],
)
def test_y_is_served_when_a_message_changes_only_in_what_stays_ignored(
    read: str, base: AnyMessage, pr: AnyMessage
) -> None:
    # T177: ruled noise, since these change on every model call, even though Y here reads them.
    spy = Mock()
    cassette = _recorded(messages.build((base,), spy, read), ASKED)
    spy.reset_mock()
    fresh = replay_run(messages.build((pr,), spy, read), cassette, serve=True)
    assert _how(fresh) == [("x", "live"), ("y", "served")]
    assert spy.call_count == 0
    assert fresh.drifts == ()


def test_ignore_paths_take_a_message_field_out_of_the_key() -> None:
    def reply(value: int) -> AIMessage:
        return AIMessage("a", id="m1", additional_kwargs={"seed": value})

    spy = Mock()
    cassette = _recorded(messages.build((reply(1),), spy), ASKED)
    spy.reset_mock()
    paths = ["/messages/*/additional_kwargs/seed"]
    fresh = replay_run(messages.build((reply(2),), spy), cassette, paths, serve=True)
    assert _how(fresh) == [("x", "live"), ("y", "served")]
    assert spy.call_count == 0


# Fixture 6: an interrupt, which replay has no way to resume.
INTERRUPTED = "no `unsupported` result (T133): an interrupted run ends in a raw"


def test_an_interrupted_call_is_never_served_and_fires_again_on_replay() -> None:
    spy = Mock()
    cassette = _recorded(interrupt.build(spy), QUESTION)
    assert [(s.node, s.code is None) for s in cassette.steps] == [
        ("before", False),
        ("__interrupt__", True),
    ]
    spy.reset_mock()
    with contextlib.suppress(TypeError):  # the next test's finding
        replay_run(interrupt.build(spy), cassette, serve=True)
    spy.assert_called_once_with()


@_finding(f"{INTERRUPTED} TypeError from comparing the Interrupt", raises=TypeError)
def test_an_interrupted_run_replays_or_says_unsupported() -> None:
    replay_run(interrupt.build(), _recorded(interrupt.build(), QUESTION), serve=True)


@_finding(f"{INTERRUPTED} ValueError with a checkpointer, as in fixture 4", raises=ValueError)
def test_an_interrupted_run_with_a_checkpointer_replays_or_says_unsupported() -> None:
    pr = interrupt.build(checkpointer=InMemorySaver())
    replay_run(pr, _recorded(interrupt.build(), QUESTION), serve=True)


@pytest.mark.parametrize(
    ("source", "base", "inputs", "why"),
    [
        pytest.param(
            "from langgraph.checkpoint.memory import InMemorySaver\n"
            "from fixtures.replay_audit.subgraph import build\n"
            "graph = build(2, parent_checkpointer=InMemorySaver())\n",
            subgraph.build(2),
            VALUE,
            "ValueError: Checkpointer requires",
            id="checkpointer",
        ),
        pytest.param(
            "from fixtures.replay_audit.interrupt import build\ngraph = build()\n",
            interrupt.build(),
            QUESTION,
            "TypeError: Object of type Interrupt is not JSON serializable",
            id="interrupt",
        ),
    ],
)
def test_lgcov_replay_fails_loudly_on_a_graph_it_can_t_replay(
    source: str,
    base: object,
    inputs: dict[str, object],
    why: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Loud, so never a silent pass, but T133 should say `unsupported` instead.
    (tmp_path / "baseline.json").write_text(runs_to_json([record(base, inputs)]), encoding="utf-8")
    (tmp_path / "graph.py").write_text(source, encoding="utf-8")
    assert main(["replay", f"{tmp_path / 'graph.py'}:graph", str(tmp_path / "baseline.json")]) == 2
    assert f"replay failed outside node code: {why}" in capsys.readouterr().err


# Fixture 7: a tool that writes.
PAYLOAD: dict[str, object] = {"payload": "p"}
RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0, jitter=False)


def test_a_write_whose_input_is_unchanged_is_served_and_doesn_t_run_again() -> None:
    sink = Mock()
    cassette = _recorded(side_effect.build("v1", sink), PAYLOAD)
    sink.reset_mock()
    fresh = replay_run(side_effect.build("v1", sink), cassette, serve=True)
    assert _how(fresh) == [("prep", "served"), ("write", "served")]
    sink.assert_not_called()


def test_a_write_whose_input_changed_runs_once_and_is_reported_as_diverged() -> None:
    sink = Mock()
    cassette = _recorded(side_effect.build("v1", sink), PAYLOAD)
    sink.reset_mock()
    fresh = replay_run(side_effect.build("v2", sink), cassette, serve=True)
    assert _how(fresh) == [("prep", "live"), ("write", "diverged")]
    sink.assert_called_once_with("v2:p")


_Build = Callable[..., CompiledStateGraph[side_effect.State]]


def _nested(build: _Build) -> _Build:
    def nested(
        tag: str, sink: Mock, fail: Mock | None = None, retry: RetryPolicy | None = None
    ) -> CompiledStateGraph[side_effect.State]:
        return side_effect.in_a_subgraph(build(tag, sink, fail, retry))

    return nested


FLAT = [pytest.param(side_effect.build, id="sync"), pytest.param(side_effect.abuild, id="async")]
NESTED = [
    pytest.param(_nested(side_effect.build), id="sync_subgraph"),
    pytest.param(_nested(side_effect.abuild), id="async_subgraph"),
]


@pytest.mark.parametrize("build", [*FLAT, *NESTED])
@pytest.mark.parametrize(
    ("policy", "error", "attempts"),
    [
        pytest.param(None, ValueError, 1, id="no_policy"),
        pytest.param(RETRY, ValueError, 1, id="default_rule_refuses_value_error"),
        pytest.param(RETRY, ConnectionError, 3, id="default_rule_retries_connection_error"),
        pytest.param(RETRY._replace(retry_on=ValueError), ValueError, 3, id="own_rule_names_it"),
        pytest.param(RETRY._replace(retry_on=KeyError), ValueError, 1, id="own_rule_doesn_t"),
    ],
)
def test_replay_makes_the_attempts_the_agent_makes_outside_lgcov(
    build: _Build, policy: RetryPolicy | None, error: type[Exception], attempts: int
) -> None:
    sink = Mock()
    boom = Mock(side_effect=error("bad"))
    with pytest.raises(error):
        asyncio.run(build("v2", sink, boom, policy).ainvoke({"payload": "p"}))
    assert sink.call_count == attempts  # plain LangGraph, the agent as it runs outside lgcov
    sink.reset_mock()
    with pytest.raises(NodeError) as caught:
        replay_run(build("v2", sink, boom, policy), Cassette(dict(PAYLOAD), []), serve=True)
    assert sink.call_count == attempts
    assert caught.value.node.endswith("write")
    assert caught.value.original is boom.side_effect
    assert caught.value.attempts == attempts
    assert (f"({attempts} failed attempts in this run)" in str(caught.value)) == (attempts > 1)


def _retried_write(sink: Mock, build: _Build = side_effect.build) -> Replay:
    # `write`'s code is the same on both sides and its input changed. The first attempt writes
    # and hits a network error, which LangGraph is right to retry, and the second succeeds.
    flaky = Mock(side_effect=[None, ConnectionError("down"), None])
    cassette = _recorded(build("v1", sink, flaky, RETRY), PAYLOAD)
    sink.reset_mock()
    return replay_run(build("v2", sink, flaky, RETRY), cassette, serve=True)


@pytest.mark.parametrize("build", FLAT)
def test_every_attempt_at_a_retried_write_is_in_the_calls(build: _Build) -> None:
    sink = Mock()
    fresh = _retried_write(sink, build)
    assert sink.call_count == 2
    assert _how(fresh, "write") == [("write", "diverged"), ("write", "after_divergence")]
    assert fresh.failed_attempts == ("write",)


@pytest.mark.parametrize("build", NESTED)
def test_a_failed_attempt_inside_a_subgraph_is_in_the_calls(build: _Build) -> None:
    sink = Mock()
    fresh = _retried_write(sink, build)
    assert sink.call_count == 2
    assert fresh.failed_attempts == ("sub.write",)


def test_a_retried_write_whose_input_changed_is_reported_as_diverged() -> None:
    fresh = _retried_write(Mock())
    assert any(
        "Diverged" in line and "`write`" in line for line in _divergence_lines([fresh], "flag")
    )


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(RETRY, id="default_rule"),
        pytest.param(RETRY._replace(retry_on=Exception), id="a_rule_that_retries_anything"),
    ],
)
def test_on_divergence_fail_stops_a_write_with_a_retry_policy(policy: RetryPolicy) -> None:
    # A retry mustn't get a node past the halt, or the write `fail` is there to stop still runs.
    sink = Mock()
    cassette = _recorded(side_effect.build("v1", sink, retry=policy), PAYLOAD)
    sink.reset_mock()
    pr = side_effect.build("v2", sink, retry=policy)
    fresh = replay_run(pr, cassette, serve=True, on_divergence="fail")
    assert fresh.halt == Halt("write", "diverged")
    assert fresh.not_checked == ("write",)
    sink.assert_not_called()


def test_an_error_handler_gets_the_node_s_own_error_and_the_attempt_is_in_the_calls() -> None:
    sink, seen = Mock(), Mock()
    boom = Mock(side_effect=ValueError("bad"))
    side_effect.build("v2", sink, boom, on_error=seen).invoke({"payload": "p"})
    seen.assert_called_once_with("ValueError")  # plain LangGraph
    sink.reset_mock()
    seen.reset_mock()
    pr = side_effect.build("v2", sink, boom, on_error=seen)
    fresh = replay_run(pr, Cassette(dict(PAYLOAD), []), serve=True)
    seen.assert_called_once_with("ValueError")
    assert sink.call_count == 1
    assert fresh.failed_attempts == ("write",)


def test_on_divergence_fail_stops_a_write_and_its_error_handler() -> None:
    sink, seen = Mock(), Mock()
    cassette = _recorded(side_effect.build("v1", sink, on_error=seen), PAYLOAD)
    sink.reset_mock()
    pr = side_effect.build("v2", sink, on_error=seen)
    fresh = replay_run(pr, cassette, serve=True, on_divergence="fail")
    assert fresh.halt == Halt("write", "diverged")
    sink.assert_not_called()
    seen.assert_not_called()


def test_a_halt_doesn_t_wait_out_the_default_retry_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    sink, sleep = Mock(), Mock()
    monkeypatch.setattr(time, "sleep", sleep)
    cassette = _recorded(side_effect.build("v1", sink, retry=RETRY), PAYLOAD)
    pr = side_effect.build("v2", sink, retry=RETRY)
    assert replay_run(pr, cassette, serve=True, on_divergence="fail").halt is not None
    sleep.assert_not_called()


@_finding("Y is served: what it reads from the Store changed, and its state didn't")
def test_y_is_not_served_when_what_it_reads_from_a_store_changed() -> None:
    spy, backend = Mock(), InMemoryStore()
    cassette = _recorded(side_effect.build_with_store("old", backend, spy), {})
    _audit(lambda: side_effect.build_with_store("new", backend, spy), cassette, spy, "x")


# Fixture 8: async nodes, the regression check for T164 (PR #54).
def test_async_y_reruns_when_x_s_prompt_changes_its_input() -> None:
    y = Mock()
    cassette = _recorded(async_chain.build("v1", y), QUESTION)
    fresh = _audit(lambda: async_chain.build("v2", y), cassette, y)
    assert _how(fresh) == [("x", "live"), ("y", "diverged"), ("outcome", "after_divergence")]
    assert y.call_count == 1
    assert [d.node for d in fresh.drifts] == ["outcome", "x", "y"]


def test_an_unchanged_async_graph_is_served_without_running() -> None:
    y = Mock()
    cassette = _recorded(async_chain.build("v1", y), QUESTION)
    fresh = _audit(lambda: async_chain.build("v1", y), cassette, y)
    assert _how(fresh) == [("x", "served"), ("y", "served"), ("outcome", "served")]
    assert y.call_count == 0
    assert fresh.drifts == ()


def test_a_cassette_recorded_before_the_key_changed_is_still_served() -> None:
    # T177: a cassette holds no key. Each replay computes it from the stored input, so changing
    # what it reads takes no new format and no re-record.
    demo = Path(__file__).resolve().parents[1] / "examples" / "demo_agent"
    saved = (demo / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    held = {k for run in json.loads(saved)["runs"] for step in run["steps"] for k in step}
    assert held <= {"node", "input", "output", "code", "goto"}
    graph = _load_graph(f"{demo / 'graph.py'}:graph")
    done = [replay_run(graph, run, serve=True) for run in runs_from_json(saved)]
    assert [c.how for r in done for c in r.calls] == ["served"] * 5
    assert all(r.drifts == () for r in done)
