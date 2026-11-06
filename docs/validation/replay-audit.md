# Replay audit: changed inputs (T152)

Does cassette replay ever serve a stale output when a node's input changed, and does every
pattern it can't replay safely say `unsupported` with a reason? Audited on 6 Nov 2026 at
`dc2dfa0`, with LangGraph 1.2.16, on Python 3.12.4 and 3.15.0.

**Today the answer is no to both.** Replay is right when a change reaches a part of the input
the key can see, which covers plain chains, loops, Send fan-out, plain subgraphs, reducers and
async nodes. It serves stale outputs, with no drift, when a change reaches only a masked value
or a Store. lgcov has no `unsupported` result, so what it can't replay ends in a raw error. And
a retry policy can repeat a write that no report mentions.

Nothing is fixed here. Each finding is a strict xfail in
`tests/test_cassette_replay_audit.py`, so a fix fails its test until the marker comes off.
To see them fail:

```bash
uv run pytest tests/test_cassette_replay_audit.py --runxfail
```

## Side-effect safety

The first fix. A node that writes can run more times under lgcov than the report says. Both
cases need a `RetryPolicy` on the node, here with `max_attempts=3`.

| Case | Writes | What the report shows | Tests |
|---|---|---|---|
| 7b. The node writes, then raises `ValueError` | Plain LangGraph: **1**. Under lgcov: **3** | The crash, and nothing about three attempts | `test_plain_langgraph_doesn_t_retry_a_node_s_value_error`, `test_replay_doesn_t_repeat_a_write_that_langgraph_wouldn_t_retry` |
| 7c. The first attempt writes and hits `ConnectionError`, the second succeeds | **2** writes, **1** logged call; the failed attempt is unlogged | No line at all, where a write with no retry gets `Diverged, so run live` | `test_every_attempt_at_a_retried_write_is_in_the_calls`, `test_a_retried_write_whose_input_changed_is_reported_as_diverged` |

Why each happens:

- **7b.** `_named` wraps every node error in `NodeError`. LangGraph's `default_retry_on` refuses
  `ValueError` and similar types but retries any type it doesn't recognise, so the wrapper turns
  an error it wouldn't retry into one it does.
- **7c.** A `Call` is logged only when a call returns. The failed attempt uses up the run's
  `diverged` label, the retry is logged as `after_divergence`, and `_divergence_lines` prints
  nothing without a `diverged` call.

With no retry policy, writes behave as they should: an unchanged input is served with no write,
and a changed input writes exactly once and is reported as diverged. That one write is real.
Replay can't tell that a node writes, so it runs it against whatever the node is connected to.

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
| 7b | Write with a retry policy, node raises `ValueError` | bug found | 1 write becomes 3: see Side-effect safety | first fix |
| 7c | Write with a retry policy, transient error | bug found | 2 writes, 1 logged call, no report line: see Side-effect safety | first fix |
| 7d | Y reads from a Store what a changed X wrote | bug found | Served `old`, live `new`, no drift: `test_y_is_not_served_when_what_it_reads_from_a_store_changed` | documented limit |
| 8 | Async X → Y → outcome, regression check for T164 (PR #54) | supported | Same as row 1, and an unchanged graph is fully served: `test_async_y_reruns_when_x_s_prompt_changes_its_input`, `test_an_unchanged_async_graph_is_served_without_running` | |

No row was left not audited.
