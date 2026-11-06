# Replay audit: changed inputs (T152)

Does cassette replay ever serve a stale output when a node's input changed, and does every
pattern it can't replay safely say `unsupported` with a reason? Audited on 6 Nov 2026 at
`dc2dfa0`, with LangGraph 1.2.16, on Python 3.12.4 and 3.15.0.

**Today the answer is no to both.** Replay is right when a change reaches a part of the input
the key can see, which covers plain chains, loops, Send fan-out, plain subgraphs, reducers and
async nodes. It serves stale outputs, with no drift, when a change reaches only a masked value
or a Store. lgcov has no `unsupported` result, so what it can't replay ends in a raw error.

The retried writes the audit found are fixed: see Side-effect safety. Each finding still open is
a strict xfail in `tests/test_cassette_replay_audit.py`, so a fix fails its test until the marker
comes off. To see them fail:

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
| masked input | `json.dumps(normalize(state, ignore_paths), sort_keys=True)`. An input that isn't JSON gives no key, so that call always runs live |

- The mask hides the values of `id`, `message_id`, `request_id`, `run_id`, `span_id`,
  `tool_call_id` and `trace_id` at any depth, UUIDs and date-times inside any string, and
  anything under `ignore_paths`.
- In a message, only type, content, name, tool call names and args, and status count. Its `id`,
  `additional_kwargs`, `response_metadata`, `usage_metadata` and `artifact` don't.
- Each recorded step is served once, and the first unused match wins. Visit number and position
  aren't in the key, and nor are `config`, `runtime` or `store`.
- Subgraphs, `ToolNode` and nodes with no fingerprint are never served. Routers always run.

The masking is deliberate (T085, T165, and the README's replay section): ids that change on
every run still have to match, or little would be served.

## Method

`_audit` replays the PR graph as `lgcov ci` does, with `replay_run(..., serve=True)` under the
default `on_divergence="flag"`, and compares it with a fully live run of the same graph. It
compares raw outputs, unmasked, so it doesn't rely on the key logic it checks. Tests also assert
how each call ran and how many times a spy ran. Cassettes go through JSON, as they do in the CLI.
Everything is offline with fakes.

## Matrix

Status is what the tests show. Proposed ruling is a proposal, not yet ruled, and applies only to
rows with an open finding.

| # | Pattern | Status | Evidence | Proposed ruling |
|---|---|---|---|---|
| 1 | X → Y → outcome, X's prompt changes Y's input | supported | `y` diverged and ran once, `outcome` re-ran: `test_y_reruns_when_x_s_prompt_changes_its_input` | |
| 1 | X's prompt changes, its output doesn't | supported | `y` served, ran 0 times: `test_y_is_served_when_x_s_prompt_changes_but_its_output_doesn_t` | |
| 1a | Y's input changes only in an `id` value, a date-time or a UUID in text | bug found | Served `handle ticket-1`, `on time`, `vip`; live `handle ticket-2`, `late`, `standard`; no drift: `test_y_is_not_served_when_its_input_changes_only_in_a_masked_value` | bug |
| 2 | Loop, 2nd of 3 visits changed, state accumulates | supported | Served, diverged, after divergence: `test_a_changed_visit_and_every_later_one_rerun_when_state_accumulates` | |
| 2 | Loop, 2nd visit changed, 3rd input still matches | supported | Served, diverged, served: `test_only_the_changed_visit_reruns_when_the_next_input_still_matches` | |
| 2 | Loop, changed visit matches another recorded visit | supported | Gets that visit's output, which is right if a node's output follows its input: `test_a_visit_that_matches_another_recorded_visit_is_served_that_visit_s_output` | |
| 3 | Send, payload `task` or `task_id`, one task changes | supported | 2 served, 1 diverged, ran only for `B`: `test_only_the_task_whose_payload_changed_reruns` | |
| 3 | Send, one task added | supported | 2 served, 1 diverged, ran only for `c`: `test_an_added_task_runs_live_and_the_recorded_ones_are_served` | |
| 3a | Send, payload `id`, ids `a,b,c` become `x,y,z` | bug found | Served results for `a`, `b`, `c`; live results for `x`, `y`, `z`: `test_tasks_that_differ_only_in_id_are_not_served_recorded_outputs`. `lgcov replay` exits 0: `test_lgcov_replay_flags_a_pr_that_changes_every_task_s_id` | bug |
| 4 | Subgraph, no parent checkpointer, default or `checkpointer=True` | supported | Always live, next node diverged: `test_a_subgraph_runs_live_and_the_node_after_it_reruns`, `test_an_unchanged_subgraph_still_runs_live` | |
| 4a | Parent has a checkpointer, either subgraph setting | bug found: no `unsupported` | `ValueError` from LangGraph (`_run` passes no `thread_id`): `test_a_graph_with_a_checkpointer_replays_or_says_unsupported`, `test_a_graph_with_a_checkpointer_records_or_says_unsupported`. `lgcov replay` exits 2: `test_lgcov_replay_fails_loudly_on_a_graph_it_can_t_replay` | T133 |
| 5 | `add_messages`, content changes | supported | `y` diverged, ran once: `test_y_reruns_when_a_message_s_content_changes` | |
| 5 | Duplicate id stops replacing a message | supported | Both kept, `y` diverged: `test_y_reruns_when_a_duplicate_id_no_longer_replaces_a_message` | |
| 5 | Duplicate id leaves Y the same messages | supported | `y` correctly served: `test_y_is_served_when_a_duplicate_id_leaves_it_the_same_messages` | |
| 5 | Duplicate ids that can't be fitted to this run | supported | Runs live, says `rebind_skipped`: `test_an_ambiguous_fit_runs_the_step_live_and_says_so` in `test_cassette_messages.py` | |
| 5a | Message changes only in `artifact` or `additional_kwargs`, and Y reads it | bug found | Served `1`, `{}`; live `2`, `{'refusal': 'no'}`; no drift: `test_y_is_not_served_when_a_message_changes_only_where_the_key_can_t_see` | bug |
| 5a | Message changes only in `response_metadata`, `usage_metadata` or `id`, and Y reads it | bug found | Served `stop`, 2 tokens, `m1`; live `length`, 4 tokens, `m2`; no drift: same test | accepted noise |
| 6 | Interrupt on replay | supported | The node runs again and is never served: `test_an_interrupted_call_is_never_served_and_fires_again_on_replay` | |
| 6a | Interrupted run, no checkpointer | bug found: no `unsupported` | `TypeError: Object of type Interrupt is not JSON serializable`: `test_an_interrupted_run_replays_or_says_unsupported`. `lgcov replay` exits 2: `test_lgcov_replay_fails_loudly_on_a_graph_it_can_t_replay` | T133 |
| 6b | Interrupted run, with a checkpointer | bug found: no `unsupported` | The `ValueError` in 4a: `test_an_interrupted_run_with_a_checkpointer_replays_or_says_unsupported` | T133 |
| 7 | Tool that writes, input unchanged | supported | Served, no write: `test_a_write_whose_input_is_unchanged_is_served_and_doesn_t_run_again` | |
| 7a | Tool that writes, input changed | supported | One write, reported as diverged: `test_a_write_whose_input_changed_runs_once_and_is_reported_as_diverged` | |
| 7b | Write with a retry policy, node raises `ValueError` | supported | 1 write, as outside lgcov: `test_replay_makes_the_attempts_the_agent_makes_outside_lgcov` | |
| 7c | Write with a retry policy, transient error | supported | 2 writes, both logged, and the report names the node: `test_every_attempt_at_a_retried_write_is_in_the_calls` | |
| 7d | Y reads from a Store what a changed X wrote | bug found | Served `old`, live `new`, no drift: `test_y_is_not_served_when_what_it_reads_from_a_store_changed` | documented limit |
| 8 | Async X → Y → outcome, regression check for T164 (PR #54) | supported | Same as row 1, and an unchanged graph is fully served: `test_async_y_reruns_when_x_s_prompt_changes_its_input`, `test_an_unchanged_async_graph_is_served_without_running` | |

No row was left not audited.
