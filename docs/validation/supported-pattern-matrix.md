# Supported-pattern matrix (T133)

When is a drift label safe, and when must lgcov say `unsupported` with a reason instead?
PR A covers cases 1–3. Cases 4–9 land in later PRs.

| # | Pattern | Safe label? | When `unsupported` | Reason substring | Evidence |
|---|---|---|---|---|---|
| 1 | Repeated visits / occurrence pairing | **Yes**, when visit counts match (T152 fixture 2: middle visit change keeps root/propagated) | Visit counts differ for a labelled node — `accept_nodes` already refuses to pair those visits | `occurrence pairing is unsafe` | `test_matched_visit_counts_keep_a_correct_root_label`, `test_mismatched_visit_counts_become_unsupported` |
| 2 | Node added or deleted | **Yes**, clear add/delete when **both** graph shapes are trusted (T238 root) | Graph shape missing/untrusted, **or** add+delete that looks like a rename | `graph shape missing or untrusted` / `looks like a rename` | `test_clear_add_with_trusted_graphs_stays_root`, `test_missing_graph_shape_is_unsupported_not_inconclusive`, `test_rename_like_add_and_delete_is_unsupported` |
| 3 | State schema / reducer change | **Yes**, when a real fingerprint root explains the run (schema unchanged or root is real) | Channel/reducer fingerprints differ **and** the label would be UNEXPLAINED or only-propagated (failing example: reducer `add`→replace) | `state schema or reducer changed` | `test_reducer_change_becomes_unsupported`, `test_node_body_change_keeps_root_when_schema_unchanged` |
| 4 | Send per-task state | later (PR C) | | | |
| 5 | Subgraph checkpointer | later (PR B) | | | |
| 6 | `add_messages` id duplication | later (PR C) | | | |
| 7 | Parallel `InvalidUpdateError` | later (PR B) | | | |
| 8 | Re-triggered interrupt | later (PR B) | | | |
| 9 | Store reads | later (PR B/C); if detection is unreliable, document the limit — do not claim detection | | | |

`unsupported` maps to the existing verdict table row of the same name: `needs_human`, exit 0 (not switchable via `fail-on-inconclusive`). No new exit code.

## Demo / synthetic impact (PR A)

Measured on this branch before merge (offline `_node_labels` / serve path, trusted graphs when
known). **Result: zero** existing cases become `unsupported`.

| Suite | Cases checked | Became `unsupported` |
|---|---:|---:|
| Demo mutations (`examples/demo_agent`, seeds→distinct cases) | 20 | **0** |
| Other synthetic agents (`fanout`, `subgraph`, … via `every_case`) | 72 | **0** |
| Shared synthetic (`every_shared_case`) | 30 | **0** |

Visit counts of 0 vs N (route started/stopped reaching a node) stay T238 labels, not occurrence
`unsupported`.
