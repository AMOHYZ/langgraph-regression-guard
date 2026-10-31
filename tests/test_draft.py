"""T106: a taxonomy drafted from traces, for a human to approve (pure)."""

import copy

import pytest
from fixtures.drafting import SKIPPED, TEAM, TRACES, Scripted

from lgcov.draft import DraftTrace, Failure, approve, draft, parse_trace
from lgcov.taxonomy import PendingReview, parse_taxonomy


def test_draft_proposes_one_pending_failure_mode_per_signature_with_evidence() -> None:
    client = Scripted()
    doc = draft(None, "demo_agent", TEAM, TRACES, client)
    assert client.asked == ["respond: rude_tone", "respond: skipped_lookup"]  # one per group
    assert [m["id"] for m in doc["failure_modes"]] == ["rude_reply", "skips_order_lookup"]
    assert [m["evidence"] for m in doc["failure_modes"]] == [["t5"], ["t3", "t4"]]
    assert doc["failure_modes"][1]["signature"] == "respond: skipped_lookup"
    assert [k["evidence"] for k in doc["kpis"]] == [["t5"], ["t3", "t4"]]
    entries = [*doc["failure_modes"], *doc["kpis"]]
    assert all(e["review"] == "pending" and e["why"] for e in entries)  # it never approves


def test_a_group_a_code_check_can_decide_gets_judge_code() -> None:
    doc = draft(None, "demo_agent", TEAM, TRACES, Scripted())
    polite, lookup = doc["kpis"]
    # Every passing run calls lookup_order and neither skipped_lookup run does.
    assert (lookup["judge"], lookup["node"]) == ("code", "respond")
    assert lookup["check"] == {"type": "tool_called", "tool": "lookup_order"}
    assert polite["judge"] == "jev" and "check" not in polite  # no tool or node tells t5 apart


def test_approve_flips_a_failure_mode_and_its_kpis_and_records_the_reviewer() -> None:
    doc = draft(None, "demo_agent", TEAM, TRACES, Scripted())
    approved = approve(doc, "skips_order_lookup", "ahmed")
    mode, kpi = approved["failure_modes"][1], approved["kpis"][1]
    assert (mode["review"], mode["reviewer"]) == ("approved", "ahmed")
    assert (kpi["review"], kpi["reviewer"]) == ("approved", "ahmed")
    assert approved["failure_modes"][0]["review"] == "pending"
    assert doc["failure_modes"][1]["review"] == "pending"  # the draft it was given is unchanged
    with pytest.raises(PendingReview, match="2 entries pending human review"):
        parse_taxonomy(approved)
    with pytest.raises(ValueError, match="no entry `nope`"):
        approve(doc, "nope", "ahmed")


def test_redraft_keeps_approved_entries_and_proposes_only_new_signatures() -> None:
    first = approve(draft(None, "demo_agent", TEAM, TRACES, Scripted()), "rude_reply", "ahmed")
    kept = copy.deepcopy(first)
    wrong = DraftTrace("t6", SKIPPED, Failure("classify", "wrong_intent"))
    client = Scripted()
    again = draft(first, "demo_agent", TEAM, (*TRACES, wrong), client)
    assert client.asked == ["classify: wrong_intent"]  # signatures already drafted aren't asked
    assert again["failure_modes"][:2] == kept["failure_modes"]
    assert again["kpis"][:2] == kept["kpis"]
    assert again["failure_modes"][2]["id"] == "misreads_intent"
    assert again["failure_modes"][2]["review"] == "pending"


def test_a_trace_is_a_run_with_an_optional_failure() -> None:
    raw = {"id": "t9", "nodes": ["a"], "tools": [], "failure": {"node": "a", "kind": "loops"}}
    assert parse_trace(raw, "x.json") == DraftTrace(
        "t9", parse_trace(raw, "x.json").run, Failure("a", "loops")
    )
    with pytest.raises(ValueError, match="x.json: a failure needs a 'node' and a 'kind'"):
        parse_trace({**raw, "failure": {"node": "a"}}, "x.json")
    with pytest.raises(ValueError, match="x.json: a trace needs an 'id'"):
        parse_trace({"nodes": ["a"]}, "x.json")
