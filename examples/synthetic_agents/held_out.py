"""Held-out synthetic cases (T264). Opt-in via every_held_out_case / bench --held-out.

Each case is a real edited graph built from the happy-path agents (demo / fanout /
subgraph), labeled with user-consequence truth and which suite question kinds the
edit is harmful on. Not part of AGENTS / every_case (the main synthetic suite).
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from examples.demo_agent import graph as demo
from examples.synthetic_agents import fanout, subgraph
from lgcov.synthetic import Case
from lgcov.synthetic import every_held_out_case as _every_held_out_case

# Suite questions (hardcoded; do not import QUESTIONS — that pulls mutations wiring).
# "Where is my order?" / "Order status?" → order; "What are your hours?" → hours.

HarmOn = Literal["all", "order", "hours", "none"]
Truth = Literal["harmful", "harmless"]


@dataclass(frozen=True)
class HeldOutCase:
    id: str
    agent: str  # demo | fanout | subgraph
    edit_kind: str
    description: str
    truth: Truth
    why: str  # user consequence
    harm_on: HarmOn
    graph: Any  # compiled edited graph


# --- demo helpers (rebuild like a PR; no mutations module) ---


def _demo_build(
    *,
    classify_fn: Callable[..., Any] = demo.classify,
    lookup_fn: Callable[..., Any] = demo.lookup_order,
    respond_fn: Callable[..., Any] = demo.respond,
    route_fn: Callable[..., Any] = demo.route,
    schema: type[Any] = demo.State,
) -> Any:
    b = StateGraph(schema)
    b.add_node("classify", classify_fn)
    b.add_node("lookup_order", lookup_fn)
    b.add_node("respond", respond_fn)
    b.add_edge(START, "classify")
    b.add_conditional_edges("classify", route_fn)
    b.add_edge("lookup_order", "respond")
    b.add_edge("respond", END)
    return b.compile()


def _respond_with_prompt(prompt: str) -> Callable[[demo.State], demo.State]:
    def respond(state: demo.State) -> demo.State:
        ctx = state.get("tool_result", "")
        return {"answer": demo.fake_llm(f"{prompt}\n{ctx}\n{state['question']}")}

    return respond


def _respond_no_prompt(state: demo.State) -> demo.State:
    ctx = state.get("tool_result", "")
    return {"answer": demo.fake_llm(f"{ctx}\n{state['question']}")}


def _respond_prefix_slice(state: demo.State) -> demo.State:
    ctx = state.get("tool_result", "")
    prompt = f"{demo.SYSTEM_PROMPT}\n{ctx}\n{state['question']}"
    return {"answer": f"[{len(prompt)}] " + prompt[:40]}


def _classify_help(state: demo.State) -> demo.State:
    return {"needs_tool": "help" in state["question"].lower()}


def _classify_always(state: demo.State) -> demo.State:
    return {"needs_tool": True}


def _classify_order_or_shipment(state: demo.State) -> demo.State:
    q = state["question"].lower()
    return {"needs_tool": "order" in q or "shipment" in q}


def _route_skip_tool(_state: demo.State) -> Literal["lookup_order", "respond"]:
    return "respond"


def _lookup_reword(_state: demo.State) -> demo.State:
    return {"tool_result": "order #42 has shipped"}


class _UnusedNotesState(TypedDict, total=False):
    question: str
    needs_tool: bool
    tool_result: str
    answer: str
    notes: str


# --- fanout helpers ---


class _PlannedState(TypedDict, total=False):
    question: str
    parts: Annotated[list[str], operator.add]
    summary: str
    planned: bool


def _fanout_build(
    *,
    plan_fn: Callable[..., Any] = fanout.plan,
    router: Callable[..., Any] = fanout.fan_out,
    a_worker: Callable[..., Any] = fanout.worker_a,
    b_worker: Callable[..., Any] = fanout.worker_b,
    c_worker: Callable[..., Any] = fanout.worker_c,
    reducer_fn: Callable[..., Any] = fanout.reducer,
    workers: tuple[str, ...] = fanout.WORKERS,
    schema: type[Any] = fanout.State,
    extra_nodes: tuple[tuple[str, Callable[..., Any]], ...] = (),
) -> Any:
    b = StateGraph(schema)
    b.add_node("plan", plan_fn)
    b.add_node("worker_a", a_worker)
    b.add_node("worker_b", b_worker)
    b.add_node("worker_c", c_worker)
    for name, fn in extra_nodes:
        b.add_node(name, fn)
    b.add_node("reducer", reducer_fn)
    b.add_edge(START, "plan")
    b.add_conditional_edges("plan", router, list(workers))
    for name in workers:
        b.add_edge(name, "reducer")
    b.add_edge("reducer", END)
    return b.compile()


def _fan_drop_b(state: fanout.State) -> list[Send]:
    return [Send(name, state) for name in ("worker_a", "worker_c")]


def _fan_cheerful(state: fanout.State) -> fanout.State:
    return {"parts": [f"b:cheerful:{state['question']}"]}


def _fan_a_empty(_state: fanout.State) -> fanout.State:
    return {"parts": []}


def _fan_comma_join(state: fanout.State) -> fanout.State:
    return {"summary": ", ".join(sorted(state["parts"]))}


def _fan_unsorted(state: fanout.State) -> fanout.State:
    return {"summary": " | ".join(state["parts"])}


def _fan_plan_flag(_state: _PlannedState) -> _PlannedState:
    return {"planned": True}


def _fan_dup_a_skip_c(state: fanout.State) -> list[Send]:
    return [Send("worker_a", state), Send("worker_a", state), Send("worker_b", state)]


def _fan_c_prefix_case(state: fanout.State) -> fanout.State:
    return {"parts": [f"C:{state['question']}"]}


def _fan_reorder(state: fanout.State) -> list[Send]:
    return [Send(name, state) for name in ("worker_c", "worker_a", "worker_b")]


def _fan_dead_d(_state: fanout.State) -> fanout.State:
    return {"parts": ["d:unused"]}


# --- subgraph helpers ---


def _sub_build(
    *,
    before_node: Callable[..., Any] = subgraph.before,
    inner_node: Callable[..., Any] = subgraph.inner,
    after_node: Callable[..., Any] = subgraph.after,
    schema: type[Any] = subgraph.State,
) -> Any:
    sub = StateGraph(schema)
    sub.add_node("inner", inner_node)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)
    b = StateGraph(schema)
    b.add_node("before", before_node)
    b.add_node("sub", sub.compile())
    b.add_node("after", after_node)
    b.add_edge(START, "before")
    b.add_edge("before", "sub")
    b.add_edge("sub", "after")
    b.add_edge("after", END)
    return b.compile()


def _sub_short(state: subgraph.State) -> subgraph.State:
    return {"draft": f"short:{state['question']}"}


def _sub_truncate(state: subgraph.State) -> subgraph.State:
    return {"draft": f"{subgraph.STYLE}:{state['question']}"[:8]}


def _sub_tail_only(state: subgraph.State) -> subgraph.State:
    return {"answer": state["draft"].upper()[-8:]}


def _sub_equiv_join(state: subgraph.State) -> subgraph.State:
    return {"draft": ":".join((subgraph.STYLE, state["question"]))}


def _sub_exclaim(state: subgraph.State) -> subgraph.State:
    return {"answer": state["draft"].upper() + "!"}


def _sub_no_strip(state: subgraph.State) -> subgraph.State:
    return {"question": state["question"]}


def _sub_strip_lower(state: subgraph.State) -> subgraph.State:
    return {"question": state["question"].strip().lower()}


def _sub_drop_style(state: subgraph.State) -> subgraph.State:
    return {"draft": state["question"]}


def _sub_casefold_upper(state: subgraph.State) -> subgraph.State:
    return {"answer": state["draft"].casefold().upper()}


class _UnusedMetaState(TypedDict, total=False):
    question: str
    draft: str
    answer: str
    meta: str


HELD_OUT: tuple[HeldOutCase, ...] = (
    # --- demo (10) ---
    HeldOutCase(
        id="ho_demo_prompt_synonym",
        agent="demo",
        edit_kind="prompt",
        description="SYSTEM_PROMPT → brief synonym of concise support agent",
        truth="harmless",
        why="Still answers as a short support agent; the user gets the same kind of help.",
        harm_on="none",
        graph=_demo_build(respond_fn=_respond_with_prompt("You are a brief support agent.")),
    ),
    HeldOutCase(
        id="ho_demo_prompt_role_swap",
        agent="demo",
        edit_kind="prompt",
        description="SYSTEM_PROMPT → long-winded sales closer",
        truth="harmful",
        why="Every reply becomes sales pitch instead of concise support the user asked for.",
        harm_on="all",
        graph=_demo_build(respond_fn=_respond_with_prompt("You are a long-winded sales closer.")),
    ),
    HeldOutCase(
        id="ho_demo_classify_help_token",
        agent="demo",
        edit_kind="routing",
        description="classify needs_tool on 'help' instead of 'order'",
        truth="harmful",
        why="Asking where an order is never looks it up, so the user gets no shipment status.",
        harm_on="order",
        graph=_demo_build(classify_fn=_classify_help),
    ),
    HeldOutCase(
        id="ho_demo_classify_always_tool",
        agent="demo",
        edit_kind="routing",
        description="classify always sets needs_tool=True",
        truth="harmful",
        why="Hours (and other non-order) questions wrongly run an order lookup and can mislead.",
        harm_on="hours",
        graph=_demo_build(classify_fn=_classify_always),
    ),
    HeldOutCase(
        id="ho_demo_route_skip_tool",
        agent="demo",
        edit_kind="routing",
        description="route always returns respond",
        truth="harmful",
        why="Order questions skip lookup, so the user never learns whether the order shipped.",
        harm_on="order",
        graph=_demo_build(route_fn=_route_skip_tool),
    ),
    HeldOutCase(
        id="ho_demo_tool_status_reword",
        agent="demo",
        edit_kind="tool",
        description="lookup_order returns 'order #42 has shipped'",
        truth="harmless",
        why="Same shipped status for the same order; the user still learns it went out.",
        harm_on="none",
        graph=_demo_build(lookup_fn=_lookup_reword),
    ),
    HeldOutCase(
        id="ho_demo_respond_drop_prompt",
        agent="demo",
        edit_kind="prompt",
        description="respond omits SYSTEM_PROMPT from the LLM input",
        truth="harmful",
        why="Answers lose the support-agent framing, so every reply can stop sounding like help.",
        harm_on="all",
        graph=_demo_build(respond_fn=_respond_no_prompt),
    ),
    HeldOutCase(
        id="ho_demo_unused_state_key",
        agent="demo",
        edit_kind="state",
        description="State gains unused notes field",
        truth="harmless",
        why="No node reads or writes notes; classify, lookup, and answers are unchanged.",
        harm_on="none",
        graph=_demo_build(schema=_UnusedNotesState),
    ),
    HeldOutCase(
        id="ho_demo_llm_prefix_slice",
        agent="demo",
        edit_kind="respond",
        description="fake_llm returns prompt[:40] instead of prompt[-40:]",
        truth="harmful",
        why="The question sits at the end of the prompt, so answers can omit what was asked.",
        harm_on="all",
        graph=_demo_build(respond_fn=_respond_prefix_slice),
    ),
    HeldOutCase(
        id="ho_demo_classify_order_or_shipment",
        agent="demo",
        edit_kind="routing",
        description="classify needs_tool on order or shipment",
        truth="harmless",
        why="Order questions still look up the order; an extra trigger does not hide status.",
        harm_on="none",
        graph=_demo_build(classify_fn=_classify_order_or_shipment),
    ),
    # --- fanout (10) ---
    HeldOutCase(
        id="ho_fan_drop_worker_b",
        agent="fanout",
        edit_kind="routing",
        description="fan_out Sends only worker_a and worker_c",
        truth="harmful",
        why="The summary silently drops worker B's view, so the user gets an incomplete answer.",
        harm_on="all",
        graph=_fanout_build(router=_fan_drop_b),
    ),
    HeldOutCase(
        id="ho_fan_tone_cheerful",
        agent="fanout",
        edit_kind="prompt",
        description="worker_b emits cheerful tone instead of plain",
        truth="harmful",
        why="Every summary's B line becomes cheerful instead of the plain tone users expect.",
        harm_on="all",
        graph=_fanout_build(b_worker=_fan_cheerful),
    ),
    HeldOutCase(
        id="ho_fan_worker_a_empty",
        agent="fanout",
        edit_kind="worker",
        description="worker_a returns empty parts",
        truth="harmful",
        why="Worker A's contribution vanishes from the summary the user reads.",
        harm_on="all",
        graph=_fanout_build(a_worker=_fan_a_empty),
    ),
    HeldOutCase(
        id="ho_fan_reducer_comma_join",
        agent="fanout",
        edit_kind="reducer",
        description="reducer joins parts with comma instead of pipe",
        truth="harmless",
        why="All worker parts are still there; only the separator the user sees changes.",
        harm_on="none",
        graph=_fanout_build(reducer_fn=_fan_comma_join),
    ),
    HeldOutCase(
        id="ho_fan_reducer_unsorted",
        agent="fanout",
        edit_kind="reducer",
        description="reducer joins parts without sorting",
        truth="harmless",
        why="The same parts reach the user; only presentation order changes.",
        harm_on="none",
        graph=_fanout_build(reducer_fn=_fan_unsorted),
    ),
    HeldOutCase(
        id="ho_fan_plan_noop_flag",
        agent="fanout",
        edit_kind="state",
        description="plan sets unused planned=True",
        truth="harmless",
        why="Nothing reads planned; workers and the summary the user gets are unchanged.",
        harm_on="none",
        graph=_fanout_build(plan_fn=_fan_plan_flag, schema=_PlannedState),
    ),
    HeldOutCase(
        id="ho_fan_duplicate_a_skip_c",
        agent="fanout",
        edit_kind="routing",
        description="Send twice to worker_a, never worker_c",
        truth="harmful",
        why="Worker C never runs, so the user never sees C's part of the answer.",
        harm_on="all",
        graph=_fanout_build(router=_fan_dup_a_skip_c),
    ),
    # Reclassified from harmful: C: vs c: is cosmetic for the user (content still present).
    HeldOutCase(
        id="ho_fan_worker_c_prefix_case",
        agent="fanout",
        edit_kind="worker",
        description="worker_c prefixes with C: instead of c:",
        truth="harmless",
        why="Worker C's text still reaches the summary; only the tag letter case differs.",
        harm_on="none",
        graph=_fanout_build(c_worker=_fan_c_prefix_case),
    ),
    HeldOutCase(
        id="ho_fan_workers_tuple_reorder",
        agent="fanout",
        edit_kind="routing",
        description="Send all three workers in a different order",
        truth="harmless",
        why="All three workers still run; the user still gets every part after reduce.",
        harm_on="none",
        graph=_fanout_build(router=_fan_reorder),
    ),
    HeldOutCase(
        id="ho_fan_dead_worker_d_node",
        agent="fanout",
        edit_kind="graph",
        description="Register unused worker_d; fan_out still only Sends a/b/c",
        truth="harmless",
        why="Dead node never runs; the live summary the user reads is unchanged.",
        harm_on="none",
        graph=_fanout_build(extra_nodes=(("worker_d", _fan_dead_d),)),
    ),
    # --- subgraph (10) ---
    HeldOutCase(
        id="ho_sub_style_short",
        agent="subgraph",
        edit_kind="prompt",
        description="inner draft uses short: instead of long:",
        truth="harmful",
        why="Answers carry SHORT: instead of the LONG: draft shape the product promises.",
        harm_on="all",
        graph=_sub_build(inner_node=_sub_short),
    ),
    HeldOutCase(
        id="ho_sub_inner_truncate",
        agent="subgraph",
        edit_kind="subgraph",
        description="inner truncates draft to 8 characters",
        truth="harmful",
        why="The final answer is cut short, so the user may not see their full question echoed.",
        harm_on="all",
        graph=_sub_build(inner_node=_sub_truncate),
    ),
    HeldOutCase(
        id="ho_sub_after_tail_only",
        agent="subgraph",
        edit_kind="after",
        description="after keeps only the last 8 characters",
        truth="harmful",
        why="The user only sees the tail of the answer; the question body can disappear.",
        harm_on="all",
        graph=_sub_build(after_node=_sub_tail_only),
    ),
    HeldOutCase(
        id="ho_sub_inner_equiv_join",
        agent="subgraph",
        edit_kind="subgraph",
        description="inner builds the same draft via join",
        truth="harmless",
        why="Still drafts long:{question}; the user-facing answer is unchanged.",
        harm_on="none",
        graph=_sub_build(inner_node=_sub_equiv_join),
    ),
    HeldOutCase(
        id="ho_sub_after_exclaim",
        agent="subgraph",
        edit_kind="after",
        description="after appends ! after uppercasing",
        truth="harmless",
        why="Full uppercased draft (and question) still present; trailing ! is cosmetic.",
        harm_on="none",
        graph=_sub_build(after_node=_sub_exclaim),
    ),
    HeldOutCase(
        id="ho_sub_before_no_strip",
        agent="subgraph",
        edit_kind="before",
        description="before returns the question without strip",
        truth="harmful",
        why="Leading/trailing spaces leak into the answer, so padded questions look wrong.",
        harm_on="all",
        graph=_sub_build(before_node=_sub_no_strip),
    ),
    HeldOutCase(
        id="ho_sub_before_strip_lower",
        agent="subgraph",
        edit_kind="before",
        description="before strips then lowercases",
        truth="harmless",
        why="after uppercases the draft, so the user still sees the full question in capitals.",
        harm_on="none",
        graph=_sub_build(before_node=_sub_strip_lower),
    ),
    HeldOutCase(
        id="ho_sub_inner_drop_style",
        agent="subgraph",
        edit_kind="subgraph",
        description="inner sets draft to raw question without STYLE prefix",
        truth="harmful",
        why="Answers lose the required LONG: prefix, so the expected draft shape is gone.",
        harm_on="all",
        graph=_sub_build(inner_node=_sub_drop_style),
    ),
    HeldOutCase(
        id="ho_sub_unused_meta_key",
        agent="subgraph",
        edit_kind="state",
        description="State gains unused meta field",
        truth="harmless",
        why="No node touches meta; before/inner/after answers stay the same for the user.",
        harm_on="none",
        graph=_sub_build(schema=_UnusedMetaState),
    ),
    HeldOutCase(
        id="ho_sub_after_upper_via_casefold",
        agent="subgraph",
        edit_kind="after",
        description="after uppercases via casefold().upper()",
        truth="harmless",
        why="Still a full uppercase of the draft; the user still sees the whole question.",
        harm_on="none",
        graph=_sub_build(after_node=_sub_casefold_upper),
    ),
)


def every_held_out_case() -> tuple[Case, ...]:
    """T264: Cases for HELD_OUT on each suite question. Opt-in; not in every_case."""
    from examples.synthetic_agents import AGENTS

    return _every_held_out_case(AGENTS, HELD_OUT)
