"""T165: a served output fitted to the ids this run made, or not served at all."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from lgcov.rebind import Ambiguous, rebind


def _ai(message_id: str, *call_ids: str, content: str = "") -> AIMessage:
    calls = [
        {"name": "search", "args": {"q": "x"}, "id": call_id, "type": "tool_call"}
        for call_id in call_ids
    ]
    return AIMessage(content, id=message_id, tool_calls=calls)


def _tool(call_id: str, message_id: str = "t1", content: str = "found") -> ToolMessage:
    return ToolMessage(content, tool_call_id=call_id, name="search", id=message_id)


def _state(*messages: Any) -> dict[str, Any]:
    return {"messages": list(messages)}


# Fitted.
def test_a_tool_result_answers_the_call_this_run_made() -> None:
    recorded = _state(HumanMessage("q", id="u1"), _ai("m1", "call_1"))
    current = _state(HumanMessage("q", id="u2"), _ai("m2", "call_2"))
    fitted = rebind(_state(_tool("call_1")), recorded, current)
    assert fitted["messages"][0].tool_call_id == "call_2"


def test_a_remove_message_names_the_message_this_run_has() -> None:
    recorded = _state(HumanMessage("q", id="u1"), _ai("m1"))
    current = _state(HumanMessage("q", id="u2"), _ai("m2"))
    fitted = rebind(_state(RemoveMessage(id="u1")), recorded, current)
    assert fitted["messages"][0].id == "u2"


def test_a_message_that_replaces_another_by_id_keeps_doing_so() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    edited = _ai("m1", "call_1", content="edited")
    fitted = rebind(_state(edited), recorded, current)["messages"][0]
    assert (fitted.id, fitted.tool_calls[0]["id"]) == ("m2", "call_2")
    assert fitted.content == "edited"


def test_several_calls_map_by_position() -> None:
    recorded = _state(_ai("m1", "a", "b"))
    current = _state(_ai("m2", "x", "y"))
    fitted = rebind(_state(_tool("a", "t1"), _tool("b", "t2")), recorded, current)
    assert [m.tool_call_id for m in fitted["messages"]] == ["x", "y"]


def test_a_plain_tool_call_in_a_send_payload_is_read() -> None:
    recorded = {"call": {"name": "s", "args": {"q": "x"}, "id": "call_1", "type": "tool_call"}}
    current = {"call": {"name": "s", "args": {"q": "x"}, "id": "call_2", "type": "tool_call"}}
    fitted = rebind(_state(_tool("call_1")), recorded, current)
    assert fitted["messages"][0].tool_call_id == "call_2"


def test_a_plain_tool_result_is_fitted_too() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    result = {"role": "tool", "content": "r", "tool_call_id": "call_1"}
    assert rebind(_state(result), recorded, current) == _state({**result, "tool_call_id": "call_2"})


# Left alone.
def test_an_output_naming_no_input_id_is_served_as_it_is() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    output = _state(_ai("new", "its_own_call"))
    assert rebind(output, recorded, current) is output


def test_an_input_with_the_same_ids_changes_nothing() -> None:
    state = _state(_ai("m1", "call_1"))
    output = _state(_tool("call_1"))
    assert rebind(output, state, state) is output


def test_the_recorded_output_is_never_changed_in_place() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    output = _state(_tool("call_1"))
    rebind(output, recorded, current)
    assert output["messages"][0].tool_call_id == "call_1"


# Ambiguous: not served.
def test_an_order_mismatch_is_ambiguous() -> None:
    # This run's tools answered in the other order: "a" pairs with both "x" and "y".
    recorded = _state(_ai("m1", "a", "b"), _tool("a", "t1"), _tool("b", "t2"))
    current = _state(_ai("m2", "x", "y"), _tool("y", "t3"), _tool("x", "t4"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a", "t5")), recorded, current)


def test_a_count_mismatch_between_messages_is_ambiguous() -> None:
    # Only possible when ignore_paths hides the difference from the input match.
    recorded = _state(_ai("m1", "a"), _tool("a"))
    current = _state(_ai("m2", "x"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a", "t5")), recorded, current)


def test_a_count_mismatch_between_a_message_s_calls_is_ambiguous() -> None:
    recorded, current = _state(_ai("m1", "a", "b")), _state(_ai("m2", "x"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a")), recorded, current)


def test_one_recorded_id_for_two_of_this_run_s_is_ambiguous() -> None:
    # A provider that numbers its calls per reply: call_0 in both.
    recorded = _state(_ai("m1", "call_0"), _tool("call_0"), _ai("m2", "call_0"))
    current = _state(_ai("m3", "x"), _tool("x"), _ai("m4", "y"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("call_0", "t5")), recorded, current)


def test_two_recorded_ids_for_one_of_this_run_s_is_ambiguous() -> None:
    recorded = _state(_ai("m1", "a"), _ai("m2", "b"))
    current = _state(_ai("m3", "call_0"), _ai("m4", "call_0"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a")), recorded, current)


def test_a_recorded_id_this_run_has_nothing_for_is_ambiguous() -> None:
    no_id = [{"name": "search", "args": {"q": "x"}, "id": None, "type": "tool_call"}]
    recorded = _state(_ai("m1", "a"))
    current = _state(AIMessage("", id="m2", tool_calls=no_id))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a")), recorded, current)


def test_a_message_where_the_recording_has_something_else_is_ambiguous() -> None:
    recorded = _state(_ai("m1", "a"))
    with pytest.raises(Ambiguous):
        rebind(_state(_tool("a")), recorded, _state({"role": "ai", "content": ""}))


def test_an_ambiguous_mapping_doesn_t_matter_to_an_output_that_uses_none_of_it() -> None:
    recorded = _state(_ai("m1", "a", "b"), _tool("a", "t1"), _tool("b", "t2"))
    current = _state(_ai("m2", "x", "y"), _tool("y", "t3"), _tool("x", "t4"))
    output = _state(_ai("new", content="done"))
    assert rebind(output, recorded, current) is output


# Masked fields only.
def test_tool_args_and_content_are_never_rewritten() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    args = {"id": "call_1", "tool_call_id": "call_1", "ref": "call_1", "more": [{"id": "call_1"}]}
    calls = [{"name": "s", "args": args, "id": "own", "type": "tool_call"}]
    output = _state(
        AIMessage("about call_1", id="new", tool_calls=calls),
        _tool("call_1", content="answers call_1"),
    )
    fitted = rebind(output, recorded, current)["messages"]
    assert fitted[0].tool_calls[0]["args"] == args
    assert fitted[0].content == "about call_1"
    assert (fitted[1].tool_call_id, fitted[1].content) == ("call_2", "answers call_1")


def test_args_in_a_plain_tool_call_are_never_rewritten() -> None:
    recorded, current = _state(_ai("m1", "call_1")), _state(_ai("m2", "call_2"))
    output = {"call": {"id": "call_1", "args": {"id": "call_1", "tool_call_id": "call_1"}}}
    assert rebind(output, recorded, current) == {
        "call": {"id": "call_2", "args": {"id": "call_1", "tool_call_id": "call_1"}}
    }


def test_ids_inside_tool_args_never_make_a_mapping() -> None:
    def state(value: str) -> dict[str, Any]:
        calls = [{"name": "s", "args": {"id": value}, "id": "call_1", "type": "tool_call"}]
        return {
            "messages": [AIMessage("", id="m1", tool_calls=calls)],
            "plain": {"args": {"id": value}},
        }

    output = _state(_tool("from_args"))
    assert rebind(output, state("from_args"), state("something_else")) is output
