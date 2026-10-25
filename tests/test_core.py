from lgcov.core import coverage


def test_coverage() -> None:
    assert coverage({"a"}, {"a", "b"}) == 0.5
    assert coverage(set(), set()) == 1.0
