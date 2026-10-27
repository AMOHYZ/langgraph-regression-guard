"""T085: mask volatile fields (UUIDs, ISO timestamps, ids, ignore_paths) before comparing."""

from lgcov.normalize import normalize


def test_uuids_and_timestamps_are_masked_inside_text() -> None:
    raw = "run 3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-10-27T07:15:30.123456+00:00 ok"
    assert normalize(raw) == "run <uuid> at <timestamp> ok"


def test_plain_dates_and_other_text_are_kept() -> None:
    assert normalize("shipped 2026-10-27, order #42") == "shipped 2026-10-27, order #42"


def test_volatile_id_keys_are_masked_but_other_keys_are_not() -> None:
    raw = {"id": "msg_01ABC", "tool_call_id": 7, "order_id": "42", "needs_tool": True}
    assert normalize(raw) == {
        "id": "<id>",
        "tool_call_id": "<id>",
        "order_id": "42",
        "needs_tool": True,
    }


def test_ignore_paths_are_json_pointers_with_wildcards() -> None:
    raw = {"meta": {"nonce": 0.42, "a/b": 1}, "items": [{"seed": 1, "x": 2}, {"seed": 3, "x": 4}]}
    out = normalize(raw, ["/meta/nonce", "/meta/a~1b", "/items/*/seed"])
    assert out == {
        "meta": {"nonce": "<ignored>", "a/b": "<ignored>"},
        "items": [{"seed": "<ignored>", "x": 2}, {"seed": "<ignored>", "x": 4}],
    }
