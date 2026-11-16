# Changelog

## Unreleased

- **Gate behaviour change.** A node only one side ran is no longer root just for that. It is root only when the other side's graph doesn't have it: the PR added or removed it. A node that is in both graphs, which a run merely stopped or started reaching, is propagated when a root or a changed router before it explains the run, and it gets the same label whether the baseline holds one run or several. **Limit:** `lgcov ci` doesn't know base's graph yet, in one job or two. So a node only the PR reached, which no baseline run ever reached, reads `inconclusive (only the PR ran it, and base's graph wasn't given…)` whether the PR added it or newly reached it. It blocks under `fail-on: root-drift` either way, as it did when it read root. The label becomes precise once the baseline recording holds the graph's node names. `lgcov bench` on real rows and `--from-runs` have neither graph, so there a node only one side ran is always inconclusive. The synthetic bench has both graphs.
- `lgcov bench --synthetic` also scores edits the diff can't pin to one node: 30 cases from 10 edits on a demo-shaped agent, where a helper, the model name or a config default is read by 2 or more nodes. One build runs a node inside a subgraph, and one keeps the model name in a client object. Each case is scored against regression roots (the edit at that node alone fails the outcome check) and drift roots (that node's own output changes). The edited node from the diff is undefined there, so it is scored as all touched nodes. `docs/benchmark-synthetic.json` holds every case under `shared`. Result: each of the 21 nodes lgcov names is a drift root, and 9 of them are regression roots. It misses 11 of 32 drift roots: 6 inside a subgraph, which it calls inconclusive, and 5 where the model name sits in a plain-class client object, which changes no fingerprint.
- **Gate behaviour change.** A graph whose state is a pydantic model is now replayed as a `TypedDict` state is: each node is keyed by the state's fields, so an unchanged node is served and a drifted one is labelled by its fingerprint. Before, every node ran live, and a drifted one read `inconclusive (no fingerprint: a subgraph or the like (T193))`, which named the wrong cause. **Re-record your baseline to get this:** one recorded earlier holds no inputs for these nodes, so they keep the old label. **Limit:** a node whose input holds another pydantic object, such as a `Document`, still can't be keyed, so it runs live.
- In a graph whose state is a dataclass, a node whose code changed is now probed on base's input rebuilt as the state class, as LangGraph builds it, so it can be root. Before, once the baseline was read from its file, it read `inconclusive (raised AttributeError on base's input)`. A pydantic state is probed the same way, and a `Send`'s payload is passed as it was sent.
- lgcov now reads recording schema 2, the baseline format of the coming schema change ([design](docs/design/cassette-schema.md)), with every field it adds, so a job on this release can read a baseline a later lgcov records. In a gate run in two jobs, the job that decides also reads schema 3 of the candidate job's file. It acts on none of those fields yet and still writes today's formats, so existing baselines load and save byte for byte. A baseline in a schema it doesn't read is refused by its number (`schema 3; this lgcov reads 1 and 2`); before, such a file was read as today's format, or failed with a TypeError on a field it didn't know. `lgcov cassette accept` keeps a schema-2 baseline's number and fields, and refuses one whose fingerprints are in a scheme other than the one this lgcov records, rather than mix the two.
- **Gate behaviour change.** An object of a class defined in your project now counts by what it holds, though its repr hides it: its attributes and slots, followed up to 4 objects deep and 64 KiB of text per object. So a model name kept in your own client class changes the fingerprint of every node that reaches the object, and those nodes run live instead of being served stale. Past either limit, or when such an object holds a compiled graph, the node gets no fingerprint and runs live, as one naming a compiled graph does. An attribute named for a secret is skipped: one where a whole word of the name, split on `_` and camelCase, is `key`, `apikey`, `token`, `secret`, `password`, `passwd`, `credential`, `credentials` or `auth`. Library objects count as before, and so do objects whose class inherits from a library class with state of its own: pydantic models, Enums, exceptions, LangChain callback handlers. **Re-record** the nodes that reach such an object: until then they run live and read as changed. A value on such an object that differs from one process or run to the next, such as a timestamp or a counter a run increments, makes its node run live. `lgcov bench --synthetic` now sees T182's model swap inside a client object: lgcov misses 6 of 32 drift roots there, all inside a subgraph (was 11), and each of the 26 nodes it names is a drift root, 13 of them regression roots.

## 0.3.0 (2026-11-14)

Everything merged from 9 to 14 November 2026 (#95 to #143), one line per change, with its PR. Docs-only, test-only and refactoring PRs have no line.

### Changed

- **Gate behaviour change: more than one root, and a root needs changed code.** `lgcov ci` and `lgcov bench` call a drifted node root when its own fingerprint changed and it still drifts when run with the PR's code on the input the baseline recorded for it; that run is offline, with the network blocked. A node that drifts only because its input did is propagated. Before, the first node to drift in a run was the root, whatever changed. A changed node that needs a model call can't be checked offline, so it is inconclusive, and under `fail-on: root-drift` inconclusive nodes count with the roots. A node with no fingerprint, such as a subgraph, is inconclusive too. New Action outputs: `first-drift` and `inconclusive-count` (#111, #113, #114).
- **Gate behaviour change.** A node is called propagated only when its input differed between the recording and the PR's run. An unchanged node that drifts on the same input is now inconclusive, wherever it ran, and so is a run's first drifted node when the only root or inconclusive node in the run comes after it, or the only changed router ran after it. Before, both read as propagated. Under `fail-on: root-drift` an inconclusive node blocks, so a run that passed with such a node now fails. In a recording with no step inputs, only the first-drifted-node part applies. The reason text now ends "an edited recording, or a live call's variation". (#138)
- A PR that edits a conditional edge's router now gets the routing node as root: `root (routing change at classify)` in the comment's replay table, beside any root from a changed node. Before, the first drifted node was inconclusive. **Re-record your baseline to get this:** a cassette recorded earlier holds no router fingerprint and keeps the old label. Existing cassettes load and replay unchanged. `lgcov bench` and the synthetic benchmark score router edits the same way. (#116, #118, #120)
- **Limit:** lgcov doesn't check that the route changed, so a router edit that routes as before is named root of drift a node edit caused. A router inside a subgraph isn't fingerprinted. (#116)
- A node's fingerprint now comes from its functions' syntax trees, not their raw lines, so a comment, whitespace, line wrapping, quote style or a redundant bracket no longer makes a node run live or read as changed. The value is the same on every supported Python. A function that calls `inspect.getsource` or `getsourcelines` is still hashed from its raw lines. **Old baselines keep working:** a node whose recorded fingerprint matches the old scheme is byte-identical, so it is still served. Re-record to get the benefit, not to keep working. (#135)
- A node function's own docstring no longer counts in its fingerprint, so editing, writing or deleting one leaves the node served and unchanged. It still counts when something could read it: the function has a decorator, a `ToolNode` in the graph exposes it as a tool, its code or project code it reaches names `__doc__` or `getdoc`, or the docstring shares a line with code. The docstrings of helpers, nested functions, classes and routers count as before. **Limit:** a node function also passed to `bind_tools` isn't seen: after editing its docstring, run with `--live`. Offline, on four labelled real PRs, this changed one node: `generate_query` in google-gemini/gemini-fullstack-langgraph-quickstart#35, whose only edit is its docstring, now reads unchanged. (#135)
- The comparison now masks a provider's response, item, call and request ids written into text, such as `rs_…`, `fc_…`, `resp_…`, `call_…`, `chatcmpl-…`, `msg_…`, `toolu_…`, `tooluse_…` and `req_…`, so a node that turns a message list into a string no longer drifts on ids alone. The replay key still leaves them unless `mask_volatile_in_key` is on. (#131)
- The comparison now masks a blob a provider makes anew on every call: the value of `encrypted_content`, `signature`, `thought_signature` or `thoughtSignature`, when it is 40 or more base64 characters with no space. In text the quoted key and value are enough. In a structured block a plain `signature` is masked only beside `type: thinking`, so a webhook's or a commit's signature stays. The replay key leaves them unless `mask_volatile_in_key` is on. **Limit:** a `redacted_thinking` block's `data`, and a blob under any other key, go through `ignore_paths`. (#132)

### Added and fixed

- `lgcov bench --noise-repeats K` (default 0: nothing extra runs) uses the noise rule below. A changed node that needs a live call, and under `--live` each run's first drifted node, runs K more times on each side, 2K calls per recorded input. A changed node is root only when it drifts above base's own spread, and otherwise inconclusive with `no drift detected above noise (K=n)`. An unchanged node under its spread gets no label. Each check's `k`, `spread`, `min_head_to_base` and verdict are in the results JSON under `noise` and in the report under "Noise checks". `lgcov ci` is unchanged: the gate still treats a changed model node as inconclusive until base samples are in the cassette. (#129)
- A noise rule for root labels: drift counts only when head's nearest sample of a node is further from base's samples than those are from each other. `lgcov bench --synthetic` reports it on the seeded-noise demo agent at K=3 over 200 runs: 1 false alarm with no change, and a small and a large prompt edit caught 68 and 112 times. It hasn't been run on a real agent. (#127)
- **Limit:** the rule misses a shift smaller than the node's own spread (132 and 88 of 200 above), and its default distance is over word sets, so one changed passage in a long output registers only for a node that repeats exactly. A node under the threshold reads `no drift detected above noise (K=n)`, which doesn't clear it. (#127)
- `lgcov bench --live` runs every node on head instead of serving the ones that didn't change. A row with the same commit on both sides then measures how much a live model varies on its own. (#104)
- `lgcov bench --from-runs DIR` scores the runs `--keep-runs` saved, without running the agent. It can't run the offline check above, so a changed node that drifted reads "not probed" (#109, #111).
- `lgcov judge --from-runs DIR` judges the `base.json` and `head.json` that `lgcov bench --keep-runs` saved, with no replay, so the agent makes no call. Model judges still cost their own calls. (#115)
- When a judge call doesn't run, `lgcov judge` and `lgcov ci` now say why under "Infra errors", such as `jev: HTTP 401`, with a count per reason. A transport error is named by its type only, so `JEV_API_URL` never appears. (#123)
- `jev_timeout_s` in `lgcov.yaml` sets how long a Jev judge waits for each attempt (default 10 seconds, as before), so a long case such as a whole report doesn't time out. (#121)
- `tool_called` and `tool_not_called` now see the tools a run called, read from the AI messages in its recorded and replayed steps, in `lgcov ci` and `lgcov judge`. Before, they got node names only, so `tool_called` always failed and `tool_not_called` always passed. (#97)
- **Limit:** a tool check reads the whole run, not only its KPI's `node:`, so a tool called by another node passes it. `lgcov ci` and `lgcov judge` say so for each tool check: "node scope not applied to tool checks yet (whole-run)". (#97)
- Under the KPI gate heading, a model judge that can only flag or advise now gets a line saying what it still needs to block: the agreed failures and passes its band is short of, then the labels to pin κ within ±0.10. `bands.json` gains `agreed_failures` and `agreed_passes` per judge; an older lgcov still reads the file (#139).
- The gate table's N* for a blocking KPI is now sized at α/K_gate, Holm's strictest step, so it no longer understates the cases a BLOCK needs when more than one KPI blocks. With one blocking KPI, and for advisory KPIs, it is unchanged. MDE stays at α. (#117)
- `lgcov.calibration.case_folds` and `cross_fit`: seeded folds split by case, so a case with several label rows is never on both sides of a fold. Nothing uses them for the bands yet; `tune_threshold` still splits by row. (#119)
- `lgcov round new` locks a round's plan in `lgcov-round.yaml`, and `lgcov guard` exits 2 if the plan moved or the commit isn't a locked candidate. `exposure.json` records which judge versions and candidates saw each case, across rounds. The Action doesn't run this yet. [Rounds](docs/round.md). (#136)
- A gate round after the first now carries `reference`, the first round's number and digest, inside its own digest. `lgcov guard` exits 2 if it is edited or missing. Nothing counts cumulative loss from it yet, and nothing re-checks the reference round itself. [The reference round](docs/round.md#the-reference-round). (#140)
- `lgcov replay` no longer fails with "Object of type … is not JSON serializable" when a node's output holds a dataclass: the fresh run is compared as the cassette saved it, and a step whose input holds one gets a replay key instead of always running live. The cassette format doesn't change (#105).
- `lgcov import langsmith` and the PR report's experiment link work with an org-scoped LangSmith key: set `LANGSMITH_WORKSPACE_ID` and every request names the workspace. Before, such a key got HTTP 403 (#100).
- `lgcov bench --synthetic` scores the labels `lgcov ci` ships: its "lgcov root" row now comes from the same function as the PR comment, with the rule it scored before beside it as "first drift + router rule". A new "Per node" table counts each node named root on its own, with exact match, the inconclusive cases and the roots named only because one side ran the node; `docs/benchmark-synthetic.json` holds them under `per_node`. An inconclusive case names no root: a miss for recall, not counted in precision. Result: by case, precision 0.57 (28 of 49) and recall 0.90 (28 of 31) on all edits; per node, precision 0.47 (28 of 60), because every router or fan-out case gets a second root. (#143)
- Two code checks for a taxonomy KPI, `node_sequence` and `tool_sequence`: the `nodes` or `tools` listed must appear in that order, and other steps may come between. Both read the whole run. A name that neither the base's recorded runs nor the PR's graph has, such as a typo or a node inside a subgraph, leaves the check not checked, with a note, and never fails a PR (#142).
- `release.yml` has a `publish-pypi` job: after TestPyPI takes a tagged release, the same files go to PyPI by trusted publishing, once a reviewer approves the `pypi` environment. 0.3.0 is the first release cut with it. (#137)

## 0.2.0 (2026-11-08)

Everything merged from 2 to 8 November 2026 (#52 to #93), one line per change, with its PR.

### Changed

- **`uses: AMOHYZ/langgraph-regression-guard@v0` moves from PR 19 (31 October) to this release.** A workflow on `@v0` picks up everything below at once, including the three changes in this section, the `mode`, `approved-by`, `ci-status` and `phase` inputs, and the base-commit gate. The defaults keep a single job (`phase: all`), enforcing.
- **A node now re-runs when its input changes only in an `id`, a UUID or date-time in text, or a message's `artifact`, `additional_kwargs`, `finish_reason` or `stop_reason`** (#78). These were served stale with no drift. Existing cassettes keep working with no re-record, but more nodes can run live on a PR. To get the old matching back for ids, UUIDs and date-times, set `mask_volatile_in_key: true` in `lgcov.yaml`. To drop one field, use `ignore_paths`. [How a step is matched](docs/validation/replay-audit.md#how-a-step-is-matched).
- **Re-record after upgrading: these nodes get a new fingerprint** (#88, #90), so each runs live on every replay until it's re-recorded, which costs model calls and can read as drift:
  - on any Python, a node that reaches a pydantic model or pydantic dataclass defined in your project, decorated or not, including one built at first use (a forward reference, `defer_build`);
  - in a recording made under `uv run --with`, or with another environment's site-packages on `PYTHONPATH`, a node that reaches installed library code, which now counts by name;
  - on Python 3.14+, a node that reaches any project class, including plain classes, Enums, TypedDicts and NamedTuples, and under `UV_LINK_MODE=symlink` or `pip install --target` one that reaches an installed class.

  A node that reaches a class with validators, serializers, computed fields or generics, sets an attribute on a pydantic instance, or reaches a LangChain message under `uv run --with` or symlinks was never served before: re-recorded, it is. Plain functions, project helpers and constants, library code in an ordinary environment and, before Python 3.14, plain classes, Enums, TypedDicts and NamedTuples keep their fingerprints. A cassette recorded by `@v0` holds no fingerprints, so its nodes run live until re-recorded, as before.
- **Re-record after upgrading, too: a node that uses a stdlib `@dataclass` from the graph file, or a class in a graph file named like a real module (`json.py`)**, has a new fingerprint (#89), so it runs live until re-recorded. With `from __future__ import annotations`, a node typed with a narrower input `TypedDict` now gets only those keys, as it does outside lgcov. A graph file named like a package that only its own code adds to `sys.path` no longer loads: put that package on `PYTHONPATH` instead.

### Added and fixed

- Under its heading, the PR comment sums up the run in one line, e.g. "1 of 2 runs touched · 1 worse (`order_lookup_runs` 1) · root node: `classify`": the runs where a node drifted, the runs a KPI's judge scored lower than on the base, and the first touched run's root node (#91).
- On a PR the gate reads `lgcov.yaml`, the taxonomy and the bands from the base commit, so a PR can't change what grades it; the first adoption PR is advisory (#69). [What this doesn't stop yet](docs/trust-boundary.md).
- `lgcov ci --result-out` binds the result to the commits and the policy, and `lgcov verify-result` checks it before the PR comment; a mismatch posts no verdict and exits 2 (#71).
- Every `lgcov ci` outcome gives one headline, next step and exit code, in `docs/verdict-actions.md`: INCONCLUSIVE shows as "No decision: needs_human", a PR that edits its own cassettes can't SHIP, `verdict` gains `needs_human`, and the Action gains `mode`, `approved-by` and `ci-status` (#72).
- A code check that passed on the base and fails on the PR blocks, whatever the paired test says, and a run replay stopped early no longer counts as a failed check (#72).
- A re-baseline in a PR can't hide a code check's new failure: when a PR edits its cassettes, blocking code checks also run against the base's (#74). A run it removes from them is named in the comment (#75).
- A gate run can be split in two jobs with the Action's `phase` input: `candidate` runs the PR's code with no keys and no write token, `evaluate` decides from its upload and posts the comment ([trust boundary](docs/trust-boundary.md)). This repo's own PR report runs that way (#83, #84, #85).
- A node's retry policy and error handler see the node's own error, as they do outside lgcov: a `ValueError` no longer repeats a write, and `on-divergence: fail` now stops a node with a retry policy. Each failed attempt is a live call, and the report names the node (#73).
- Graphs with `async def` nodes or routers record and replay; served nodes make no real call (#54).
- State holding LangChain messages records, replays and compares; new ids and metadata aren't drift (#55).
- A served step's ids are fitted to the run's own; if they can't be, it runs live and reports `rebind_skipped` (#56).
- A node that returns `Command` is served, and a change to where it sends the run is drift (#59).
- A node that reaches a pydantic class keeps its fingerprint across processes, so it's served when unchanged (#65).
- That now holds for a class with a validator, serializer, computed field or generic, and setting an attribute or copying an instance no longer changes a fingerprint. Such a node ran live on every replay, and a model's new answer read as drift (#88).
- Packages in any `site-packages` folder count as library code, as under `uv run --with` (#88).
- An edit behind a pydantic class that builds at first use (a forward reference, `defer_build`), or on Python 3.14+ to a TypedDict's or NamedTuple's field types, now changes the fingerprint, and that first use doesn't. Such a node was served stale, so the change passed unseen (#88).
- A graph file or `--client` file stays in `sys.modules` while it runs, as an import leaves it, so `from __future__ import annotations` with a dataclass, a `TypedDict` state or a pydantic model, and a parametrized pydantic generic, no longer crash lgcov. A file named like a real module doesn't hide it, and an edit to a class in that file now counts (#89).
- `lgcov bench --synthetic` scores 72 base-vs-PR cases from 24 edits on the demo agent, by case and by edit, with A/A re-runs for flip-rate baselines (#68, #81, #82).
- Per-PR records and `lgcov metrics summary` (#80).
- `lgcov round init | lock | check` lock a benchmark round's commits and cases, and an exposure ledger catches a moved SHA or a case a candidate has already seen (#86, [docs](docs/benchmark-rounds.md)).
- `lgcov bench` runs each side in its own process, with its own imports; a bad import exits 2 and names the side (#57).
- `lgcov bench --keep-runs DIR` saves each side's runs as they finish, so a crash can't lose a paid recording (#58).
- `lgcov bench` shows what each side prints, named by side, like the reason a researcher failed (#60).
- The first real PR through `lgcov bench`, open_deep_research #163, scored as a pilot: one PR, one question (#61, #62, #63, #64).
- `lgcov --help` lists lgcov's commands instead of graph-diff's usage, and an unknown command is rejected instead of running graph-diff (#53).

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
