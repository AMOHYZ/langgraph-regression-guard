"""T126: format/length drift + judge-only gains → needs_human; never exit 1."""

from __future__ import annotations

from lgcov.format_length import (
    ResponseShape,
    format_length_reason,
    length_drifted,
    markdown_density,
    measure_text,
    tokens_approx,
)
from lgcov.verdict_actions import Evidence, decide_action


def test_tokens_are_chars_over_four() -> None:
    assert tokens_approx("abcd") == 1
    assert tokens_approx("a" * 40) == 10


def test_plus_sixty_percent_response_length_with_judge_only_gains_is_needs_human() -> None:
    base = ResponseShape(prompt_tokens=100, response_chars=100, markdown_density=0.05)
    head = ResponseShape(prompt_tokens=100, response_chars=160, markdown_density=0.05)
    assert length_drifted(base, head) is True
    reason = format_length_reason(base=base, head=head, judge_only_gains=True)
    assert reason  # non-empty
    action = decide_action(Evidence(statistical="SHIP", k_gate=1, format_length_drift=reason))
    assert action.state == "format_length_drift"
    assert action.verdict == "needs_human"
    assert action.exit_code == 0  # never exit 1 from format/length alone


def test_length_drift_alone_does_not_flag() -> None:
    base = ResponseShape(prompt_tokens=50, response_chars=100, markdown_density=0.1)
    head = ResponseShape(prompt_tokens=50, response_chars=200, markdown_density=0.1)
    assert length_drifted(base, head) is True
    assert format_length_reason(base=base, head=head, judge_only_gains=False) == ""


def test_judge_only_gains_alone_does_not_flag() -> None:
    base = ResponseShape(prompt_tokens=50, response_chars=100, markdown_density=0.1)
    head = ResponseShape(prompt_tokens=50, response_chars=105, markdown_density=0.1)
    assert length_drifted(base, head) is False
    assert format_length_reason(base=base, head=head, judge_only_gains=True) == ""


def test_measure_text_reports_prompt_tokens_and_markdown_density() -> None:
    prompt = "x" * 40  # 10 tokens
    response = "# Title\n\n**bold** and more text"
    shape = measure_text(prompt=prompt, response=response)
    assert shape.prompt_tokens == 10
    assert shape.response_chars == len(response)
    assert shape.markdown_density == markdown_density(response)
    assert shape.markdown_density > 0.0
