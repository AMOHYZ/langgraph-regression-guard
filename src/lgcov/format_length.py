"""T126: format/length drift monitor — budget vs prompt, response length, markdown density."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Relative length increase that counts as drift (e.g. +60% → 1.60× baseline).
_LENGTH_DRIFT_RATIO = 1.60
_MARKDOWN_MARKS = re.compile(r"[#*_`\[\]]|^\s*[-*] |\^\s*\d+\. ", re.MULTILINE)


@dataclass(frozen=True)
class ResponseShape:
    prompt_tokens: int  # chars/4 of the existing prompt (budget baseline)
    response_chars: int
    markdown_density: float  # markdown marks / response chars


def tokens_approx(text: str) -> int:
    """Approximate tokens as characters / 4 (no new tokenizer dependency)."""
    return len(text) // 4


def markdown_density(text: str) -> float:
    if not text:
        return 0.0
    return len(_MARKDOWN_MARKS.findall(text)) / len(text)


def measure_text(*, prompt: str, response: str) -> ResponseShape:
    return ResponseShape(
        prompt_tokens=tokens_approx(prompt),
        response_chars=len(response),
        markdown_density=markdown_density(response),
    )


def length_drifted(base: ResponseShape, head: ResponseShape) -> bool:
    """True when head response length is at least +60% vs base."""
    if base.response_chars <= 0:
        return head.response_chars > 0
    return head.response_chars >= base.response_chars * _LENGTH_DRIFT_RATIO


def format_length_reason(
    *, base: ResponseShape, head: ResponseShape, judge_only_gains: bool
) -> str:
    """Reason string when length drift AND judge-only gains; else empty (no flag).

    needs_human only when both hold; never exit 1 from this alone.
    """
    if not judge_only_gains or not length_drifted(base, head):
        return ""
    base_n = base.response_chars
    head_n = head.response_chars
    pct = ((head_n / base_n) - 1.0) * 100.0 if base_n else float("inf")
    return (
        f"format/length drift: response {base_n}→{head_n} chars "
        f"(+{pct:.0f}%) with judge-only gains "
        f"(prompt budget≈{head.prompt_tokens} tokens; "
        f"markdown density {base.markdown_density:.3f}→{head.markdown_density:.3f})"
    )
