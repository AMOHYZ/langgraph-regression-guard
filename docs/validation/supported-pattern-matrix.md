# Supported-pattern matrix (T133)

When is a drift label safe, and when must lgcov say `unsupported` with a reason instead?
PR A covers cases 1–3. PR B covers cases 5, 7, 8, and 9 (Store). Cases 4 and 6 land in PR C.

| # | Pattern | Safe label? | When `unsupported` | Reason substring | Evidence |
|---|---|---|---|---|---|
| 1 | Repeated visits / occurrence pairing | **Yes**, when visit counts match (T152 fixture 2: middle visit change keeps root/propagated) | Visit counts differ for a labelled node — `accept_nodes` already refuses to pair those visits | `occurrence pairing is unsafe` | `test_matched_visit_counts_keep_a_correct_root_label`, `test_mismatched_visit_counts_become_unsupported` |
| 2 | Node added or deleted | **Yes**, clear add/delete when **both** graph shapes are trusted (T238 root) | Graph shape missing/untrusted, **or** add+delete that looks like a rename | `graph shape missing or untrusted` / `looks like a rename` | `test_clear_add_with_trusted_graphs_stays_root`, `test_missing_graph_shape_is_unsupported_not_inconclusive`, `test_rename_like_add_and_delete_is_unsupported` |
| 3 | State schema / reducer change | **Yes**, when a real fingerprint root explains the run (schema unchanged or root is real) | Channel/reducer fingerprints differ **and** the label would be UNEXPLAINED or only-propagated (failing example: reducer `add`→replace) | `state schema or reducer changed` | `test_reducer_change_becomes_unsupported`, `test_node_body_change_keeps_root_when_schema_unchanged` |
| 4 | Send per-task state | later (PR C) | | | |
| 5 | Subgraph checkpointer | **Yes**, subgraph-only `checkpointer=True` or default with **no** parent checkpointer (T152 row 4) | Parent / compiled graph has a checkpointer (`thread_id` required) — was a crash | `checkpointer` | `test_parent_checkpointer_replay_is_unsupported_not_a_crash`, `test_subgraph_checkpointer_without_parent_stays_supported`, `test_a_graph_with_a_checkpointer_replays_or_says_unsupported` |
| 6 | `add_messages` id duplication | later (PR C) | | | |
| 7 | Parallel `InvalidUpdateError` | n/a (was crash) | Parallel writers update one channel without a reducer — was a crash on record/replay | `InvalidUpdateError` / `parallel` | `test_parallel_invalid_update_replay_is_unsupported_not_a_crash`, `test_parallel_invalid_update_record_is_unsupported_not_a_crash` |
| 8 | Re-triggered interrupt | n/a (was crash) | Fresh or recorded `__interrupt__` step — was TypeError on compare / checkpointer ValueError | `interrupt` | `test_interrupted_run_replay_is_unsupported_not_a_crash`, `test_an_interrupted_run_replays_or_says_unsupported` |
| 9 | Store reads | **No claim of value-change detection** | Compiled graph has a `store` and `serve=True` — Store is not in the serving key; refuse serve instead of silent stale (T152 7d). Live (non-serve) still runs. | `Store reads are not in the serving key` | `test_store_graph_serve_is_unsupported_not_silent_stale`, `test_y_is_not_served_when_what_it_reads_from_a_store_changed` |

`unsupported` maps to the existing verdict table row of the same name: `needs_human`, exit 0 (not switchable via `fail-on-inconclusive`). No new exit code.

## Demo / synthetic impact (PR B)

Measured on this branch before merge (offline `_node_labels` / serve path, plus `replay_run(...).unsupported`). **Result: zero** existing cases become `unsupported`.

| Suite | Cases checked | Became `unsupported` |
|---|---:|---:|
| Demo mutations (`examples/demo_agent`, seeds→distinct cases) | 20 | **0** |
| Other synthetic agents (`fanout`, `subgraph`, … via `every_case`) | 72 | **0** |
| Shared synthetic (`every_shared_case`) | 30 | **0** |

Visit counts of 0 vs N (route started/stopped reaching a node) stay T238 labels, not occurrence
`unsupported`. Demo/synthetic agents do not compile with a parent checkpointer, Store, interrupt, or
parallel conflict channel.
