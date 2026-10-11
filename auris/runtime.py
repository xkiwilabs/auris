from __future__ import annotations

import os


def ensure_valid_hashseed() -> None:
    """Normalize PYTHONHASHSEED so spawned interpreters don't abort at startup.

    CPython requires PYTHONHASHSEED to be unset, "random", or an int in
    [0, 2**32-1]. Some shells/tools export it as an empty string, which makes any
    freshly-spawned interpreter (e.g. f5-TTS's multiprocessing workers) die with
    `Fatal Python error: config_init_hash_seed`. If it's set to something invalid,
    reset it to a stable value so child processes inherit a usable one."""
    value = os.environ.get("PYTHONHASHSEED")
    if value is None or value == "random" or value.isdigit():
        return
    os.environ["PYTHONHASHSEED"] = "0"
