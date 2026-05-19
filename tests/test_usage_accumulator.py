"""Usage accumulation across LLM rounds."""

from __future__ import annotations

from seed.core.usage_accumulator import (
    begin_usage_accumulation,
    end_usage_accumulation,
    record_round_usage,
)


def test_accumulate_two_rounds() -> None:
    tok = begin_usage_accumulation()
    record_round_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    record_round_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    out = end_usage_accumulation(tok)
    assert out["prompt_tokens"] == 13
    assert out["completion_tokens"] == 7
    assert out["total_tokens"] == 20
