# Does a shared-helper edit reach the node fingerprints? (T220)

Measured on 13 Nov 2026 at lgcov `8801e37`, on three merged open_deep_research PRs. Imports
only: no node ran, so no model or search call was made. A report, with no fix in it.

**Every node lgcov fingerprints registered every helper edit that reaches it, and no edit
registered where it shouldn't.** The gap is elsewhere: `lgcov ci` fingerprints only the entry
graph's plain-function nodes, so on #178 it flags 3 of the 8 nodes the edit reaches. The
expected nine is eight: `supervisor` never calls `get_today_str`.

## Counts

"Should change" is every node whose run can reach the edited code, counting a node that holds
or invokes a subgraph. "ci flags" is a node whose fingerprint differs between base and head in
what `lgcov ci` records (`cassette._codes` on the entry graph): head runs it live.

| PR | Edit | Nodes | Should change | ci flags | Invisible: in a subgraph | Visible, edit didn't register |
|---|---|---|---|---|---|---|
| [#178](https://github.com/langchain-ai/open_deep_research/pull/178) | `utils.py:get_today_str` body | 9 | 8 | 3 | 5 | 0 |
| [#143](https://github.com/langchain-ai/open_deep_research/pull/143) | 8 entries added to `utils.py:MODEL_TOKEN_LIMITS` | 9 | 1 | 1 | 0 | 0 |
| [#107](https://github.com/langchain-ai/open_deep_research/pull/107) | docstrings on 4 `@tool` classes in `multi_agent.py` | 5 | 5 | 2 | 3 | 0 |

The two gaps are different and need different fixes:

- **Invisible because it's in a subgraph (8 nodes across #178 and #107).** A node that is a
  compiled subgraph has no fingerprint, and lgcov records nothing inside it (T193). It always
  runs live, so the edit does take effect, but `lgcov ci` can't name the inner node, and calls
  the subgraph node inconclusive when it drifts. Walking the subgraphs by hand, 5 of the 6
  inner nodes register the edit. The sixth, #178's `supervisor_tools`, has no fingerprint
  either, because its code names the compiled `researcher_subgraph`.
- **Visible but the edit didn't register (0 nodes).** Not seen on these three PRs, at any of
  the four reaches below.

## What the fingerprint hashes

From `src/lgcov/fingerprint.py`, and confirmed by the runs where the last column says so. A
node's fingerprint is its function's source text, its defaults and closure values, and the
value of every module global its code names, followed the same way through project code.

| Reach | What is hashed | Registers? | Confirmed by |
|---|---|---|---|
| Helper in the node's own module | the helper's source text | yes | control C: an edit to `execute_tool_safely` changed `researcher_tools` only |
| Helper in another module | the helper's source text, when the node or a helper it calls names it | yes | #178: all 6 fingerprinted nodes that reach `get_today_str` changed |
| Helper behind a tool object | a `@tool`'s function, held by the tool object in a list a helper builds | yes | #178: `researcher_tools` reaches `get_today_str` only through `get_all_tools`, `tavily_search` and `summarize_webpage` |
| Module-level constant | its value: a dict's keys and values | yes | #143: `final_report_generation` changed, the other 6 fingerprinted nodes didn't |
| Tool class handed over in a list | the class's members and pydantic schema, docstring included | yes | #107: all 4 function nodes changed |
| Line numbers, file paths | not hashed | n/a | control B: a new function above every other shifted them and changed nothing |

Not hashed, from the module's docstring and not tested here: what a node reads at run time
(files, environment, network), library upgrades, anything looked up by a string built at run
time, and a change an object's `repr` hides.

## Per node

Fingerprints are stable across processes: #178's base gave the same nine values in a second
process. "direct" and "through" say how the node reaches the edit.

### #178, `71c857f` to `9f6cfce`: `deep_researcher.py:deep_researcher`

| Node | In what ci records | Base | Head | Result | Reaches `get_today_str` |
|---|---|---|---|---|---|
| `clarify_with_user` | yes | `81d718adc01d53a7` | `8ed2754a3f354877` | changed | direct |
| `write_research_brief` | yes | `1248b735f6a5b919` | `98444e382ff77f77` | changed | direct |
| `final_report_generation` | yes | `680c064ea68c18e0` | `1743830149238bba` | changed | direct |
| `research_supervisor` | yes, no fingerprint | none | none | always live | holds the subgraph |
| `research_supervisor.supervisor` | no | `8a72f6c3bf7fcf22` | `8a72f6c3bf7fcf22` | same, correctly | doesn't |
| `research_supervisor.supervisor_tools` | no | none | none | no fingerprint: names `researcher_subgraph` | invokes the researcher graph |
| `research_supervisor.supervisor_tools.researcher` | no | `6d29c4f161031ac1` | `855f19bdc337c45a` | changed | direct, and through the tool list |
| `research_supervisor.supervisor_tools.researcher_tools` | no | `414bedd387ec8bca` | `7a1d8794b455c939` | changed | through the tool list only |
| `research_supervisor.supervisor_tools.compress_research` | no | `57d2c0d3f7ca5bb5` | `9aeda19c0377d1df` | changed | direct |

### #143, `0c9b239` to `062d1e0`: same graph

Only `final_report_generation` reaches `MODEL_TOKEN_LIMITS`, through `get_model_token_limit`.
It went from `5bde47de8eb6636e` to `680c064ea68c18e0`. The other six fingerprinted nodes kept
their values. `is_token_limit_exceeded`, which three nodes call, doesn't read the table.

### #107, `c300af6` to `d2c9f60`: `multi_agent.py:graph`

| Node | In what ci records | Base | Head | Result | Reaches the tool classes |
|---|---|---|---|---|---|
| `supervisor` | yes | `05fcd61ea1009388` | `5262702fea389fd5` | changed | list from `get_supervisor_tools` |
| `supervisor_tools` | yes | `67a6702ee9d53d6c` | `5877811c8a0782a1` | changed | the same list, called by name |
| `research_team` | yes, no fingerprint | none | none | always live | holds the subgraph |
| `research_team.research_agent` | no | `5be06e8acba19155` | `40d3f22c974d5b5f` | changed | list from `get_research_tools` |
| `research_team.research_agent_tools` | no | `3c9115577bca6ee8` | `252fd5c3a29a08f4` | changed | the same list, called by name |

## Controls

Each is one edit on #178's base, compared with the unedited base.

| Control | Edit | Expected | Result |
|---|---|---|---|
| A | one line added to `utils.py:get_notes_from_tool_calls` | no change: its only caller, `supervisor_tools`, has no fingerprint | no fingerprint changed |
| B | a new function nothing calls, added above `utils.py`'s first function | no change | no fingerprint changed |
| C | one line added to `deep_researcher.py:execute_tool_safely` | `researcher_tools` only | `researcher_tools` only |

No existing helper in `utils.py` goes uncalled, so B adds one. A shows the other side of the
subgraph gap: an edit to a helper that only `supervisor_tools` calls changes no fingerprint
anywhere.

## How it was run

- lgcov's fingerprint code from a worktree at `8801e37`. Graphs loaded with
  `lgcov.cli._load_graph`, with the side's `src` on the path, as `lgcov bench` loads them.
- Each side in its own process with an empty environment, dummy keys and every proxy pointed
  at a dead port.
- Python 3.12.4, langgraph 1.2.16, langchain-core 1.6.13, pydantic 2.14.1 on every side, not
  the versions each PR's lockfile pinned. #107's tree needed its own environment, with
  `pymupdf` held below 2: `pymupdf==2.1` failed to build.
- The subgraph walk is this report's own: it fingerprints the nodes of each compiled graph a
  node holds or names. `lgcov ci` doesn't do that.

## Limits

- Three PRs from one project, chosen because their helpers are shared.
- A changed fingerprint means the node runs live on head. Whether `lgcov ci` then labels it
  root also depends on drift and a probe, which need a run and weren't measured.
- "Should change" was traced by hand from each tree's source.
