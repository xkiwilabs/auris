from __future__ import annotations


def word_count(text: str) -> int:
    return max(1, len(text.split()))


def format_timing(elapsed_s: float, text: str) -> str:
    """A compact round-trip timing line: total wall time and time per input word."""
    n = word_count(text)
    plural = "" if n == 1 else "s"
    return f"{elapsed_s:.2f}s total · {elapsed_s / n:.3f}s/word ({n} word{plural})"
