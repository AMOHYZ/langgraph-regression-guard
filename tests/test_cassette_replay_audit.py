"""T152: does replay ever serve a stale output when a node's input changed?

The oracle is `_audit`: a served replay must give the same outputs, unmasked, as a fully live
run of the same PR graph. It doesn't use the key logic it checks, so any stale hit shows up as
a difference. Results: docs/validation/replay-audit.md.

Each open finding is a strict xfail (`_finding`). Run with --runxfail to see them fail.
"""

import contextlib
import json
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
from langgraph.store.memory import InMemoryStore
from langgraph.types import Checkpointer, RetryPolicy

from lgcov.cassette import Cassette, NodeError, Replay, Step, record, replay_run, runs_to_json
from lgcov.cli import _divergence_lines, main
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


@_finding("Y is served: its input changed only in a masked value, which the key can't see")
@pytest.mark.parametrize("change", ["ticket", "due", "customer"])
def test_y_is_not_served_when_its_input_changes_only_in_a_masked_value(change: str) -> None:
    y = Mock()
    cassette = _recorded(chain.build(f"{change} v1", y), QUESTION)
    _audit(lambda: chain.build(f"{change} v2", y), cassette, y, "x")


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


@_finding("tasks that differ only in `id` share one key, so each is served a recorded output")
def test_tasks_that_differ_only_in_id_are_not_served_recorded_outputs() -> None:
    spy = Mock()
    cassette = _recorded(send_fanout.build(("a", "b", "c"), spy, "id"), {})
    _audit(lambda: send_fanout.build(("x", "y", "z"), spy, "id"), cassette, spy, "plan")


@_finding("`lgcov replay` passes a PR that changes every task's id: no drift, exit 0")
def test_lgcov_replay_flags_a_pr_that_changes_every_task_s_id(tmp_path: Path) -> None:
    base = record(send_fanout.build(("a", "b", "c"), key="id"), {})
    (tmp_path / "baseline.json").write_text(runs_to_json([base]), encoding="utf-8")
    (tmp_path / "graph.py").write_text(
        "from fixtures.replay_audit.send_fanout import build\n"
        "graph = build(('x', 'y', 'z'), key='id')\n",
        encoding="utf-8",
    )
    assert main(["replay", f"{tmp_path / 'graph.py'}:graph", str(tmp_path / "baseline.json")]) == 1


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


@_finding("Y is served: this part of a message isn't in the key, and Y reads it")
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
            id="response_metadata",
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
def test_y_is_not_served_when_a_message_changes_only_where_the_key_can_t_see(
    read: str, base: AnyMessage, pr: AnyMessage
) -> None:
    spy = Mock()
    cassette = _recorded(messages.build((base,), spy, read), ASKED)
    _audit(lambda: messages.build((pr,), spy, read), cassette, spy, "x")


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


def test_plain_langgraph_doesn_t_retry_a_node_s_value_error() -> None:
    # What the next test compares with: the agent's own behaviour outside lgcov is one write.
    sink = Mock()
    boom = Mock(side_effect=ValueError("bad"))
    with pytest.raises(ValueError, match="bad"):
        side_effect.build("v2", sink, boom, RETRY).invoke({"payload": "p"})
    assert sink.call_count == 1


@_finding("`_named` turns the ValueError into a NodeError, which LangGraph's retry rule retries")
def test_replay_doesn_t_repeat_a_write_that_langgraph_wouldn_t_retry() -> None:
    sink = Mock()
    cassette = _recorded(side_effect.build("v1", sink), PAYLOAD)
    sink.reset_mock()
    boom = Mock(side_effect=ValueError("bad"))
    with pytest.raises(NodeError):
        replay_run(side_effect.build("v2", sink, boom, RETRY), cassette, serve=True)
    assert sink.call_count == 1


def _retried_write(sink: Mock) -> Replay:
    # `write`'s code is the same on both sides and its input changed. The first attempt writes
    # and hits a network error, which LangGraph is right to retry, and the second succeeds.
    flaky = Mock(side_effect=[None, ConnectionError("down"), None])
    cassette = _recorded(side_effect.build("v1", sink, flaky, RETRY), PAYLOAD)
    sink.reset_mock()
    return replay_run(side_effect.build("v2", sink, flaky, RETRY), cassette, serve=True)


@_finding("a failed attempt logs no Call, so its write is in no count")
def test_every_attempt_at_a_retried_write_is_in_the_calls() -> None:
    sink = Mock()
    fresh = _retried_write(sink)
    assert len(_how(fresh, "write")) == sink.call_count


@_finding("the failed attempt takes the `diverged` label with it, so the report has no line")
def test_a_retried_write_whose_input_changed_is_reported_as_diverged() -> None:
    fresh = _retried_write(Mock())
    assert any("`write`" in line for line in _divergence_lines([fresh], "flag"))


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
