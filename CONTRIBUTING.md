# Contributing to auris

Thanks for your interest in auris! This guide covers how to set up, test, and submit changes.

auris is **local-first and fully offline** — that constraint shapes everything below. Please keep it
that way: no telemetry, no network calls in the core path, and audio never leaves the machine.

## Project shape (read this first)

- **Layered, model-behind-an-interface.** The CLI and HTTP service are thin layers over a pure
  core; models implement a small `TTSModel` interface so they can be swapped or mocked.
- **The dependency tiers are load-bearing.** The core library, CLI, and the *entire fast test
  suite* run with **no heavy ML dependencies**. This is enforced by **lazy imports**, not just
  packaging: `torch`, `f5_tts`, `faster_whisper`, `kokoro`, and `sounddevice` are imported *inside*
  the methods that use them, never at module top level. If you add a feature that needs one of
  these, **keep the import lazy** and raise a clear "install the X extra" error if it's missing.
  Putting any of these at module top level breaks `pytest` and every headless install.

See [docs/architecture.md](docs/architecture.md) for the full architecture and conventions.

## Development setup

```bash
git clone <repo-url> auris && cd auris
./scripts/install.sh            # dev + audio deps into .venv (NO heavy model)
source .venv/bin/activate
```

Add `--model` to also install the model runtime (torch, f5-TTS, faster-whisper) if you need to run
real inference locally. FFmpeg is a runtime dependency (`brew install ffmpeg` / `sudo apt install
ffmpeg`); espeak-ng is needed for the Kokoro preset voices.

## Tests

```bash
pytest                          # fast suite — runs WITHOUT model weights or a GPU
pytest -m requires_model        # opt-in real-inference tests (need the model extras + a sample)
pytest tests/cli/test_tts.py    # a single file
```

The default run excludes `requires_model` (configured in `pyproject.toml`), so the standard suite
never downloads weights or needs a GPU. **Keep it that way** — new code must be covered by
model-free tests; gate anything needing real weights behind `@pytest.mark.requires_model`.

Testing patterns to follow (see existing tests):
- CLI/server tests `monkeypatch` `build_model` to return a fake model — never load the real one.
- Use the `cli_data_dir` fixture for an isolated `data/` dir; connect DB tests to `:memory:`.
- Audio/transcription tests mock the lazy imports.

We practice **TDD**: write a failing test, make it pass, refactor. New behaviour should arrive with
the test that proves it.

## Code style

- **Ruff** handles linting and formatting — line length 100, target `py311`.
- Run before every commit:
  ```bash
  ruff check . && ruff format .
  ```
- Match the surrounding code: its naming, comment density, and idioms. Comments explain *why*, not
  *what*.

## Commits & pull requests

1. **Branch off `main`** — don't commit directly to it.
2. Keep each PR to **one logical change**. Smaller is easier to review.
3. Before opening a PR: **tests pass** (`pytest`) and **lint is clean** (`ruff check . && ruff
   format .`).
4. Write a clear description: **what** changed and **why**. Reference any issue it closes.
5. Commit messages: a concise imperative summary line (`feat:`/`fix:`/`docs:`/`test:` prefixes are
   welcome), with a body explaining the reasoning for non-trivial changes.

For larger or design-affecting work, open an issue first that describes the approach and
trade-offs — it saves review churn.

## Reporting bugs & requesting features

Open an issue using the templates. For bugs, include your OS, how you installed (CLI vs service,
with/without `--model`), the exact command, and the full error output. A minimal reproduction is the
single most helpful thing you can provide.

## Licensing

By contributing, you agree your contributions are licensed under the repository's **MIT** license
(see [LICENSE](LICENSE)). Note the *model weights* keep their own licenses (see [NOTICE](NOTICE)) —
f5-TTS is non-commercial; auris is for research and personal use.
