# Replay audit: changed inputs (T152)

Does cassette replay ever serve a stale output when a node's input changed, and does every
pattern it can't replay safely say `unsupported` with a reason? Audited on 6 Nov 2026 at
`dc2dfa0`, with LangGraph 1.2.16, on Python 3.12.4 and 3.15.0.

**Today the answer is still no to both.** Replay is right when a change reaches a part of the
input the key can see, which covers plain chains, loops, Send fan-out, plain subgraphs, reducers
and async nodes. It serves stale outputs, with no drift, when a change reaches only a Store, or a
value that stays masked by ruling: a message's id, usage and other metadata, or a key like
`run_id`. lgcov has no `unsupported` result, so what it can't replay ends in a raw error.

The retried writes and the stale hits in rows 1a, 3a and 5a are fixed: see Side-effect safety and
How a step is matched. Each finding still open is a strict xfail in
`tests/test_cassette_replay_audit.py`, so a fix fails its test until the marker comes off. To see
them fail:

```bash
uv run pytest tests/test_cassette_replay_audit.py --runxfail
```

## Side-effect safety

Fixed, the audit's first fix. A node that writes ran more times under lgcov than it does outside
it, and the reports didn't say so. These cases need a `RetryPolicy` on the node, here with
`max_attempts=3`, or an error handler.

| Case | Before the fix | Now | Tests |
|---|---|---|---|
| 7b. The node writes, then raises `ValueError` | Plain LangGraph **1** write, lgcov **3** | **1** write, as outside lgcov | `test_replay_makes_the_attempts_the_agent_makes_outside_lgcov` |
| 7c. The first attempt writes and hits `ConnectionError`, the second succeeds | **2** writes, **1** logged call, no report line | **2** writes, **2** logged calls, and the report names the node | `test_every_attempt_at_a_retried_write_is_in_the_calls`, `test_a_failed_attempt_inside_a_subgraph_is_in_the_calls`, `test_a_retried_write_whose_input_changed_is_reported_as_diverged`, `test_cli_ci_names_a_node_that_raised_and_ran_again`, `test_cli_replay_names_a_node_that_raised_and_ran_again` |
| The node's own rule names the error, `retry_on=ValueError` | Plain LangGraph **3** writes, lgcov **1** and a crash | **3** writes, as outside lgcov | `test_replay_makes_the_attempts_the_agent_makes_outside_lgcov` |
| Every attempt fails | The crash named no attempts | It says `(3 failed attempts in this run)` | `test_cli_ci_says_how_many_attempts_a_failed_node_made` |
| `on-divergence: fail` on a node with a retry policy | Ignored: the retry caught the halt, and the write ran | The run halts, with no write and no wait | `test_on_divergence_fail_stops_a_write_with_a_retry_policy`, `test_a_halt_doesn_t_wait_out_the_default_retry_rule`, `test_on_divergence_fail_stops_a_write_and_its_error_handler` |
| The node has an error handler | The handler got lgcov's wrapper | It gets the node's own error, and the failed attempt is logged | `test_an_error_handler_gets_the_node_s_own_error_and_the_attempt_is_in_the_calls` |

What changed:

- A node's error now goes through LangGraph as it is, and `_run` makes it a `NodeError` once it
  leaves the run. A retry rule or an error handler sees what it sees outside lgcov.
- Every attempt that raised is a `Call`. It counts as a live call, the run keeps its `diverged`
  line, and the report adds `Raised, and the run went on`.
- A halt stays in force once it's set, so a retry can't get a call past it.

A retry is still a real write: if the agent's own policy retries a write, it runs again under
lgcov as it does outside it, and now the report says so. With no retry policy, an unchanged input
is served with no write, and a changed input writes exactly once and is reported as diverged.
That one write is real too. Replay can't tell that a node writes, so it runs it against whatever
the node is connected to.

## How a step is matched

`_run` in `src/lgcov/cassette.py` serves a recorded step when all three of these match:

| Field | What it is |
|---|---|
| `node` | The LangGraph node name |
| `code` | `fingerprint(fn)`: a hash of the function's source, defaults, closure values and the values of the globals it uses, followed through project code |
| masked input | `json.dumps(key_view(state, ignore_paths, mask_volatile), sort_keys=True)`. An input that isn't JSON gives no key, so that call always runs live |

- The mask hides the values of `message_id`, `request_id`, `run_id`, `span_id`, `tool_call_id`
  and `trace_id` at any depth, and anything under `ignore_paths`. A plain `id`, and a UUID or
  date-time inside a string, count.
- The `id` of a LangChain `ToolCall`, a dict with `type: tool_call` as in a `Send` payload, is
  masked. It's a `tool_call_id`, and a served output is fitted to this run's.
- In a message, type, content, name, tool call names and args, status, `artifact`,
  `additional_kwargs`, `response_metadata.finish_reason` and `response_metadata.stop_reason`
  count. Its `id`, `usage_metadata` and the rest of `response_metadata` don't.
- Inside `additional_kwargs`, an `id` is masked: it's a provider's, like the message's own.
- A value JSON can't hold counts as its `repr`, with any memory address cut, because that's how
  a recording holds it.
- Each recorded step is served once, and the first unused match wins. Visit number and position
  aren't in the key, and nor are `config`, `runtime` or `store`.
- Subgraphs, `ToolNode` and nodes with no fingerprint are never served. Routers always run.

**To get the old matching back,** set `mask_volatile_in_key: true` in `lgcov.yaml`. The key then
also hides `id` values, and UUIDs and date-times inside strings. Message fields still count. To
drop one field instead, use `ignore_paths`, like `/messages/*/additional_kwargs/usage`, but that
blanks the whole value and hides it from drift too. On a PR both are read from the base.

**Cassettes store no key.** It's computed from the stored input on each replay, so an existing
cassette needs no re-record, and the new matching applies to it from the next replay.

**Comparing is separate.** `normalize` decides drift, and it still masks `id` values, UUIDs and
date-times, so a fresh one in a live output isn't drift (T085).

**Known limit, the root is named one node late.** If X's output changes only in an `id`, X shows
no drift. Y re-runs, and if that changes Y's output, Y is the first node that drifts, so it's named
the root: `test_the_root_is_named_one_node_late_when_x_changes_only_in_an_id`.

### What seeing these values costs

A node that runs live and writes a new id or time into plain state makes everything that reads it
re-run. If it's a subgraph, that's on every PR, since a subgraph is never served. A provider that
reports usage in `additional_kwargs` does the same whenever a model call above it re-runs.

Steps served, offline, on `bec6052` and with this fix:

| Run | Before | After |
|---|---|---|
| Demo agent, unchanged | 5/5 | 5/5 |
| Demo agent, prompt tweak | 3/5 | 3/5 |
| open_deep_research #163, 6 kept recordings, real graph, local stub for the model | 12/24 | 12/24 |
| Worst case: a subgraph stamps a new UUID and date-time, then 3 unchanged nodes | 3/4 | 0/4, or 3/4 with `mask_volatile_in_key` |

The demo and #163 don't move because they barely hold these values. The demo has none, and #163
has one UUID, on a step after its always-live subgraph, so it shows no harm there, not no cost
anywhere. #163's recordings predate the fingerprint fix in PR 65, so its fingerprints were
refreshed in memory to compare the key alone.

## Method

`_audit` replays the PR graph as `lgcov ci` does, with `replay_run(..., serve=True)` under the
default `on_divergence="flag"`, and compares it with a fully live run of the same graph. It
compares raw outputs, unmasked, so it doesn't rely on the key logic it checks. Tests also assert
how each call ran and how many times a spy ran. Cassettes go through JSON, as they do in the CLI.
Everything is offline with fakes.

## Matrix

Status is what the tests show. The last column applies only to rows with a finding: 1a, 3a, 5a and
7d are ruled, and the rest are proposals.

| # | Pattern | Status | Evidence | Ruling or proposal |
|---|---|---|---|---|
| 1 | X → Y → outcome, X's prompt changes Y's input | supported | `y` diverged and ran once, `outcome` re-ran: `test_y_reruns_when_x_s_prompt_changes_its_input` | |
| 1 | X's prompt changes, its output doesn't | supported | `y` served, ran 0 times: `test_y_is_served_when_x_s_prompt_changes_but_its_output_doesn_t` | |
| 1a | Y's input changes only in an `id` value, a date-time or a UUID in text | fixed | Re-runs, and gives what a live run gives: `test_y_is_not_served_when_its_input_changes_only_in_an_id_a_date_time_or_a_uuid`. With `mask_volatile_in_key`, served: `test_mask_volatile_serves_y_when_its_input_changes_only_in_those` | ruled: bug |
| 2 | Loop, 2nd of 3 visits changed, state accumulates | supported | Served, diverged, after divergence: `test_a_changed_visit_and_every_later_one_rerun_when_state_accumulates` | |
| 2 | Loop, 2nd visit changed, 3rd input still matches | supported | Served, diverged, served: `test_only_the_changed_visit_reruns_when_the_next_input_still_matches` | |
| 2 | Loop, changed visit matches another recorded visit | supported | Gets that visit's output, which is right if a node's output follows its input: `test_a_visit_that_matches_another_recorded_visit_is_served_that_visit_s_output` | |
| 3 | Send, payload `task` or `task_id`, one task changes | supported | 2 served, 1 diverged, ran only for `B`: `test_only_the_task_whose_payload_changed_reruns` | |
| 3 | Send, one task added | supported | 2 served, 1 diverged, ran only for `c`: `test_an_added_task_runs_live_and_the_recorded_ones_are_served` | |
| 3a | Send, payload `id`, ids `a,b,c` become `x,y,z` | fixed | Each worker re-runs: `test_tasks_that_differ_only_in_id_are_not_served_recorded_outputs`. `lgcov replay` exits 1: `test_lgcov_replay_flags_a_pr_that_changes_every_task_s_id`. A `ToolCall` in a payload is still served, and answers this run's id: `test_a_worker_given_a_plain_tool_call_answers_this_run_s_id` in `test_cassette_messages.py` | ruled: bug |
| 4 | Subgraph, no parent checkpointer, default or `checkpointer=True` | supported | Always live, next node diverged: `test_a_subgraph_runs_live_and_the_node_after_it_reruns`, `test_an_unchanged_subgraph_still_runs_live` | |
| 4a | Parent has a checkpointer, either subgraph setting | bug found: no `unsupported` | `ValueError` from LangGraph (`_run` passes no `thread_id`): `test_a_graph_with_a_checkpointer_replays_or_says_unsupported`, `test_a_graph_with_a_checkpointer_records_or_says_unsupported`. `lgcov replay` exits 2: `test_lgcov_replay_fails_loudly_on_a_graph_it_can_t_replay` | T133 |
| 5 | `add_messages`, content changes | supported | `y` diverged, ran once: `test_y_reruns_when_a_message_s_content_changes` | |
| 5 | Duplicate id stops replacing a message | supported | Both kept, `y` diverged: `test_y_reruns_when_a_duplicate_id_no_longer_replaces_a_message` | |
| 5 | Duplicate id leaves Y the same messages | supported | `y` correctly served: `test_y_is_served_when_a_duplicate_id_leaves_it_the_same_messages` | |
| 5 | Duplicate ids that can't be fitted to this run | supported | Runs live, says `rebind_skipped`: `test_an_ambiguous_fit_runs_the_step_live_and_says_so` in `test_cassette_messages.py` | |
| 5a | Message changes only in `artifact`, `additional_kwargs`, `finish_reason` or `stop_reason`, and Y reads it | fixed | Re-runs, and gives what a live run gives: `test_y_is_not_served_when_a_message_changes_only_in_what_a_node_can_act_on` | ruled: bug |
| 5a | Message changes only in `id`, `usage_metadata` or the rest of `response_metadata`, and Y reads it | accepted | Served, no drift: `test_y_is_served_when_a_message_changes_only_in_what_stays_ignored` | ruled: accepted noise |
| 6 | Interrupt on replay | supported | The node runs again and is never served: `test_an_interrupted_call_is_never_served_and_fires_again_on_replay` | |
| 6a | Interrupted run, no checkpointer | bug found: no `unsupported` | `TypeError: Object of type Interrupt is not JSON serializable`: `test_an_interrupted_run_replays_or_says_unsupported`. `lgcov replay` exits 2: `test_lgcov_replay_fails_loudly_on_a_graph_it_can_t_replay` | T133 |
| 6b | Interrupted run, with a checkpointer | bug found: no `unsupported` | The `ValueError` in 4a: `test_an_interrupted_run_with_a_checkpointer_replays_or_says_unsupported` | T133 |
| 7 | Tool that writes, input unchanged | supported | Served, no write: `test_a_write_whose_input_is_unchanged_is_served_and_doesn_t_run_again` | |
| 7a | Tool that writes, input changed | supported | One write, reported as diverged: `test_a_write_whose_input_changed_runs_once_and_is_reported_as_diverged` | |
| 7b | Write with a retry policy, node raises `ValueError` | supported | 1 write, as outside lgcov: `test_replay_makes_the_attempts_the_agent_makes_outside_lgcov` | |
| 7c | Write with a retry policy, transient error | supported | 2 writes, both logged, and the report names the node: `test_every_attempt_at_a_retried_write_is_in_the_calls` | |
| 7d | Y reads from a Store what a changed X wrote | bug found | Served `old`, live `new`, no drift: `test_y_is_not_served_when_what_it_reads_from_a_store_changed` | ruled: documented limit |
| 8 | Async X → Y → outcome, regression check for T164 (PR #54) | supported | Same as row 1, and an unchanged graph is fully served: `test_async_y_reruns_when_x_s_prompt_changes_its_input`, `test_an_unchanged_async_graph_is_served_without_running` | |

No row was left not audited.
