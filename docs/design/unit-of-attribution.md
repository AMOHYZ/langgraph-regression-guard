# The unit of attribution (design, T239)

**Status: chat-model parts are recorded and serving reads them; labels don't yet.** `lgcov record`
writes a chat model's fields as `parts`. Replay serves a step only when those fingerprints still
match. Nothing else on this page is built.

lgcov names the graph node that caused a behaviour change. In a deep agent the graph is a
generic loop: a `model` node, a `tools` node and a few hook nodes. Those names say nothing
about the agent. What carries meaning is the tool call, the skill and the sub-agent. This page
says what a *recorded unit* should be, so lgcov can name those.

**How it was checked.** Read on 14 November 2026, not run: lgcov at `da1c153`, deepagents
0.7.13, langchain 1.3.18, langgraph-prebuilt 1.1.1, the MCP 2026-07-28 specification and
chronicle's main branch. *(not verified)* marks a claim that rests on less than a read. A
function named with no file is in [`cassette.py`](../../src/lgcov/cassette.py).

## 1. What lgcov records today

- **The file.** `lgcov record` writes one run per input to `baseline.json` (`_record` in
  [`cli.py`](../../src/lgcov/cli.py), `runs_to_json`). It has no schema number and holds
  nothing about the graph.
- **A step.** A run is a list of steps, one per node update of the top-level graph (`take` in
  `_run`). Only a plain-function node gets `input` and `code`, its fingerprint (`Step`,
  `_plain`, `_codes`). lgcov serves its call when the fingerprint and the masked input match
  an unused recorded step (`begin` in `_run`).
- **Everything else runs live,** with no input and no fingerprint: a subgraph, a `ToolNode`,
  any node that isn't a plain function ([README](../../README.md#which-nodes-replay-re-runs)).
- **Inside a subgraph, nothing is recorded.** Only an error carries the inner node's name
  (`_attributed`), and a subgraph's routers get no fingerprint (`_router_prints`).
- **Tools.** `node_tools()` in [`snapshot.py`](../../src/lgcov/snapshot.py) reads which tools
  each `ToolNode` exposes. No command calls it, only its tests. The code judges
  ([`code_judges.py`](../../src/lgcov/code_judges.py)) read a trace built by `_l2_cases` in
  `cli.py`: the run's top-level node names, and the tool calls its AI messages asked for
  (`run_tools` in [`messages.py`](../../src/lgcov/messages.py)), over the whole run.

So a tool call is something a judge can ask about. It is never a step, never served, and never
root or propagated. A skill and a sub-agent don't exist for lgcov at all.

**On a deep agent** (read from the code of both sides, not run), every tool call, skill load
and sub-agent run happens inside one `ToolNode`, named `tools`. Drift there reads `tools`, "no
fingerprint: a subgraph or the like (T193)". A prompt edit reads `model`, inconclusive. The
comment can't name a tool, a skill or a sub-agent.

## 2. A recorded unit

**A recorded unit is one call, in a recorded run, to a piece of the agent that a PR can
change.**

| Kind | One unit is | Recorded today |
|---|---|---|
| `node` | One visit of a graph node at any depth, so an inner node of a subgraph too. A visit of `tools` is one, and the tool calls it ran are units inside it | Top level only |
| `tool` | One tool call that a tool node ran | No |
| `agent` | One run of a sub-agent: a graph started from inside another unit | No |

A skill is not a unit: loading one is a tool call, and its files are parts of that call
(section 3).

Prior art: [Chronicle](https://github.com/theagentplane/chronicle) (MIT,
[arXiv 2609.20625](https://arxiv.org/abs/2609.20625)) records every LangGraph node and router
as a boundary and serves chosen boundaries on replay. By its README and abstract, it names no
cause and uses no statistics.

**The fields a unit needs to be served:**

| Field | On | Holds |
|---|---|---|
| `kind` | All | `node`, `tool` or `agent`. Absent means `node`, so an old step reads as it does now |
| `name` | All | The node's, the tool's or the sub-agent's name. Today's `node` |
| `path` | All | The names of the units it ran inside, outermost first. A dotted name can't say what kind each level is |
| `input` | All | Node: the state it was given. Tool: its arguments, and the state it was handed if it asks for state. deepagents' file tools do, and matched on arguments alone a `read_file` could be served an old file. Agent: the state it was started with |
| `output` | All | Node: its update and `goto`. Tool: its message, or its update when it returns one. Agent: what it handed back |
| `parts` | All | The names of what it depends on. Their fingerprints sit in one table on the recording (section 3) |
| `call_id` | Tool, agent | The tool call's id. For an agent, the id of the call that started it. Never compared. Fitted to this run's ids when served, as [`rebind`](../../src/lgcov/rebind.py) fits ids today |
| `status` | Tool | How the call ended. Values are proposed below |
| `samples` | All | Base's other outputs on the same input, taken when it was recorded (section 6) |
| `goto`, `router`, `routed`, `router_state` | Node | As today, plus the two fields of the [router design](../router-route-design.md) |

**Serving** follows today's rule (`begin` in `_run`), read per unit. A unit is served when
every one of its parts matches the recording, its masked input matches an unused recorded unit
of the same kind, name and path, and the recorded ids can be fitted to this run's. Otherwise
it runs. An `agent` unit is served whole when none of its parts changed, nor any part of what
can run inside it. The sub-agent then never starts.

**T248: a status for transient failures.** `status` is `success` or `error`, as a tool message
says today, or one of `timeout`, `rate_limited` (429) and `server_error` (5xx).

- **Rule.** A unit with one of the last three is never compared, never served and never
  labelled. It reads *not checked*, and so does the rest of its run. One search timeout made
  a real-model run invalid ([A/A at K=3](../benchmark.md#aa-at-k3)).
- **Guard.** A unit not checked for this reason makes the verdict INCONCLUSIVE, never a pass.
  Otherwise a PR could hide a regression behind tool timeouts. A node that a stopped replay
  didn't reach has this rule today ([README](../../README.md#outcomes)).

## 3. What a fingerprint means for each

**A part** is one thing a unit depends on that a PR can change: a function, a prompt, a tool's
description, a skill file, a sub-agent's settings. Each part has a stable name and its own
fingerprint. **A part counts where it is used:** code for the unit that runs it, and what the
model is told for the unit that calls the model.

| What | Its parts | They count for |
|---|---|---|
| Node | Its code, as today: the function, its defaults, its closure and the project code it reaches ([`fingerprint.py`](../../src/lgcov/fingerprint.py)). Not what it reads at run time | The node unit |
| Python tool | Its code. Its spec: name, description and argument schema | Code: the tool unit. Spec: each unit that offers the tool to a model |
| Skill | Each file in its folder, by content. The frontmatter of `SKILL.md` on its own | Frontmatter: each unit whose model call lists the skill. A file: the tool call that reads it |
| MCP tool | Its spec as the server lists it. The repo's config for that server. The server's name and version, as it reports them | Spec: each unit that offers it. Config and version: the tool unit |
| Sub-agent | Declared: each field of its spec (prompt, model, tools, middleware, skills). A compiled graph: that graph's own nodes, routers and tools. Remote: its config in the repo *(not verified)* | The agent unit, or the units inside it. Its description: each unit that offers the tool that starts it |

**Ruling: a part that differs but isn't from the repo is outside the PR.** A server that was
upgraded. A skill folder in the user's home. The model a provider now serves under the same
name. The unit is inconclusive, with that reason, and never root: the PR didn't change it.
"From the repo" means a file inside the checkout, which is stricter than today's test for
project code (`_in_project` in `fingerprint.py`). An MCP server's version only ever adds that
reason. It never decides whether a unit is served and never names a root: the spec calls it
self-reported and tells clients not to change behaviour on it
([discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)).

**Serving an MCP tool.** A served tool unit returns the whole recorded response. lgcov never
re-sends a recorded request to a live server. One tool call can take several round trips: the
server asks for input and hands the client a `requestState` to send back. The spec says a
server should put a short expiry inside that state and reject it once lapsed
([multi round-trip requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)),
so a recorded one is dead. The unit is the whole call, served whole or run whole.

## 4. Root, propagated, and which change caused it

The labels keep their meaning (`classify` and `explain_runs` in
[`attribution.py`](../../src/lgcov/attribution.py)), read per unit. Root needs a changed part
where it needed a changed fingerprint. Propagated is as today. Inconclusive gains one reason:
only an outside part changed.

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
  instead, as the subgraph benchmark already expects: root `sub.inner`, propagated `after`,
  nothing on `sub` ([`subgraph.yaml`](../../tests/fixtures/bench/subgraph.yaml)). A container
  whose drift nothing inside explains is inconclusive, as today.

**From a root to the change in the PR.** The stated pain is "which change caused it". A
unit's name is half the answer. Its parts are the other half.

1. **One fingerprint per part.** Today a node has one hash for everything it reaches. So lgcov
   knows that a node changed, and not what changed in it.
2. **A stable name per part:** a function or a constant by its qualified name, a skill file by
   skill and file, an MCP tool by server and tool, a sub-agent's field by agent and field. The
   part's file is kept beside it, for the comment, and stays out of the fingerprint.
3. **A root's cause is its changed parts.** The comment names them with the root, and an
   inconclusive unit still lists the ones it saw. A shared helper is one part, so it shows
   once, behind every root it reaches. Several changed parts are all named: telling them
   apart needs a run per part, not designed here.
4. **Proposed: check each changed part against the PR's diff.** The job that decides can list
   the files the diff touches without trusting the PR. A changed part whose file isn't among
   them may not be the PR's doing. It has three readings: an outside change; a stale base
   recording, where base moved and nobody re-recorded; or a value built from a file the PR
   did touch. Telling a stale recording from an outside change is the stale-baseline question
   (T243). Built for chat-model parts' `source`; the outside-part reading stays open.
5. **A check this makes cheap.** Serve base's recorded output at a root, run again, and see
   whether the drift after it goes away. Not designed here.

**Trust.** Head's fingerprints and part names come from the job that runs the PR's code. They
are that code's word, as steps and probes are today ([trust boundary](../trust-boundary.md),
limit 1). Base's come from the recording in the base checkout. T238's rule holds: nothing the
PR writes may turn a blocking label into one that doesn't block.

## 5. LangGraph-only, or a tool-call boundary?

**Decision: the unit is defined so that it could sit at a tool-call boundary outside
LangGraph. The recorder stays LangGraph-only in this schema change.**

It could sit there. No field in section 2 needs a graph, and an MCP `tools/call` has a `tool`
unit's shape: a name, arguments and a result (section 3 has the rule for serving one). The
Claude Agent SDK's [hooks](https://code.claude.com/docs/en/agent-sdk/hooks) hand a recorder
each tool call's name, input, output and id, and the sub-agent it ran in. Why not now:

1. **Deep agents are inside LangGraph.** The deepagents
   [README](https://github.com/langchain-ai/deepagents/blob/main/README.md) says it is built
   on LangGraph, on top of `create_agent`, and that any compiled LangGraph graph can be a
   sub-agent. The recorder T193 needs reaches them without leaving LangGraph.
2. **Serving doesn't carry over.** It needs code that can stand in for a call. In LangGraph
   lgcov wraps the node, and the agent's code doesn't change. Chronicle serves outside
   LangGraph, but only at boundaries the developer marks in the agent's code. The Agent SDK's
   hooks can replace a tool's output only after the tool ran, and none can answer a model
   call. Every replay would pay for every call, and the job that runs the PR's code would
   need a model key ([trust boundary](../trust-boundary.md), limit 4).
3. **Propagated doesn't carry over.** It needs each unit's input. A tool-call boundary has it
   for tools, but not for the model choosing the next call. In LangGraph that input is a
   node's state.
4. **Scope.** [why-us](../why-us.md#what-lgcov-does-not-do-yet) lists agents not built on
   LangGraph under what lgcov doesn't do. This page only stops the schema from closing the
   door.

What stays LangGraph-only: `node` units and their paths; routers and routes (T175, T221);
`goto` for a `Command` or a `Send` (T168); the graph's node names (T238); probes that call a
node function; and serving by wrapping a node, or a `ToolNode` through its `wrap_tool_call`
(read, not run).

## 6. What the single schema change must carry

Subgraphs (T193), routes (T221) and the graph's node names (T238) share one cassette schema
change, with one re-record ([router design](../router-route-design.md)). Base's noise samples
(T229) join it, and so does T239. This lists fields. The migration is not designed here.

On a unit, with section 2 saying what each holds: `path` and an inner node's `router` for
T193; `routed` and `router_state` for T221; `samples` for T229; `kind`, `parts` and `call_id`
for T239; and `status` for T248, if its values are accepted. `code` stays while old recordings
are read. On the recording:

| Field | For | Holds |
|---|---|---|
| `schema` | All | The recording's schema number. There is none today |
| `scheme` | T212 | Which fingerprint scheme the recording uses, so the old one can go (`_Raw` in `fingerprint.py`) |
| `graph` | T238 | The node names of the graph that was recorded. T243 tells a stale list from a current one |
| `parts` | T239 | Each part's name, its fingerprint, its file and `source` (T243) |

- **`samples` sit in the recording** so the job that decides can apply the noise rule without
  running base's code. It reads them from the base checkout, which the PR can't write. Base's
  node names go there for the same reason.
- **The candidate file changes too.** It must carry head's units and parts, with the
  read-first, write-later rollout T162 used (`SCHEMA` in
  [`candidate.py`](../../src/lgcov/candidate.py)).

**What chronicle's record has that this unit lacks,** from its
[envelope schema](https://github.com/theagentplane/primitives/blob/main/src/agentplane_primitives/schemas/Envelope.json):

| Chronicle has | Here |
|---|---|
| The model call as its own kind, with the request as sent: `system`, `messages`, `tools`, `params` | Not taken: a model call stays inside its node. Open for T193: the request as sent is one place to read the "told" parts from, and the objects a node holds are the other |
| `output.model`, and whether that is the model served or the one requested | Taken, as an outside part. The name is already in each recorded AI message and nothing reads it (`encode` in `messages.py`; *not verified* per provider) |
| `attempt`, a `retry_of` link, and `output.error` with a type and a message | Not taken. lgcov's replay log counts the attempts that raised (`Call`), and `status` covers the error if T248 accepts its values |
| `started_at`, `ended_at`, `usage`, `provider`, `trace_id`, `span_id`, `parent_envelope_id`, and a `state` of open or closed | Not taken: cost, timing and cross-service ids, not cause. `path` names the parent, and a crash is already a `NodeError` |

## Not verified

- **T243 and T248** are in [`cassette-schema.md`](cassette-schema.md) rulings 4 and 1. T193 has
  no design of its own either: its fields here are what this page needs.
- **Nothing was run on a deep agent.** What section 1 says of one is read from lgcov's,
  LangChain's and deepagents' code.
- **How a recorder sees inside a tool call,** where a sub-agent runs, and how it ties a file
  read to a file, so a skill file becomes a part of the call that read it. Not designed.
- **How lgcov sees a transient failure.** It can mark an exception at the tool call or an
  HTTP status. A tool that catches its own timeout returns text, and an MCP tool reports an
  error inside its result.
- **A handle that a served MCP call returned** is unknown to a live server, so a later live
  call that passes it on fails. lgcov can't tell a handle from any other string.
- **Remote sub-agents,** which deepagents has, and an MCP server whose code is in the repo,
  whose files should count as parts. Not read, not designed.
- **Chronicle** beyond its README, its LangGraph instrumenter and its envelope schema, and
  the Claude Agent SDK beyond its hooks page. Chronicle's paper was read as an abstract only.
