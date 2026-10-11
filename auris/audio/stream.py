from __future__ import annotations

import queue
import threading
from collections.abc import Iterator

import numpy as np

from auris.models.base import PreparedReference, TTSModel
from auris.text.chunker import split_into_chunks

_DONE = object()  # sentinel marking the worker finished cleanly


def stream_synthesis(
    model: TTSModel, text: str, reference: PreparedReference, speed: float = 1.0
) -> Iterator[np.ndarray]:
    """Yield synthesised audio chunks (float32 mono) in order, as each is generated.

    Text is split into sentence chunks; a background worker generates the next chunks
    ahead of the consumer (bounded queue), so while one chunk plays the next is already
    being synthesised. An exception in the worker is re-raised to the consumer so a
    failed chunk surfaces cleanly instead of hanging. On any exit — exhaustion, error,
    or early consumer close — the worker is cancelled and the queue drained so the
    background thread can't block forever on a full queue."""
    chunks = split_into_chunks(text)
    if not chunks:
        return

    q: queue.Queue[object] = queue.Queue(maxsize=2)
    cancel = threading.Event()

    def _worker() -> None:
        try:
            for chunk_text in chunks:
                if cancel.is_set():
                    return
                audio = model.synthesise(chunk_text, reference, speed)
                if cancel.is_set():  # consumer went away while we synthesised
                    return
                q.put(audio)
        except Exception as exc:  # re-raised to the consumer below — not suppressed
            q.put(exc)
        else:
            q.put(_DONE)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    try:
        while True:
            item = q.get()
            if item is _DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        # Stop the worker and unblock any pending q.put() so join() can't hang,
        # whether we exited by exhaustion, error, or an early consumer close().
        cancel.set()
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
        thread.join()
