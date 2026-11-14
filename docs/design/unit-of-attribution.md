# The unit of attribution (design, T239)

**Status: design only.** Nothing here is built. This file changes no code, no cassette field
and no label.

lgcov names the graph node that caused a behaviour change. In a deep agent the graph is a
generic loop: a `model` node, a `tools` node and a few hook nodes. Those names say nothing
about the agent. What carries meaning is the tool call, the skill and the sub-agent. This page
says what a *recorded unit* should be, so lgcov can name those.

**How it was checked.** Sources were read on 14 November 2026: lgcov at `da1c153`,
deepagents 0.7.13, langchain 1.3.18, langgraph-prebuilt 1.1.1, the MCP 2026-07-28
specification and chronicle's main branch (1.1.1 on PyPI). Nothing was run. *(not verified)*
marks a claim that rests on less than a read. The last section lists them. A function named
with no file beside it is in [`cassette.py`](../../src/lgcov/cassette.py).

## 1. What lgcov records today

- **The file.** `lgcov record` runs the graph once per input and writes the runs to
  `baseline.json` (`_record` in [`cli.py`](../../src/lgcov/cli.py), `runs_to_json`). The file
  has no schema number and holds nothing about the graph.
- **A step.** A run is a list of steps, one per node update of the top-level graph (`take` in
  `_run`). A step holds `node` and `output`, and for some nodes `input`, `code`, `goto` and
  `router` (`Step`).
- **What is served.** Only a plain-function node gets `input` and `code`, its fingerprint
  (`_plain`, `_codes`). lgcov wraps it, and serves a call when the fingerprint and the masked
  input match an unused recorded step (`begin` in `_run`).
- **Everything else runs live,** with no input and no fingerprint: a subgraph, a `ToolNode`,
  any node that isn't a plain function ([README](../../README.md#which-nodes-replay-re-runs)).
- **Inside a subgraph, nothing is recorded.** Only an error carries the inner node's name
  (`_attributed`). A subgraph's routers get no fingerprint (`_router_prints`).
- **A fingerprint** covers the function's code, its defaults, its closure and the project code
  it reaches ([`fingerprint.py`](../../src/lgcov/fingerprint.py)). It doesn't cover what a node
  reads at run time: files, environment, network. A node that uses a compiled graph gets none.
- **Tools.** `node_tools()` in [`snapshot.py`](../../src/lgcov/snapshot.py) reads which tools
  each `ToolNode` exposes. No command calls it yet, only its tests. The code judges
  ([`code_judges.py`](../../src/lgcov/code_judges.py)) read a trace that `_l2_cases` in
  `cli.py` builds: the run's top-level node names, and the tool calls its AI messages asked
  for (`run_tools` in [`messages.py`](../../src/lgcov/messages.py)). A tool check covers the
  whole run, whatever node its KPI names (`_WHOLE_RUN` in `cli.py`).

So a tool call is something a judge can ask about. It is never a step, never served, and never
root or propagated. A skill and a sub-agent don't exist for lgcov at all.

**On a deep agent.** This is read from the code of both sides, not run.

- LangChain's `create_agent` builds the graph: a `model` node, a `tools` node that is a
  `ToolNode`, and one node per middleware hook. deepagents' `create_deep_agent` returns that
  graph.
- `model` is a plain function, so it is recorded. Its fingerprint walks its closure, so the
  base system prompt, the model object and the tool list count, by their repr. What middleware
  adds to each request, such as the list of skills, doesn't.
- Every tool call runs inside `tools`. So does every skill load, which is a `read_file` call.
  So does every sub-agent run: the `task` tool invokes the sub-agent's graph inside the call
  and hands back one message with a state update.
- The result: drift there reads `tools`, "no fingerprint: a subgraph or the like (T193)". A
  prompt edit reads `model`, inconclusive, because a model call can't be probed with the
  network blocked. The comment can't name a tool, a skill or a sub-agent.

## 2. A recorded unit

**A recorded unit is one call, in a recorded run, to a piece of the agent that a PR can
change.**

| Kind | One unit is | Recorded today |
|---|---|---|
| `node` | One visit of a graph node at any depth, so an inner node of a subgraph too | Top level only |
| `tool` | One tool call that a tool node ran | No |
| `agent` | One run of a sub-agent: a graph started from inside another unit | No |

- A visit of the `tools` node stays a `node` unit. The tool calls it ran are units inside it.
- A skill is not a unit. Loading one is a tool call, and its files are parts of that call
  (section 3).
- A remote sub-agent has no inside to record. It is one `agent` unit with nothing in it.

Prior art: [Chronicle](https://github.com/theagentplane/chronicle) (MIT,
[arXiv 2609.20625](https://arxiv.org/abs/2609.20625)) records every LangGraph node and router
as a boundary and serves chosen boundaries on replay. Its README and abstract describe no
cause and no statistics.

### The fields a unit needs to be served

| Field | On | Holds |
|---|---|---|
| `kind` | All | `node`, `tool` or `agent` |
| `name` | All | The node's, the tool's or the sub-agent's name |
| `path` | All | The names of the units it ran inside, outermost first. Empty at the top level |
| `input` | All | Node: the state it was given. Tool: its arguments, and the state it was handed if it asks for state. Agent: the state it was started with |
| `output` | All | Node: its update and `goto`. Tool: its message, or its update when it returns one. Agent: what it handed back |
| `parts` | All | The names of what it depends on. Their fingerprints sit in one table on the recording (section 3) |
| `call_id` | Tool, agent | The tool call's id. For an agent, the id of the call that started it. Never compared. Fitted to this run's ids when served, as [`rebind`](../../src/lgcov/rebind.py) fits ids today |
| `status` | Tool | How the call ended. Values are proposed below |
| `samples` | All | Base's other outputs on the same input (T229, section 6) |
| `goto`, `router`, `routed`, `router_state` | Node | As today, plus the two fields of the [router design](../router-route-design.md) |

- **Why a tool's input holds state.** deepagents' file tools are handed the run's state.
  Matched on arguments alone, a `read_file` could be served an old file.
- **No field says who asked.** The unit that asked for a tool call is the earlier unit whose
  output holds that `call_id`.

**Serving** follows today's rule (`begin` in `_run`), read per unit. A unit is served when
every one of its parts matches the recording, its masked input matches an unused recorded unit
of the same kind, name and path, and the recorded ids can be fitted to this run's. Otherwise
it runs.

- An `agent` unit is served whole when nothing it holds changed: no part of it, and no part of
  anything that can run inside it. The sub-agent then never starts.
- LangGraph's `ToolNode` takes a `wrap_tool_call` wrapper. That is the seam where one tool
  call can be recorded, served or probed. Read, not run.

### Proposed: a status for transient failures (T248, not decided)

| `status` | Means | Evidence |
|---|---|---|
| `success`, `error` | What a tool message already says. An `error` is the tool failing on its own terms | Yes |
| `timeout`, `rate_limited` (429), `server_error` (5xx) | A transient failure | No |

- **Proposed rule.** A unit with a transient status is never compared, never served and never
  labelled. It reads *not checked*, and so does the rest of its run.
- **Guard.** A unit not checked for this reason makes the verdict INCONCLUSIVE, never a pass.
  Otherwise a PR could hide a regression behind tool timeouts. A node that a stopped replay
  didn't reach has this rule today ([README](../../README.md#outcomes)).
- **Why.** A transport error that escapes a node is already a harness error
  (`TRANSPORT_ERRORS`). But a tool's failure often comes back as a result: the tool catches it
  and returns an error text, or its `ToolNode` is set to turn errors into messages. Then a
  timeout is compared as output. One search timeout made a real-model run invalid
  ([A/A at K=3](../benchmark.md#aa-at-k3)).
- **Limit.** lgcov can mark only a failure it can see: an exception at the tool call, or an
  HTTP status. A tool that catches its own timeout and returns text looks like a `success`.

## 3. What a fingerprint means for each

**A part** is one thing a unit depends on that a PR can change: a function, a prompt, a tool's
description, a skill file, a sub-agent's settings. Each part has a stable name and its own
fingerprint.

**Rule: a part counts where it is used.** Code counts for the unit that runs it. What the
model is told counts for the unit that calls the model.

| What | Its parts | They count for |
|---|---|---|
| Node | Its code, as today: the function, its defaults, its closure, the project code it reaches | The node unit |
| Python tool | Its code. Its spec: name, description and argument schema | Code: the tool unit. Spec: each unit that offers the tool to a model |
| Skill | Each file in its folder, by content. The frontmatter of `SKILL.md` on its own | Frontmatter: each unit whose model call lists the skill. A file: the tool call that reads it |
| MCP tool | Its spec as the server lists it. The repo's config for that server. The server's name and version, as it reports them | Spec: each unit that offers it. Config and version: the tool unit |
| Sub-agent, declared | Each field of its spec: prompt, model, tools, middleware, skills. Its description | The agent unit. Description: each unit that offers the tool that starts it |
| Sub-agent, a compiled graph | That graph's own nodes, routers and tools, read as the parent's are | The units inside the agent unit |
| Sub-agent, remote | Its config in the repo, and what it reports of itself *(not verified)* | The agent unit |

Where the "told" parts are read from is open. One source is the objects a node holds: today's
walk already reaches a `create_agent` model node's base prompt and tool list. The other is the
request as it was sent to the model. T193 decides.

**Ruling: a part that differs but isn't from the repo is outside the PR.** A server that was
upgraded. A skill folder in the user's home. The model a provider now serves under the same
name. The unit is inconclusive, with that reason. It is never root, because the PR didn't
change it. "From the repo" means a file inside the checkout. That is stricter than the test
code gets today, where anything not installed as a library counts as project code
(`_in_project` in `fingerprint.py`).

- **An MCP server's version only ever adds that reason.** It never decides whether a unit is
  served and never names a root. The spec calls it self-reported and tells clients not to
  change behaviour on it
  ([discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)). A
  server can also change and report the same version.
- **The served model's name** is already in each recorded AI message, and nothing reads it.
  `encode` in `messages.py` keeps the response metadata. *(not verified per provider)*

**Serving an MCP tool.** A served tool unit returns the whole recorded response. lgcov never
re-sends a recorded request to a live server.

- One tool call can take several round trips: the server asks for input and hands the client a
  `requestState` to send back. The spec says a server should put a short expiry inside that
  state and reject it once lapsed
  ([multi round-trip requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)).
  A recorded one is dead. So the unit is the whole call, from the first request to the final
  result. It is served whole or run whole.
- The spec's
  [caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching)
  rules don't cover `tools/call`. Serving one is lgcov's own bet, the one it makes today for a
  node that reads the network: same parts and same input give the recorded output. `--live`
  drops the bet.
- **Limit.** A handle that a served call returned is unknown to a live server, so a later live
  call that passes it on fails. lgcov can't tell a handle from any other string.

## 4. Root, propagated, and which change caused it

The labels keep their meaning (`classify` and `explain_runs` in
[`attribution.py`](../../src/lgcov/attribution.py)). They are read per unit.

| Label | Today, per node | Per unit |
|---|---|---|
| Root | Its fingerprint changed, and it drifts on base's recorded input | A part of it changed, and it drifts on base's recorded input |
| Propagated | It drifts only because its input did | The same |
| Inconclusive | No fingerprint, a probe that couldn't run, a graph that isn't known, or drift nothing explains | The same, and one more: only an outside part changed |

- **A tool is probed as a node is.** Head's tool runs on base's recorded arguments, with the
  network blocked, in the job that runs the PR's code (`probe_steps`, `_offline`). A tool that
  reaches for the network can't be probed that way.
- **Nor can a model call.** So an edit to a prompt, a skill's description or a tool's
  description reads inconclusive in the gate. Much of what a PR edits in a deep agent is of
  that kind *(not measured)*, so this is the main limit. It holds until base's `samples`
  (T229) let the job that decides weigh head's repeats against base's own spread
  (`noise_check` in `attribution.py`).
- **Ruling: a container gets no label when a unit inside it explains its drift.** A container
  is a sub-agent run, a visit of the `tools` node or a subgraph node. The inner unit is named
  instead. The subgraph benchmark already expects this: root `sub.inner`, propagated `after`,
  nothing on `sub` ([`subgraph.yaml`](../../tests/fixtures/bench/subgraph.yaml)). A container
  whose drift nothing inside explains is inconclusive, as today.

### From a root to the change in the PR

The stated pain is "which change caused it". A unit's name is half the answer. Its parts are
the other half.

1. **One fingerprint per part.** Today a node has one hash for everything it reaches. So lgcov
   knows that a node changed, and not what changed in it.
2. **A stable name per part.** A function or a constant by its qualified name. A skill file by
   skill and file. An MCP tool by server and tool. A sub-agent's field by agent and field. The
   part's file is kept beside the name, for the comment. Paths and line numbers stay out of
   the fingerprint, as today.
3. **A root's cause is its changed parts.** The comment names them with the root.
4. **A shared helper is one part.** It shows once, behind every root it reaches.
5. **An inconclusive unit still lists the changed parts it saw.** For a prompt edit that is
   most of the answer.
6. **Several changed parts in one root are all named.** Telling them apart needs a run per
   part, with the others put back. Not designed here.
7. **Proposed: check each changed part against the PR's diff.** The job that decides can list
   the files the diff touches without trusting the PR. A changed part whose file isn't among
   them may not be the PR's doing. It has three readings: an outside change; a stale base
   recording, where base moved and nobody re-recorded; or a value built from a file the PR
   did touch. Telling a stale recording from an outside change is the stale-baseline question
   (T243). The check stays open.

**Trust.** Head's fingerprints and part names come from the job that runs the PR's code. They
are that code's word, as steps and probes are today ([trust boundary](../trust-boundary.md),
limit 1). Base's come from the recording in the base checkout. T238's rule holds: nothing the
PR writes may turn a blocking label into one that doesn't block.

**A check this makes cheap.** Serve base's recorded output at a root, run again, and see
whether the drift after it goes away. It needs nothing that serving doesn't. Not designed here.

## 5. LangGraph-only, or a tool-call boundary?

**Decision: the unit is defined so that it could sit at a tool-call boundary outside
LangGraph. The recorder stays LangGraph-only in this schema change.**

Could it sit there? Yes.

- No field in section 2 needs a graph. A `tool` unit is a name, arguments, a result and parts.
- An MCP `tools/call` has that shape: one request, one response. Section 3 has its serving
  rule.
- The Claude Agent SDK's [hooks](https://code.claude.com/docs/en/agent-sdk/hooks) hand a
  recorder each tool call's name, input, output and id, and the sub-agent it ran in.

Why not now:

1. **Deep agents are inside LangGraph.** The deepagents
   [README](https://github.com/langchain-ai/deepagents/blob/main/README.md) says it is built
   on LangGraph, as a harness on top of `create_agent`, and that any compiled LangGraph graph
   can be a sub-agent. The recorder T193 needs reaches them without leaving LangGraph.
2. **Serving doesn't carry over.** It needs code that can stand in for a call. In LangGraph
   lgcov wraps the node, and the agent's code doesn't change. Chronicle serves outside
   LangGraph, but only at boundaries the developer marks in the agent's code. The Agent SDK's
   hooks can't serve: one can replace a tool's output only after the tool ran, and none can
   answer a model call. Every replay would pay for every call, and the job that runs the PR's
   code would need a model key ([trust boundary](../trust-boundary.md), limit 4).
3. **Propagated doesn't carry over.** It needs each unit's input. A tool-call boundary has it
   for tools. The step that matters most, the model choosing the next call, has its input only
   where the model call is recorded too. In LangGraph that is a node's state.
4. **Scope.** [why-us](../why-us.md#what-lgcov-does-not-do-yet) lists agents not built on
   LangGraph under what lgcov doesn't do. This page doesn't reopen that. It only stops the
   schema from closing the door.

What stays LangGraph-only: `node` units and their paths; routers and routes (T175, T221);
`goto` for a `Command` or a `Send` (T168); the graph's node names (T238); probes that call a
node function; and serving by wrapping a node or a `ToolNode`.

## 6. What the single schema change must carry

Subgraphs (T193), routes (T221) and the graph's node names (T238) share one cassette schema
change, with one re-record ([router design](../router-route-design.md)). Base's noise samples
(T229) join it, and so does T239. This is a list of fields. The migration is not designed
here.

**On the recording**

| Field | For | Holds |
|---|---|---|
| `schema` | All | The recording's schema number. There is none today |
| `scheme` | T212 | Which fingerprint scheme the recording uses, so the old one can go (`_Raw` in `fingerprint.py`) |
| `graph` | T238 | The node names of the graph that was recorded. Telling a stale list from a current one is open (T243) |
| `parts` | T239 | Each part's name, its fingerprint and, for the comment, its file |

**On a unit**

| Field | For | Holds |
|---|---|---|
| `kind` | T239 | `node`, `tool` or `agent`. Absent means `node`, so an old step reads as it does now |
| `path` | T193, T239 | The units it ran inside. T193 needs it for an inner node. A dotted name can't say what kind each level is |
| `parts` | T239 | Names into the recording's table. `code` stays while old recordings are read |
| `call_id` | T239 | For a tool call and an agent run |
| `status` | T239, T248 | The transient values are proposed, not decided (section 2) |
| `samples` | T229 | Base's other outputs on this unit's input, taken when it was recorded |
| `routed`, `router_state` | T221 | As the router design has them |
| `router`, for inner nodes | T193 | A subgraph's routers get fingerprints too |

- **Why `samples` sit in the recording.** The job that decides can then apply the noise rule
  without running base's code. It reads them from the base checkout, which the PR can't write.
  Base's node names go there for the same reason.
- **The candidate file changes too.** It must carry head's units and head's parts. It has a
  schema number (`SCHEMA` in [`candidate.py`](../../src/lgcov/candidate.py)) and needs the
  read-first, write-later rollout T162 used.

**What chronicle's record has that this unit lacks.** Read from its
[envelope schema](https://github.com/theagentplane/primitives/blob/main/src/agentplane_primitives/schemas/Envelope.json).

| Chronicle has | Here |
|---|---|
| The model call as its own kind, with the request as sent: `system`, `messages`, `tools`, `params` | Not taken. A model call stays inside its node. Open for T193: the request as sent is one source for the "told" parts |
| `output.model`, and whether that is the model served or the one requested | Taken, as an outside part (section 3) |
| `attempt`, and a `retry_of` link | Not taken. lgcov's replay log counts the attempts that raised (`Call`). To revisit with T248 |
| `output.error`, with a type and a message | Covered by `status`, if T248 accepts its values |
| `started_at`, `ended_at`, `usage`, `provider` | Not taken: cost and timing, not cause |
| `trace_id`, `span_id`, `parent_envelope_id` | Not taken: lgcov records one process, and `path` names the parent |
| `state`: open or closed | Not taken: a crash is already a `NodeError` |

What this unit has that chronicle's record doesn't: a fingerprint of what each unit depends
on, matching by input (chronicle matches by name and call count), and the `agent` kind.

## Not verified

- **T229, T243 and T248 have no text in this repo yet.** This page states each as its task
  was given.
- **T193 has no design of its own.** Its fields here are what this page needs from it.
- **Nothing was run on a deep agent.** That its `model` node gets a fingerprint, and what that
  fingerprint covers, is read from lgcov's and LangChain's code.
- **How a recorder sees inside a tool call,** where a sub-agent runs. Not tried.
- **How a recorder ties a file read to a file,** so a skill file becomes a part of the call
  that read it. Not designed.
- **How a transient failure shows at an MCP boundary.** An MCP tool reports an error inside
  its result.
- **The served model's name, per provider.**
- **Remote sub-agents.** deepagents has them. Their code was not read.
- **An MCP server whose code is in the repo.** Its files should count as parts. Not designed.
- **Chronicle** beyond its README, its LangGraph instrumenter and its envelope schema. Its
  paper was read as an abstract only.
- **The Claude Agent SDK** beyond its hooks page. How a skill load shows up in a hook was not
  read.
