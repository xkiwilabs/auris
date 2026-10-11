from __future__ import annotations

import numpy as np
import pytest

from auris.audio.reference import fit_reference
from auris.audio.transcript import Word

SR = 24000


def _speech(spec, sr=SR, floor=1e-4, seed=0):
    """Build a clip from [(kind, seconds, word-or-None), ...]: "s" is a tone burst standing in
    for a spoken word, "p" a pause of low room noise. Returns (audio, words) with the words'
    timestamps where the bursts actually are — a perfect transcript to fit against."""
    rng = np.random.default_rng(seed)
    parts, words, t = [], [], 0.0
    for kind, seconds, word in spec:
        n = int(seconds * sr)
        if kind == "s":
            tone = 0.3 * np.sin(2 * np.pi * 180 * np.arange(n) / sr)
            parts.append(tone.astype(np.float32))
            if word:
                words.append(Word(start=t, end=t + seconds, word=word, probability=1.0))
        else:
            parts.append((floor * rng.standard_normal(n)).astype(np.float32))
        t += seconds
    return np.concatenate(parts), words


def _is_speech(audio, at_s, sr=SR):
    """True when the 20 ms around `at_s` holds a burst rather than the noise floor."""
    i = int(at_s * sr)
    window = audio[max(i - sr // 100, 0) : i + sr // 100]
    return window.size and float(np.sqrt(np.mean(window**2))) > 0.01


def test_a_short_clip_is_kept_whole_with_a_little_silence_either_side():
    audio, words = _speech(
        [
            ("p", 1.0, None),
            ("s", 2.0, "Hello"),
            ("p", 0.3, None),
            ("s", 2.0, "there."),
            ("p", 1.5, None),
        ]
    )
    fit = fit_reference(audio, SR, words)
    assert fit.transcript == "Hello there."
    assert not fit.dropped_speech
    seconds = len(fit.audio) / SR
    # 0.15 s lead-in, 4.3 s of speech and pause, then up to 0.3 s of tail.
    assert 4.3 < seconds <= 4.3 + 0.15 + 0.3 + 0.02
    assert not _is_speech(fit.audio, 0.0)  # starts in silence, not on the first word
    assert not _is_speech(fit.audio, seconds - 0.01)  # ends in silence


def test_a_long_clip_is_cut_inside_a_pause_never_through_a_word():
    # A word straddles the 10 s cap (9.4–11.4 s). A blind cap would end mid-word, which is what
    # makes f5-TTS speak the rest of that word at the start of every generation.
    audio, words = _speech(
        [
            ("s", 3.0, "one"),
            ("p", 0.4, None),
            ("s", 3.0, "two"),
            ("p", 0.4, None),
            ("s", 2.2, "three"),
            ("p", 0.4, None),
            ("s", 2.0, "four"),
            ("p", 0.5, None),
        ]
    )
    fit = fit_reference(audio, SR, words, max_seconds=10.0)
    seconds = len(fit.audio) / SR
    assert seconds <= 10.0
    assert fit.dropped_speech
    assert fit.transcript == "one two three"
    assert not _is_speech(fit.audio, seconds - 0.01)
    assert _is_speech(fit.audio, seconds - 0.4)  # but kept right up to "three"


def test_speech_running_to_the_last_sample_is_backed_up_to_a_pause():
    # Under the cap, but the recording stopped mid-word (a 10 s auto-stop, or a clip stored by
    # the old blind cap): that last word is incomplete and must go.
    audio, words = _speech(
        [
            ("p", 0.5, None),
            ("s", 3.0, "Hi,"),
            ("p", 0.4, None),
            ("s", 3.0, "it's me."),
            ("p", 0.4, None),
            ("s", 1.2, "My"),
        ]
    )
    fit = fit_reference(audio, SR, words, max_seconds=10.0)
    assert fit.transcript == "Hi, it's me."
    assert fit.dropped_speech
    assert not _is_speech(fit.audio, len(fit.audio) / SR - 0.01)


def test_speech_with_no_quiet_after_it_is_backed_up_even_when_it_ends_softly():
    # A clip cut inside the soft "s" of "Science" ends quietly and still leaked the word in 22
    # of 24 generations: the level of an ending proves nothing, only quiet after it does.
    audio, words = _speech(
        [("p", 0.5, None), ("s", 3.0, "Hello there,"), ("p", 0.4, None), ("s", 2.0, "friend.")]
    )
    fade = np.linspace(1.0, 0.01, int(0.25 * SR), dtype=np.float32)
    audio[-len(fade) :] *= fade
    fit = fit_reference(audio, SR, words)
    assert fit.transcript == "Hello there,"
    assert fit.dropped_speech and not fit.abrupt_end


def test_an_abrupt_ending_with_no_earlier_pause_is_kept_but_flagged():
    audio, words = _speech(
        [
            ("p", 0.5, None),
            ("s", 1.0, "Hi,"),
            ("p", 0.3, None),
            ("s", 3.0, "talking right up to the end"),
        ]
    )
    fit = fit_reference(audio, SR, words)
    assert fit.abrupt_end and not fit.dropped_speech
    assert len(fit.audio) == len(audio) - int(0.35 * SR)  # lead-in trimmed, the end kept


def test_a_sentence_end_is_preferred_when_it_still_leaves_a_useful_clip():
    audio, words = _speech(
        [
            ("s", 3.5, "First"),
            ("s", 3.0, "sentence."),
            ("p", 0.4, None),
            ("s", 2.0, "and"),
            ("p", 0.3, None),
            ("s", 2.0, "then"),
            ("p", 0.3, None),
            ("s", 2.0, "more."),
        ]
    )
    fit = fit_reference(audio, SR, words, max_seconds=10.0)
    # The pause after "and" (≈9.2 s) is later, but ending on a full stop matches the ". " f5
    # appends to every reference transcript, and 6.5 s is still plenty of voice.
    assert fit.transcript == "First sentence."


def test_without_a_transcript_the_cut_still_lands_in_a_pause():
    # STT switched off and the caller typed the text: energy alone decides the cut.
    audio, _ = _speech(
        [("s", 4.0, None), ("p", 0.4, None), ("s", 4.0, None), ("p", 0.4, None), ("s", 4.0, None)]
    )
    fit = fit_reference(audio, SR, None, max_seconds=10.0)
    seconds = len(fit.audio) / SR
    assert fit.transcript is None
    assert fit.dropped_speech
    assert 8.4 < seconds <= 8.4 + 0.3 + 0.02  # 4 s + 0.4 s pause + 4 s, then the tail
    assert not _is_speech(fit.audio, seconds - 0.01)


def test_continuous_speech_with_no_pause_is_capped_and_flagged():
    audio, words = _speech([("s", 14.0, "endless")])
    fit = fit_reference(audio, SR, words, max_seconds=10.0)
    assert len(fit.audio) <= 10 * SR
    assert fit.forced  # there was nowhere clean to cut — the caller warns about it


def test_a_quiet_onset_is_not_shaved_off():
    # A soft first syllable 32 dB under the loudest word: librosa's top_db=30 trim (the old
    # prepare_reference) would have cut it off.
    quiet = (0.3 * 10 ** (-32 / 20) * np.sin(2 * np.pi * 180 * np.arange(SR // 2) / SR)).astype(
        np.float32
    )
    audio, words = _speech([("p", 1.0, None), ("s", 2.0, "loud"), ("p", 1.0, None)])
    audio = np.concatenate([audio[:SR], quiet, audio[SR:]])
    fit = fit_reference(audio, SR, None)
    assert len(fit.audio) >= int(2.5 * SR)


def test_pure_silence_comes_back_unchanged():
    audio = np.zeros(5 * SR, dtype=np.float32)
    fit = fit_reference(audio, SR, None)
    assert len(fit.audio) == len(audio)


@pytest.mark.parametrize("max_seconds", [6.0, 10.0])
def test_the_cap_is_never_exceeded(max_seconds):
    audio, words = _speech([("s", 0.8, "w"), ("p", 0.2, None)] * 20)
    fit = fit_reference(audio, SR, words, max_seconds=max_seconds)
    assert len(fit.audio) <= max_seconds * SR


# --- reference_for_clone: the decision both the server and the CLI make --------------------

from auris.audio.reference import clone_options, reference_for_clone  # noqa: E402
from auris.audio.transcript import Segment, Transcript  # noqa: E402


def _decoded(words, text=None):
    text = text if text is not None else " ".join(w.word for w in words)
    return Transcript(text=text, segments=(Segment(0, 0.0, 1.0, text, tuple(words)),))


_LONG = [
    ("s", 4.0, "One."),
    ("p", 0.4, None),
    ("s", 4.0, "Two."),
    ("p", 0.4, None),
    ("s", 4.0, "Three."),
]


def test_the_decoded_words_that_were_kept_become_the_transcript():
    audio, words = _speech(_LONG)
    ref = reference_for_clone(audio, SR, ref_text=None, decoded=_decoded(words))
    assert ref.transcript == "One. Two."
    assert len(ref.audio) <= 10 * SR


def test_a_typed_transcript_is_refused_when_the_cut_drops_speech():
    audio, _ = _speech(_LONG)
    with pytest.raises(ValueError, match="typed transcript would not match"):
        reference_for_clone(audio, SR, ref_text="One. Two. Three.", decoded=None)


def test_a_typed_transcript_is_kept_when_the_whole_recording_fits():
    audio, _ = _speech([("p", 0.5, None), ("s", 5.0, None), ("p", 0.5, None)])
    ref = reference_for_clone(audio, SR, ref_text="  Typed it myself. ", decoded=None)
    assert ref.transcript == "Typed it myself."


def test_a_decode_without_word_timings_is_used_only_if_nothing_was_cut():
    short, _ = _speech([("p", 0.5, None), ("s", 5.0, None), ("p", 0.5, None)])
    assert (
        reference_for_clone(short, SR, ref_text=None, decoded=_decoded([], "hi")).transcript == "hi"
    )
    long, _ = _speech(_LONG)
    with pytest.raises(ValueError, match="line the transcript up"):
        reference_for_clone(long, SR, ref_text=None, decoded=_decoded([], "One. Two. Three."))


def test_no_speech_is_an_error():
    with pytest.raises(ValueError, match="no speech"):
        reference_for_clone(
            np.zeros(5 * SR, np.float32), SR, ref_text=None, decoded=_decoded([], "")
        )


def test_an_abrupt_ending_comes_back_with_a_warning():
    audio, words = _speech(
        [
            ("p", 0.5, None),
            ("s", 1.0, "Hi,"),
            ("p", 0.3, None),
            ("s", 6.0, "talking right up to the end"),
        ]
    )
    ref = reference_for_clone(audio, SR, ref_text=None, decoded=_decoded(words))
    assert any("still speaking" in w for w in ref.warnings)


def test_a_short_or_forced_clip_comes_back_with_warnings():
    audio, words = _speech([("p", 0.3, None), ("s", 4.0, "Short."), ("p", 0.5, None)])
    ref = reference_for_clone(audio, SR, ref_text=None, decoded=_decoded(words))
    assert any("usually captures the voice" in w for w in ref.warnings)
    audio, words = _speech([("s", 14.0, "endless")])
    ref = reference_for_clone(audio, SR, ref_text=None, decoded=_decoded(words))
    assert any("no pause" in w for w in ref.warnings)


def test_cloning_decodes_english_without_vad_and_with_word_times():
    o = clone_options("small.en")
    assert (o.model, o.language, o.vad_filter, o.word_timestamps) == ("small.en", "en", False, True)


def test_extra_leading_words_finds_a_leaked_word_and_nothing_else():
    from auris.audio.reference import extra_leading_words

    asked = "Please remember to bring your laptop."
    assert extra_leading_words(asked, "Science. Please remember to bring your laptop.") == [
        "science"
    ]
    assert extra_leading_words(asked, "Please remember to bring your laptop") == []
    assert extra_leading_words(asked, "please, remember to bring the laptop.") == []  # a slip
    assert extra_leading_words(asked, "") == []


def test_the_decode_window_follows_a_late_start_so_the_transcript_covers_the_kept_audio():
    from auris.audio.reference import decode_window

    # 6 s of quiet (a slow start after the countdown), then 9.5 s of speech: the clip runs to
    # ~15.6 s, past a window that started at sample 0 and stopped at 15 s.
    audio, _ = _speech(
        [("p", 6.0, None), ("s", 4.5, None), ("p", 0.4, None), ("s", 4.6, None), ("p", 0.5, None)]
    )
    window = decode_window(audio, SR)
    fit = fit_reference(audio, SR, None)
    kept_until = 6.0 - 0.15 + len(fit.audio) / SR
    assert len(window) >= min(
        len(audio), int((kept_until + 1.0) * SR)
    )  # the old one stopped at 15 s


def test_a_typed_transcript_for_a_tightly_trimmed_short_file_is_kept_and_warned_about():
    # Trimmed tight in an editor: no quiet after the last word, but well under the cap. The text
    # describes the whole file, so the whole file is kept — with a warning, not a false refusal.
    audio, _ = _speech([("p", 0.3, None), ("s", 3.0, None), ("p", 0.4, None), ("s", 1.4, None)])
    ref = reference_for_clone(audio, SR, ref_text="one two three four", decoded=None)
    assert ref.transcript == "one two three four"
    assert len(ref.audio) >= len(audio) - int(0.2 * SR)
    assert any("still speaking" in w for w in ref.warnings)
