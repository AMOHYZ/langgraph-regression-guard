# Changelog

## Unreleased

- `lgcov round init | lock | check` lock a benchmark round's commits and cases, and an exposure ledger catches a moved SHA or a case a candidate has already seen ([docs](docs/benchmark-rounds.md)).
- `lgcov bench` runs each side in its own process, with its own imports; a bad import exits 2 and names the side (#57).
- `lgcov bench --keep-runs DIR` saves each side's runs as they finish, so a crash can't lose a paid recording (#58).
- `lgcov bench` shows what each side prints, named by side, like the reason a researcher failed (#60).
- Graphs with `async def` nodes or routers record and replay; served nodes make no real call (#54).
- State holding LangChain messages records, replays and compares; new ids and metadata aren't drift (#55).
- A served step's ids are fitted to the run's own; if they can't be, it runs live and reports `rebind_skipped` (#56).
- A node that returns `Command` is served, and a change to where it sends the run is drift (#59).
- A node that reaches a pydantic class keeps its fingerprint across processes, so it's served when unchanged (#65).
- That now holds for a class with a validator, serializer, computed field or generic, and setting an attribute or copying an instance no longer changes a fingerprint. Such a node ran live on every replay, and a model's new answer read as drift.
- Packages in any `site-packages` folder count as library code, as under `uv run --with`. After upgrading, re-record a node that reaches a project pydantic class: until then it runs live.
- An edit behind a pydantic class that builds at first use (a forward reference, `defer_build`), or on Python 3.14+ to a TypedDict's or NamedTuple's field types, now changes the fingerprint, and that first use doesn't. Such a node was served stale, so the change passed unseen. On Python 3.14+, re-record a node that reaches any project class but a stdlib dataclass: until then it runs live.
- The first real PR through `lgcov bench`, open_deep_research #163, scored as a pilot: one PR, one question (#61, #62, #63, #64).
- On a PR the gate reads `lgcov.yaml`, the taxonomy and the bands from the base commit, so a PR can't change what grades it; the first adoption PR is advisory. [What this doesn't stop yet](docs/trust-boundary.md).
- `lgcov ci --result-out` binds the result to the commits and the policy, and `lgcov verify-result` checks it before the PR comment; a mismatch posts no verdict and exits 2.
- Every `lgcov ci` outcome gives one headline, next step and exit code, in `docs/verdict-actions.md`: INCONCLUSIVE shows as "No decision: needs_human", a PR that edits its own cassettes can't SHIP, `verdict` gains `needs_human`, and the Action gains `mode`, `approved-by` and `ci-status`.
- A code check that passed on the base and fails on the PR blocks, whatever the paired test says, and a run replay stopped early no longer counts as a failed check.
- A re-baseline in a PR can't hide a code check's new failure: when a PR edits its cassettes, blocking code checks also run against the base's. A run it removes from them is named in the comment.
- A node's retry policy and error handler see the node's own error, as they do outside lgcov: a `ValueError` no longer repeats a write, and `on-divergence: fail` now stops a node with a retry policy. Each failed attempt is a live call, and the report names the node.
- **Behaviour change:** a node now re-runs when its input changes only in an `id`, a UUID or date-time in text, or a message's `artifact`, `additional_kwargs`, `finish_reason` or `stop_reason`. These were served stale with no drift. Existing cassettes keep working with no re-record, but more nodes can run live on a PR. To get the old matching back for ids, UUIDs and date-times, set `mask_volatile_in_key: true` in `lgcov.yaml`. To drop one field, use `ignore_paths`. [How a step is matched](docs/validation/replay-audit.md#how-a-step-is-matched).
- A graph file or `--client` file stays in `sys.modules` while it runs, as an import leaves it, so `from __future__ import annotations` with a dataclass, a `TypedDict` state or a pydantic model, and a parametrized pydantic generic, no longer crash lgcov. A file named like a real module doesn't hide it, and an edit to a class in that file now counts.
- **Behaviour change:** with that fix, these nodes get a new fingerprint, so they run live until you re-record them: one that uses a `@dataclass` from the graph file, one that uses a class from a graph file named like a real module, and one that reaches a pydantic model in the graph file whose annotations, under `from __future__ import annotations`, use more than builtins, which couldn't be built before. With `from __future__ import annotations`, a node typed with a narrower input `TypedDict` now gets only those keys, as it does outside lgcov. A graph file named like a package that only its own code adds to `sys.path` no longer loads: put that package on `PYTHONPATH` instead.

## 0.1.0 (2026-11-02)

The first release: what was built from 25 to 31 October 2026, by layer, one line per
feature, with its PR.

### L1 — Cassettes

- `lgcov --version`, `lgcov record`, and a quickstart that starts with the install (#10).
- `lgcov replay`: drifted nodes against the recorded cassettes, root vs propagated (#6).
- Volatile fields (UUIDs, timestamps, ids, `ignore_paths`) masked before comparing (#12).
- Unchanged nodes served from the cassette: replay runs only what a change touched (#18).
- Drift gate (`--fail-on root-drift|any-drift|never`) and `lgcov snapshot` (#16).
- Node and edge coverage, with the trend against main, in the PR comment (#20, #21).
- Outcomes PASS / AGENT_FAIL / INFRA_ERROR: a node that raises is the agent's, network
  trouble isn't (#24); `lgcov selfcheck` proves the classification on fixtures (#25).
- Divergence policy, a time budget and a timing line; `pr` and `nightly` tiers (#28).
- `thresholds.min_node_coverage` gates `lgcov ci` (#30).
- A router, or a node inside a subgraph, that raises is named like a node (#35), and so
  is a router that returns a key its mapping doesn't have (#39).
- `lgcov lint`: unreachable nodes, routers and edges naming missing nodes, nodes no
  cassette reached, nodes no KPI names; warnings only (#36).

### L2 — Judge gate

- Deterministic code judges: node visited, tool called, tool not called (#2).
- A failure-mode taxonomy for open_deep_research's PR #163, one binary KPI per mode (#14),
  its names checked against the PR's merge commit (#38).
- `lgcov taxonomy draft` proposes failure modes and KPIs from traces; `lgcov taxonomy
  approve` is a human's yes, and a pending entry can't gate (#38).
- pass@k, pass^k, flakiness and cost per KPI (#22); `lgcov judge`, with a call guard (#23).
- Paired significance statistics (#26) and the gate: SHIP, BLOCK or INCONCLUSIVE per KPI,
  in `lgcov judge` and the Action (#27).
- `lgcov calibrate`: TPR/TNR with Wilson intervals and Cohen's κ against human labels;
  calibration bands decide which judges can block (#32).
- KPI results per node and per slice; the overall verdict is the worst group (#34).

### L3 — Review loop

- `lgcov cassette accept <node>...` re-baselines only the nodes a reviewer accepts (#11).
- `lgcov review override`: an override becomes a label, and bands move on the next PR,
  through git (#33).

### The Action and the PR comment

- A Markdown PR report with a golden test (#1); a graph-diff comment on PRs (#3), updated
  in place rather than added again (#4).
- `lgcov.yaml`, a typed config that replay and the Action read (#9).
- A reusable composite Action, dogfooded on this repo (#17); its exit-code steps survive
  Actions' `bash -e` (#19).

### Benchmarks and docs

- The demo agent: 3 nodes, 1 tool, 1 conditional edge, a fake LLM (#5).
- `docs/why-us.md`: what lgcov does that others don't, with an honest status each (#15,
  #29, #34).
- Speed benchmark: served vs `--live` on the demo PR (#31).
- `lgcov bench`: the attribution benchmark, on synthetic fixtures (#34).
- The first real benchmark row, open_deep_research PR #163, labels pending human review
  (#38).
- The README roadmap lists only what isn't built (#37).

### Packaging and release

- This changelog, an Install section, and `release.yml` by hand as a dry run that builds
  the wheel and checks it runs (#40).
- The package is named `lgcov`, like its command; the repo keeps its name (#41).
