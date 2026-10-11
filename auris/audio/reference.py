"""Fit an upload to a cloning reference: at most `max_seconds`, cut only inside a pause.

**Why this exists.** f5-TTS generates the new speech as a continuation of the reference, so a
reference that ends part-way through a word makes it finish that word at the start of the
output — the "same stray word every time" glitch. Measured on a real voice with the same seeds
and text: the intact clip leaked its last word in 1 of 24 generations, the same clip with its
final 0.15 s removed in 22 of 24. Trailing silence made no difference either way; only an
incomplete word did. So the cut is placed by the *audio* (a run of quiet frames), never by
a blind cap or by Whisper's word end times, which run early and would clip the word anyway.
And an ending only counts as finished when quiet follows it: how loud the last sound is says
nothing about whether the word was complete.

The transcript comes from the same word-timestamped decode the caller ran on this audio, so
the stored text covers exactly the words that were kept. Pure numpy: no model imports.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from auris.audio.transcript import TranscribeOptions, Transcript, Word

FRAME_S = 0.01  # energy frame
LEAD_S = 0.15  # silence kept before the first word, so a soft onset is never shaved
TAIL_S = 0.3  # silence kept after the last word
QUIET_TAIL_S = 0.1  # an ending needs this much quiet after the last sound to count as finished
MIN_PAUSE_S = 0.15  # shorter gaps are stop consonants and breaths inside a phrase
MIN_KEEP_S = 3.0  # matches the server's minimum reference length
SENTENCE_FROM_S = 6.0  # a full stop wins over a later pause only if it keeps this much
_SENTENCE_END = ".!?"


@dataclass(frozen=True)
class FittedReference:
    audio: np.ndarray  # float32 mono, same rate as the input
    transcript: str | None  # the kept words; None when no words were given
    dropped_speech: bool  # the cut removed speech past the cap (a typed transcript won't fit)
    forced: bool  # no usable pause before the cap: cut at the quietest point instead
    abrupt_end: bool = False  # still ends with no quiet after it: no pause late enough to back to


def _frame_db(audio: np.ndarray, sr: int) -> np.ndarray:
    hop = max(int(sr * FRAME_S), 1)
    n = len(audio) // hop
    frames = audio[: n * hop].astype(np.float64).reshape(n, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames**2, axis=1)) + 1e-10)


def _speech_mask(db: np.ndarray) -> np.ndarray | None:
    """Per-frame "is this speech". None when the clip is effectively silent.

    The threshold sits between the noise floor (3rd percentile — a fast talker may leave under
    a tenth of the clip quiet) and the speech level (95th): 8 dB over the floor, but never more
    than 35 dB under the speech — wide enough to keep a soft onset that a peak-relative 30 dB
    trim would cut."""
    if db.size == 0 or db.max() < -60:
        return None
    floor, level = np.percentile(db, 3), np.percentile(db, 95)
    if level - floor < 10:  # no quiet frames at all: one unbroken stretch of sound
        return np.ones_like(db, dtype=bool)
    return db > max(floor + 8, level - 35)


def _pauses(mask: np.ndarray) -> list[tuple[float, float]]:
    """(start, end) seconds of each quiet run of at least MIN_PAUSE_S between speech."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            i += 1
            continue
        j = i
        while j < n and not mask[j]:
            j += 1
        if 0 < i and j < n and (j - i) * FRAME_S >= MIN_PAUSE_S:
            out.append((i * FRAME_S, j * FRAME_S))
        i = j
    return out


def _words_between(words: Sequence[Word], start: float, end: float) -> str:
    return " ".join(w.word.strip() for w in words if start <= (w.start + w.end) / 2 < end).strip()


def _ends_sentence(words: Sequence[Word], pause: tuple[float, float]) -> bool:
    # Whisper's word end times run early, so look a little before the pause starts.
    ps, pe = pause
    return any(w.word.rstrip()[-1:] in _SENTENCE_END and ps - 0.6 <= w.end <= pe for w in words)


def fit_reference(
    audio: np.ndarray,
    sample_rate: int,
    words: Sequence[Word] | None,
    max_seconds: float = 10.0,
    keep_ending: bool = False,
) -> FittedReference:
    """Trim to a short lead-in, then end inside the latest pause before `max_seconds`.

    `words` are word timestamps for this same audio (same time base); pass None when there is
    no transcription (STT off and the caller typed the text), and the cut is chosen by energy
    alone. `keep_ending` keeps an abrupt ending under the cap (flagged, not cut back): a typed
    transcript describes the whole file, so cutting it would make the text wrong instead. The cap stays safely under f5-TTS's ~12 s internal re-clip, which keeps the whole
    transcript and so leaks words too — see the 10 s gotcha in docs/architecture.md."""
    audio = audio.astype(np.float32)
    sr = sample_rate
    db = _frame_db(audio, sr)
    mask = _speech_mask(db)
    if mask is None or not mask.any():  # nothing to anchor on: keep the clip as it is
        text = _words_between(words, 0.0, float("inf")) if words else None
        return FittedReference(audio[: int(max_seconds * sr)], text, False, False)

    speech = np.flatnonzero(mask)
    first, last = speech[0] * FRAME_S, (speech[-1] + 1) * FRAME_S
    total = len(audio) / sr
    start = max(first - LEAD_S, 0.0)
    limit = min(start + max_seconds, total)
    forced = False

    # A recording that stops with no quiet after its last sound stopped while someone was
    # talking — a timer, or a file trimmed tight — so nothing says its last word finished. How
    # loud that ending is proves nothing: a clip cut inside the quiet "s" of "Science" ends softly
    # and still leaks the word in most generations. So it is backed up to its last pause too.
    cut_off = total - last < QUIET_TAIL_S
    abrupt = False
    within = last <= limit + FRAME_S / 2  # frame times are float multiples: 510 × 0.01 > 5.1
    if within and (not cut_off or keep_ending):
        end, abrupt = min(last + TAIL_S, limit), cut_off
    else:
        candidates = []
        for pause in _pauses(mask):
            ps, pe = pause
            end = min(ps + TAIL_S, pe - FRAME_S, limit)
            if ps >= limit or end - start < MIN_KEEP_S:
                continue
            candidates.append((end, bool(words) and _ends_sentence(words, pause)))
        sentences = [c for c in candidates if c[1] and c[0] - start >= SENTENCE_FROM_S]
        if candidates:
            end = max(sentences or candidates)[0]
        elif within:  # under the cap, but no pause late enough to keep MIN_KEEP_S
            end, abrupt = total, True
        else:  # unbroken speech up to the cap: the quietest frame in its last two seconds
            lo = int(max(limit - 2.0, start + MIN_KEEP_S) / FRAME_S)
            hi = max(int(limit / FRAME_S), lo + 1)
            end = min((lo + int(np.argmin(db[lo:hi]))) * FRAME_S, limit) if hi <= len(db) else limit
            forced = True

    clip = audio[int(start * sr) : int(end * sr)]
    text = _words_between(words, start, end) if words else None
    return FittedReference(clip, text, end < last - FRAME_S, forced, abrupt)


def decode_window(audio: np.ndarray, sample_rate: int, max_seconds: float = 10.0) -> np.ndarray:
    """The prefix of an upload worth transcribing before `fit_reference`: up to where speech
    starts, plus the cap, plus a few seconds of slack for the word straddling the cut. Measured
    from the speech onset, not sample 0 — a slow start after the recorder's countdown would
    otherwise leave the end of the kept clip undecoded, and its transcript short. Still starts at
    sample 0, so word timestamps share the full upload's time base."""
    mask = _speech_mask(_frame_db(audio.astype(np.float32), sample_rate))
    onset = float(np.argmax(mask)) * FRAME_S if mask is not None and mask.any() else 0.0
    return audio[: int((onset + max_seconds + 5.0) * sample_rate)]


def clone_options(model: str | None = None) -> TranscribeOptions:
    """Cloning's decode: English, no VAD, word timestamps (fit_reference cuts against them).

    English and no VAD on purpose: an accented English reference that auto-detected as another
    language would degrade f5's conditioning, and the VAD can drop a leading word and
    desynchronise audio from transcript. The STT surfaces auto-detect instead."""
    return TranscribeOptions(model=model, language="en", vad_filter=False, word_timestamps=True)


@dataclass(frozen=True)
class CloneReference:
    audio: np.ndarray
    transcript: str
    warnings: tuple[str, ...]


GOOD_LENGTH_S = 6.0  # under this the clone still works, but carries less of the voice


def reference_for_clone(
    audio: np.ndarray,
    sample_rate: int,
    *,
    ref_text: str | None,
    decoded: Transcript | None,
    max_seconds: float = 10.0,
) -> CloneReference:
    """The one place a clone's reference clip and transcript are decided — server and CLI alike.

    `ref_text` is a transcript the caller typed; `decoded` the `clone_options` decode of
    `decode_window(audio)`, used when no text was typed. A typed transcript describes the whole
    upload, so it is refused when the cut drops speech rather than stored against audio it no
    longer matches — that mismatch is exactly what makes f5 speak stray words."""
    words = [w for seg in decoded.segments for w in seg.words] if decoded else []
    typed = bool(ref_text and ref_text.strip())
    fit = fit_reference(audio, sample_rate, words or None, max_seconds, keep_ending=typed)
    kept = len(fit.audio) / sample_rate
    if typed:
        if fit.dropped_speech:  # only past the cap: keep_ending never backs an ending off
            raise ValueError(
                f"the recording has speech past {max_seconds:.0f} s, so only its first "
                f"{kept:.1f} s is kept and a typed transcript would not match it — leave the "
                f"transcript empty to have the kept part transcribed, or send at most "
                f"{max_seconds:.0f} s"
            )
        text = ref_text.strip()
    elif words:
        text = fit.transcript or ""
    else:  # a decode without word timings: its text is only right if nothing was cut away
        if decoded is not None and decoded.text.strip() and fit.dropped_speech:
            raise ValueError(
                "could not line the transcript up with the audio — send at most "
                f"{max_seconds:.0f} s, or type the transcript"
            )
        text = decoded.text.strip() if decoded is not None else ""
    if not text:
        raise ValueError("no speech detected in the reference audio")
    warnings = []
    if fit.abrupt_end:
        warnings.append(
            "The recording stops while you're still speaking, and there was no earlier pause to "
            "end on, so its last word may be cut off — which can add a stray sound at the start "
            "of generated speech. Record again and leave a second of silence before stopping."
        )
    if fit.forced:
        warnings.append(
            f"There was no pause before {max_seconds:.0f} s, so the clip was cut mid-phrase and "
            "may add a stray sound at the start of generated speech. Record again with a short "
            "pause between sentences."
        )
    if kept < GOOD_LENGTH_S:
        warnings.append(
            f"The clip is {kept:.1f} s. Between {GOOD_LENGTH_S:.0f} and {max_seconds:.0f} s of "
            "speech usually captures the voice more faithfully."
        )
    return CloneReference(fit.audio, text, tuple(warnings))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def extra_leading_words(asked: str, heard: str) -> list[str]:
    """Words a transcription of generated speech has *before* the text that was asked for — the
    signature of a leaking reference ("Science. Please remember…" for "Please remember…")."""
    a, h = _words(asked), _words(heard)
    blocks = [
        b for b in difflib.SequenceMatcher(a=a, b=h, autojunk=False).get_matching_blocks() if b.size
    ]
    return h[: blocks[0].b] if blocks else h
