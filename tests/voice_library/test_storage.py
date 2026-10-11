from __future__ import annotations

import numpy as np

from auris.audio import io
from auris.voice_library import storage


def test_store_clip_writes_file_at_expected_path(tmp_path):
    audio = np.zeros(24000, dtype=np.float32)
    path = storage.store_clip(
        voices_dir=tmp_path, speaker_id="sp1", audio=audio, sample_rate=24000, index=1
    )
    assert path == tmp_path / "sp1" / "clips" / "clip_001.wav"
    assert path.exists()
    loaded, sr = io.load_audio(path)
    assert sr == 24000
    assert len(loaded) == 24000


def test_next_clip_index_counts_existing(tmp_path):
    audio = np.zeros(10, dtype=np.float32)
    assert storage.next_clip_index(tmp_path, "sp1") == 1
    storage.store_clip(tmp_path, "sp1", audio, 24000, 1)
    assert storage.next_clip_index(tmp_path, "sp1") == 2


def test_next_clip_index_after_deletion_avoids_collision(tmp_path):
    audio = np.zeros(10, dtype=np.float32)
    for i in (1, 2, 3):
        storage.store_clip(tmp_path, "sp1", audio, 24000, i)
    # delete the middle clip
    (storage.clips_dir(tmp_path, "sp1") / "clip_002.wav").unlink()
    # next index must be 4 (max existing 3 + 1), NOT 3 (which would collide)
    assert storage.next_clip_index(tmp_path, "sp1") == 4


def test_clip_index_parses_the_filename():
    assert storage.clip_index("clip_003.wav") == 3
    assert storage.clip_index("clip_012.wav") == 12


def test_remove_clip_deletes_the_file_and_tolerates_missing(tmp_path):
    d = storage.clips_dir(tmp_path, "sp")
    d.mkdir(parents=True)
    (d / "clip_001.wav").write_bytes(b"x")
    storage.remove_clip(tmp_path, "sp", "clip_001.wav")
    assert not (d / "clip_001.wav").exists()
    storage.remove_clip(tmp_path, "sp", "clip_001.wav")  # no raise
