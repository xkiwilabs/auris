from __future__ import annotations

import re

# Split at whitespace that follows a sentence terminator, or at newlines.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]


def _cap(sentence: str, max_chars: int) -> list[str]:
    """Hard-split a sentence longer than max_chars on word boundaries."""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: list[str] = []
    buf = ""
    for word in sentence.split():
        if buf and len(buf) + 1 + len(word) > max_chars:
            pieces.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def split_into_chunks(text: str, max_chars: int = 200) -> list[str]:
    """Split text into sentence-sized chunks for streaming synthesis.

    Each sentence (and newline-separated segment) becomes one chunk, so audio can start
    after the first sentence. A sentence longer than max_chars is hard-split on word
    boundaries; a single word longer than max_chars is emitted whole (never split
    mid-word, to preserve pronunciation), so a chunk can exceed max_chars in that case.
    Returns [] for empty/whitespace-only input."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    for sentence in _split_sentences(text):
        chunks.extend(_cap(sentence, max_chars))
    return chunks
