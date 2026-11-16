# The cassette schema (design, T193)

**Status:** PR 1 in section 6, the reader, is built (#158, #159) and shipped in lgcov 0.4.0,
release R1. It reads every field on this page, and no label uses one yet. The writers are off:
lgcov still writes recording 1 and candidate 2. The rest of the page is design only.

lgcov records a baseline once and replays each PR against it. Several planned tasks need the
recording to hold more than it does. This page is the one schema change that carries them all, so
a user re-records once to move to it. It starts from the field list in
[the unit of attribution](unit-of-attribution.md) (T239).

| Task | What it needs in the recording |
|---|---|
| T193 | Units inside a subgraph, inside a tool node, and inside a graph started from a node or a tool |
| T221 | The targets a router returned and the state it saw ([router design](../router-route-design.md)) |
| T229 | Base's noise samples, so the job that decides needn't run base's code |
| T238 | Base's node names, read from the base checkout |
| T212 | The fingerprint scheme, so the old one can go |
| T239, T248 | A unit's kind, parts and call id; a tool call's status (T248's values are pending) |

**How it was checked.** Read on 15 November 2026 at lgcov `0173e8a`. Run: the checks in section 5,
offline, with fake nodes, tools and chat model, on langgraph 1.2.16, langgraph-prebuilt 1.1.1 and
langchain-core 1.6.13, the versions the Action's `uv.lock` pins. Not run: deepagents, langchain's
`create_agent`, MCP servers, any real model. *(not verified)* marks a claim that rests on less than
a read. A function named with no file is in [`cassette.py`](../../src/lgcov/cassette.py).

## 1. Today's files

| | Today |
|---|---|
| The recording (`baseline.json`) | `{"runs": [{"inputs", "steps"}]}`, with no schema number. Only `runs_to_json` writes it, and only `runs_from_json` reads it |
| A step | `node`, `output`, `input`, `code`, `goto` and `router` (`Step`). Order carries meaning: `accept_nodes` pairs steps by occurrence, and a run's first drift comes from it (`_run_root` in [`cli.py`](../../src/lgcov/cli.py)) |
| An older lgcov on a newer file | It ignores an unknown key at the top or on a run, and refuses one on a step: `Step(**s)` raises |
| The candidate file | Schema 2. The job that decides refuses anything but exactly that shape ([`candidate.py`](../../src/lgcov/candidate.py)) |
| What the job that decides reads | The PR's `baseline.json` (`_evaluate` in `cli.py`, run in the PR's checkout). Base's copy only for the edited-cassette check (`_cassettes_edited`) |
| `cassette accept` | Writes back each run's `inputs` and `steps`, nothing else |
| Secrets | Nothing is redacted. Inputs and outputs are written as they ran; masking only affects matching and comparison |

## 2. The fields

### On the recording

| Field | Type | Written by | Read by | Task |
|---|---|---|---|---|
| `schema` | Integer, `2`. Absent means 1, today's file | `lgcov record`. `cassette accept` keeps the file's number | Every reader (`runs_from_json`). A number it doesn't know is refused by name | All |
| `scheme` | Text: the fingerprint scheme | `lgcov record` | `_codes` and `_router_prints`, which then compute that scheme only | T212 |
| `graph` | Object: node names, nested for subgraphs; each tool node's tool names; one fingerprint per state channel | `lgcov record`, from the compiled graph | The job that decides, from the base checkout's recording. One-job `ci` the same way | T238, T193 |
| `parts` | Object: part name to `fingerprint`, `file` and `source` | `lgcov record` | The candidate job (what changed), the job that decides (a root's cause, the outside-part rule), the comment | T239. `source` is for T243, if ruling 4 is yes |
| `runs` | As today | | | |

`source` is a hash of the part's own syntax tree in its file, read as T212 reads source. A job that
runs no project code can recompute it from a checkout.

### On a unit

A unit is today's step. The recorder writes every field: `lgcov record` for the recording, the
candidate job for the PR's units.

| Field | Type | Holds | Read by | Task |
|---|---|---|---|---|
| `node` | Text | The unit's name: a node, a tool or a sub-agent. Kept, not renamed to `name`, so every recorded line and reader stays | Everything that reads a step today | All |
| `kind` | `node`, `tool` or `agent` | Absent means `node`, so an old step reads as it does now | The serving key, labels, the comment | T239 |
| `units` | List of units | The units that ran inside this one, in the order LangGraph started them | Serving, the container rule, the comment | T193 |
| `input` | As today | A tool: its arguments as the model sent them, plus the state if it asks for state | The serving key (masked), the propagated check, probes | T193 |
| `output` | As today | A tool: its message, or its update if it returns a `Command` | Comparison, serving | T193 |
| `code` | Text or null | As today, in the recording's `scheme` | Serving, `changed_nodes`, labels | T212 |
| `goto` | As today | | | T168 |
| `router` | As today | Now on inner nodes too | T175's rule | T193 |
| `routed` | List | The targets the router returned | T221's rule | T221 |
| `router_state` | Any | The state the router saw, masked like an input. Capped, and can be switched off | Head's router probe | T221 |
| `parts` | List of text | Names in the recording's `parts` | Serving, labels, the comment | T239 |
| `call_id` | Text | A tool: the call's id. An agent: the id of the call that started it. Never compared; fitted when served (`rebind`) | `rebind` | T239 |
| `status` | Text | A tool: `success` or `error`, as its message says, and T248's three if accepted | The not-checked rule | T239, T248 |
| `samples` | List of `{output, goto}` | Base's code run again on the same input when recorded | The job that decides, from the base checkout, in `noise_check` ([`attribution.py`](../../src/lgcov/attribution.py)) | T229 |

**Worked out when read, never written:** a unit's path (the names of the units around it), its call
index (the n-th unit of its kind, name and path in the run) and its order. So a file can't
contradict itself.

### In the candidate file, schema 3

- A run's steps are units of the same shape, with `units`.
- A probe names its unit by its position down the tree, not by a step index. A router probe carries
  `routed` (T221).
- Head's `parts`, as fingerprints. They are the PR's code's word, as steps are today
  ([trust boundary](../trust-boundary.md), limit 1).
- `repeats`: head's live repeats of a unit, for the noise rule (T229). Opt in: they need a model key
  in the candidate job, which is the user's call (limit 4).

It needs no re-record. It still needs read first, write later (section 4).

### Which recording each field comes from

**Rule: a field that can turn a blocking label into a non-blocking one comes from the base
checkout's recording, never the PR's.** That is `graph`, where a node list that already holds an
added node makes it propagated, and `samples`, where a wide spread hides drift. Today the job that
decides reads only the PR's recording, so this is new plumbing. The other fields come from the PR's
recording as today, and an edit to it is flagged as before ([trust boundary](../trust-boundary.md),
limit 3).

### What no fingerprint sees (T182)

T182 found gaps that a schema change can name but not always close.

- **State schema: defaults, reducers, validators.** They are in no node's fingerprint, since a node
  names its state class only in annotations. Serving stays right: a changed reducer changed the next
  node's input, so that node ran live and wasn't served stale (check 7). Naming the cause doesn't:
  the drift read "drifted with no changed node before it". `graph` gets one fingerprint per channel,
  so the comment can say "the state schema changed (channel `x`)". It makes no unit root by itself.
- **A model name in a client object or an environment variable.** The fingerprint sees a
  module-level client only through its class and repr, so it depends on the class (check 6). A plain
  class's repr shows only its type, and a change to its model changed no fingerprint: T182 builds
  this case. A LangChain chat model's repr left out a model name equal to its default and showed it
  otherwise. It also held langchain-core's version, so an upgrade would change it *(inferred from
  the repr)*. A name read from an environment variable inside the node changed nothing. Schema 2
  adds two things. The request as sent names the model whenever the unit runs live (question 3).
  And, proposed, a chat model object's model and sampling fields become parts of their own. A value
  that changed while its `source` didn't came from outside the code: an outside part, inconclusive,
  never root. A served unit never calls the model, so a change the fingerprint can't see is still
  served stale. That stays a gap, with `--live` as the way round it.
- **A config class counts whole.** An edit to one default reaches every node that names the class,
  even nodes that don't read that field *(T182's reading, not run here)*. Parts don't change that.
- **An input that isn't JSON gets no key** (`_key` returns None). That node keeps no input or
  code, so it is never served, and its drift reads "no fingerprint: a subgraph or the like
  (T193)", which names the wrong cause. At `0173e8a` a pydantic state was such an input (check 7).
  Since #156 a pydantic state is keyed by its fields, as a TypedDict state is, and the probe
  rebuilds the class. An input that holds another pydantic object, such as a `Document`, still
  gets no key. That was run with #156's code, in a TypedDict state and in a pydantic one.

## 3. Six questions

1. **Does a unit carry its order and its call index, so a divergence reads "unit X, call n"?** Yes,
   by position. Units sit in the order LangGraph started them, and a tool call sits at its place in
   the AI message. The call index is worked out when read. Start order is the stable one: a Send
   fan-out of 8 started its tasks in the same order in six runs, while updates arrived in a
   different order each time (check 2c). Today's steps follow update order. Chronicle numbers calls
   by count, and its README warns that parallel calls can start in a different order and shift the
   number ([chronicle](https://github.com/theagentplane/chronicle)).
2. **Is there an explicit outcome when head makes a call base never made?** lgcov's `diverged`
   (T089) is something else: the first call whose code matched the recording but whose input didn't.
   It runs live, and with `on-divergence: fail` the run stops there and reads INCONCLUSIVE. A call
   base never made is no error, since lgcov serves by input, not by count. It runs live and is
   compared. Its label is T238's rule, read per unit (`classify` in `attribution.py`). It is root
   when the other side's graph or tool list lacks it. It is propagated when the list has it and
   something before it explains the run. It is inconclusive when the list isn't known, and under
   `fail-on: root-drift` an inconclusive unit blocks with the roots.
3. **Are a model call's request settings recorded as parts?** Yes, as parts of the unit that calls
   the model, from the request as sent. A LangChain callback saw each call's model name, temperature
   and the tools offered, with the node and its namespace (check 6). They exist only after a live
   call, so they name a change and never decide serving. The model a provider served is an outside
   part (T239).
4. **What is redacted before tool inputs and outputs are written?** Nothing today, and tool results
   already reach the recording inside message state. Proposed: redaction at write time on every
   stored value, with the same rules on the live value before matching, so a redacted input still
   matches. Default rules cover key shapes, and a setting covers the rest. A served value is then
   the marker, so a node that uses a secret from state drifts: secrets don't belong in state.
   Chronicle redacts before sending, if you opt in, and calls pattern matching a baseline, not a
   guarantee. Shadow redacts by default, and `shadow scan` checks committed traces
   ([shadow](https://github.com/manav8498/Shadow)). It lands before tool units (section 6).
5. **Is a tool marked safe to re-run offline?** A probe runs a changed unit on base's recorded input
   (T162). For a node, that is its function with sockets refused (`_offline`). A tool unit is probed
   only when `lgcov.yaml` lists it as safe to re-run, and none is by default. The gate reads
   `lgcov.yaml` from the base, so a PR can't mark its own tool. An unlisted changed tool reads
   inconclusive, "not marked safe to re-run". An MCP tool is never re-run (T239). Node probes keep
   T162's rule, which blocks only the network. Giving them the same mark is a separate question.
6. **When several parts of one unit changed, how are they told apart?** Not built in this change.
   The plan is a part probe in the candidate job, which runs the unit once per changed part with
   only that part at head's value. Code parts run offline. Model parts need live calls, `K` repeats
   and base's `samples`. The candidate file carries one record per unit and part, and the job that
   decides names the part whose run alone drifts. Until then, every changed part is named together
   (T239). How to put one code part into head's process is open. Shadow names the config change most
   likely behind each regression with `shadow bisect`, which its README calls LASSO-based.

**Smaller points.**
- **Streaming.** lgcov drives the graph itself, with `.stream` or `.astream` and
  `stream_mode="updates"`. Schema 2 adds `subgraphs=True` and the `tasks` mode. What a node streams
  token by token isn't recorded, only what it returned. Chronicle leaves streaming out too.
- **LangGraph internals:** section 5.
- **Forked PRs.** The Action tries the comment and falls back to the job summary
  ([`action.yml`](../../action.yml)). Nothing here changes that.
- **Repeats.** `K` is fixed (`--noise-repeats`). Stopping early once the answer is clear isn't
  designed.

## 4. Read first, write later

**The #116 lesson.** In this repo the job that decides runs base's lgcov. #116's candidate job wrote
a `router` key on each step. Base's `Step()` refused it, so the job that decides couldn't read the
candidate file and exited 2. #118, which only read the key, merged first, then #116
([`test_cassette.py`](../../tests/test_cassette.py) keeps the read-first test). Outside users run
one Action ref in both jobs. They meet the same break when the lgcov they record with is newer than
the ref CI pins.

| Release | Reads | Writes |
|---|---|---|
| 0.3.0 and older | Recording 1, candidate 2 | The same. On a schema-2 recording it ignores the new top-level fields and exits 2 on a step with a new key |
| R1, 0.4.0 | Recording 1 and 2, candidate 2 and 3, with every field on this page | Recording 1, candidate 2 |
| R2 | The same | Recording 2 with `graph` and subgraph units, candidate 3 |
| Later | The same | Tool and agent units, routes, samples and parts, as optional keys under the same numbers |

- R1 ships before R2. R2's notes say: re-record once, and every job and the lgcov you record with
  must be at R1 or later.
- A file without a later field keeps today's labels. Re-record to get it, as the changelog says of
  router fingerprints.
- R1 refuses a number it doesn't know by name ("schema 3; this lgcov reads 1 and 2"), not with a
  TypeError.
- In this repo, the PR that reads merges before any PR that writes.
- `cassette accept` keeps the file's schema and recording fields, and never mixes schemes in a
  schema-2 file.
- `_Raw` and schema-1 reading stay until a later, announced release.
- `action.yml` passes lgcov no new flag. An older lgcov exits 2 on a flag it doesn't know, and a new
  step in this repo runs from the base. `--samples` is a `lgcov record` option.

## 5. Inside a tool node and a subgraph

### Verified by running

| # | Check | Result |
|---|---|---|
| 1 | Today's `_run` on a graph with a subgraph node and a `ToolNode` | Top-level steps only. `sub` and `tools` have no input and no code |
| 2 | `stream(subgraphs=True, stream_mode=["updates", "tasks"])` on that graph | Each inner node visit arrives with its input and result, under `("sub:<task id>",)`. That id is the `sub` task's own, so a visit ties to its container. Tool calls are not tasks |
| 2c | A Send fan-out of 8, six runs | Task start order was the same each time; update order was different each time |
| 3 | Serve an inner node: copy the subgraph's node and wrap its function, as `_attributed` copies them | The function didn't run, the next inner node ran on the served output, and the original graph was unchanged |
| 4 | A graph started by a bare `.invoke()` inside a node, and inside a tool | Its node visits arrive, with inputs, under the calling node's namespace (`supervisor_tools:<id>`, `tools:<id>`), not a name of their own |
| 5a | `ToolNode(wrap_tool_call=...)` | The wrapper sees each call's id, name and arguments, the state and the namespace. Returning a recorded `ToolMessage` skips the tool |
| 5b | The same wrapper, set on a copy of a compiled graph's `ToolNode` | It works only after rebinding the copy's `func` and `afunc`, which still point at the original. The original stays untouched |
| 5c | Two tool calls in one AI message | They ran in parallel, on two threads |
| 5d | A tool that raises | With default error handling the run raised. With `handle_tool_errors=True` the call became a message with status `error`, and the run finished |
| 5e | Serving a tool that starts a graph | Neither the tool nor the graph ran |
| 5f | A tool that asks for state | Its arguments in the wrapper are the model's, without the state, which is on the request |
| 6 | A fake LangChain chat model and a plain-class client in a node, with a callback | See question 3 and section 2 |
| 7 | A reducer edit (TypedDict state) and a default edit (pydantic state) | See section 2. No fingerprint changed in either |

Task ids differed between runs, so a unit's address leaves them out.

### Read, not run

- LangGraph 1.2.16 has no public hook that wraps every node at any depth. `CachePolicy` can skip a
  node by serving cached writes. But it is set per compiled graph when the graph is built, keys on
  LangGraph's own hash of the input, and stores channel writes, not what the node returned.
- `wrap_tool_call` is a `ToolNode` constructor argument, kept in a private attribute, with no
  setter.
- deepagents and langchain's `create_agent` run tool calls, skills and sub-agents inside one
  `ToolNode`, as T239 read. Checks 4 and 5 build that shape with plain LangGraph.

### The mechanism

1. **Record** every node visit at any depth from `subgraphs=True` and the `tasks` mode, with input
   and result (checks 2 and 4). This uses public API.
2. **Serve an inner node** by copying the subgraph's node and wrapping it (check 3).
3. **Serve a tool call whole** with `wrap_tool_call` on a copy of the `ToolNode` (checks 5a, 5b and
   5e). An MCP tool is served the same way and never re-sent (T239).
4. **A graph started inside a node or a tool** is recorded (check 4) and served only whole, through
   the unit that started it (check 5e). Its parts join that unit's, where the fingerprint gives up
   today (`_describe` in [`fingerprint.py`](../../src/lgcov/fingerprint.py) raises on a compiled
   graph). Its namespace names the caller, so what to call its `agent` unit is open.
5. **Address** a unit by kind, name and path, with task ids left out, and order it by start.

**LangGraph internals.** Today the recorder imports only public names, but it reads and replaces
attributes of private classes: `PregelNode.bound` and `.copy`, `RunnableCallable.func` and `afunc`,
and `BranchSpec.path` and `.ends`. Chronicle instruments the graph builder before `compile` instead
(`chronicle.instrument(graph)`, per its README). Recording can move to public API. Serving can't:
steps 2 and 3 keep internals, including `ToolNode`'s private `_wrap_tool_call`. A LangGraph upgrade
can break them, and `pyproject.toml` sets no upper bound.

## 6. PR split

In merge order. Each is safe alone: from the first on, base's lgcov reads every field, and every
field after the flip is optional.

1. **Read** recording 2 and candidate 3, every field on this page, and write nothing. Split it into
   recording and candidate if it runs over about 200 lines. Then release R1.
2. **Recording fields** `schema`, `scheme` and `graph`, behind a writer switch, so `lgcov record`
   still writes schema 1. The job that decides reads `graph` from the base checkout.
3. **Subgraph units:** record and serve them, then the labels. This PR flips T182's pin (#155),
   `test_ci_names_no_root_inside_a_subgraph_and_t193_should_change_that` in
   [`test_cli.py`](../../tests/test_cli.py). On its nested build there is no root today, and
   `answer` is inconclusive. After this PR `answer.respond` is root, and `answer` gets no label
   (the container rule).
4. **Flip:** `lgcov record` writes schema 2. The demo baselines and the fixtures tests compare byte
   for byte are re-recorded once ([`test_demo_agent.py`](../../tests/test_demo_agent.py) requires
   it), with docs and changelog. Then release R2.
5. **Redaction** at write time.
6. **Tool units**, after 5.
7. **Units of a graph started inside a node or a tool**, served only whole.
8. **Routes** (T221): `routed`, `router_state` and the router probe.
9. **Samples** (T229): `lgcov record --samples K`.
10. **Parts** (T239), with a chat model's fields.

Each of 5 to 10 ships when ready, with no new schema number. It re-records the demo baseline only if
the demo graph gains that field. A PR that changes a label also updates the docs that state lgcov's
limits.

## 7. Open rulings

1. **Do transient errors never count as evidence?** *Recommendation: yes.* A unit with a transient
   status (`timeout`, `rate_limited`, `server_error`) is not checked, and neither is the rest of its
   run. That makes the verdict INCONCLUSIVE, never a pass, so a PR can't hide a regression behind
   timeouts. This covers a failure a tool node handled, so the run went on (check 5d). One that ends
   the run is judged as today: a timeout or connection error is INFRA_ERROR, exit 2, and anything
   else a node raises is an agent failure ([README](../../README.md#outcomes)). A tool that catches
   its own timeout and returns text can't be told apart.
2. **Confirm a root by serving base's output there and running again?** *Recommendation: only when
   several units changed; later for the rest.* With one changed unit, the units after it see base's
   inputs and, where they can be served, are served from base's recording. So the run equals base by
   construction. With several, serving base's output at one while another runs live tells them
   apart. Where units after the root run live, it needs `K` repeats and base's `samples`. Causal
   Agent Replay works this way: it changes one step, runs the rest forward again, and reports each
   effect with a confidence interval. Its abstract names a confound that running forward brings
   ([arXiv 2606.08275](https://arxiv.org/abs/2606.08275); tested on synthetic models only). No new
   field: serving a recorded output exists.
3. **Report an A/A rate, and an explicit low-confidence outcome?** *Recommendation: the outcome now,
   the rate later.* A root that rests on one live sample of a model unit, with no `samples` to weigh
   it against, says so in the comment. It still blocks as a root does: the words only stop it
   reading as proof, as `write_research_brief` was a root by construction on a real PR ([A/A noise
   floor](../benchmark.md#aa-noise-floor)). No new field. The rate needs many paid runs.
4. **A stale baseline (T243)?** *Recommendation: block, with the re-record command, once detection
   reads only the base checkout.* The job that decides recomputes each part's `source` from the base
   checkout and compares it with the recording. Exempt a PR that changes only the recording, or the
   PR that fixes a stale one can't merge. That PR still goes through the edited-cassette check: its
   headline is "No decision: needs_human", never SHIP (`cassettes_edited`). So the exemption can't
   be used to swap in a convenient baseline. Until detection exists, T238's "not known:
   inconclusive" stays.

## Not verified

- deepagents, langchain's `create_agent` and MCP servers: read through T239, not run.
- A real provider's client: only a fake LangChain model and a plain class were run.
- The redaction rules, the part probe, `graph.state` and `source`: designs, not tried.
- How much a recording grows with `units`, `samples` and `router_state`: not measured.
- Chronicle and Shadow beyond their READMEs, and Causal Agent Replay beyond its abstract.
