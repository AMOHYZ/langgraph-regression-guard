"""T085: mask volatile fields (UUIDs, ISO timestamps, ids, ignore_paths) before comparing."""

from lgcov.cassette import NodeIO, diff_cassettes
from lgcov.normalize import normalize, normalize_counted


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


def test_timestamp_only_difference_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "hi at 2026-01-01T00:00:00Z"})}
    new = {"n": NodeIO(input={}, output={"msg": "hi at 2026-06-15T12:30:00+00:00"})}
    assert diff_cassettes(base, new) == {}


def test_uuid_only_difference_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"id": "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"})}
    new = {"n": NodeIO(input={}, output={"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})}
    assert diff_cassettes(base, new) == {}


def test_real_content_change_is_still_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "hello"})}
    new = {"n": NodeIO(input={}, output={"msg": "goodbye"})}
    assert diff_cassettes(base, new) == {"n": "root"}


def test_ignore_paths_field_is_no_drift() -> None:
    base = {"n": NodeIO(input={}, output={"msg": "ok", "nonce": 0.1})}
    new = {"n": NodeIO(input={}, output={"msg": "ok", "nonce": 0.9})}
    assert diff_cassettes(base, new, ["/nonce"]) == {}
    assert diff_cassettes(base, new) == {"n": "root"}


def test_normalize_counted_reports_replacements() -> None:
    raw = {
        "id": "x",
        "msg": "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-01-01T00:00:00Z",
        "meta": {"nonce": 1},
    }
    out, n = normalize_counted(raw, ["/meta/nonce"])
    assert out == {
        "id": "<id>",
        "msg": "<uuid> at <timestamp>",
        "meta": {"nonce": "<ignored>"},
    }
    assert n == 4  # id + uuid + timestamp + ignored
