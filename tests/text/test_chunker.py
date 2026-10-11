from __future__ import annotations

from auris.text.chunker import split_into_chunks


def test_empty_text_returns_empty_list():
    assert split_into_chunks("") == []
    assert split_into_chunks("   \n  ") == []


def test_splits_into_sentences():
    chunks = split_into_chunks("First sentence here. Second sentence here.")
    assert chunks == ["First sentence here.", "Second sentence here."]


def test_handles_question_and_exclamation_and_newlines():
    chunks = split_into_chunks("Are you sure? Yes indeed! New\nline here.")
    assert chunks == ["Are you sure?", "Yes indeed!", "New", "line here."]


def test_short_sentence_is_its_own_chunk():
    # short sentences stay as their own chunk — no merging complexity
    chunks = split_into_chunks("This is a real sentence. Hi. Another real sentence here.")
    assert chunks == [
        "This is a real sentence.",
        "Hi.",
        "Another real sentence here.",
    ]


def test_long_sentence_is_capped_on_spaces():
    text = "word " * 100  # 500 chars, no sentence punctuation
    chunks = split_into_chunks(text.strip(), max_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)


def test_no_punctuation_single_short_line():
    assert split_into_chunks("hello there world") == ["hello there world"]


def test_single_word_longer_than_cap_is_emitted_whole():
    word = "a" * 60
    # we never split mid-word, so the chunk exceeds the cap rather than mangling the word
    assert split_into_chunks(word, max_chars=50) == [word]


def test_blank_lines_between_sentences_are_ignored():
    chunks = split_into_chunks("A real sentence.\n  \nAnother real sentence.")
    assert chunks == ["A real sentence.", "Another real sentence."]
