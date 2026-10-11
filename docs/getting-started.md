# Getting started — auris on your own computer

This guide is for running auris **on your own machine**: install it, try a built-in voice, then
clone your own and hear it speak. It uses the `auris` command line. Plan for ~15 minutes, most of
which is a one-time model download.

> Covers macOS (Apple Silicon) and Ubuntu Linux. auris runs fully offline after install —
> the only downloads are the Python packages and the model weights, fetched once.

> **Just want to use a shared service someone else is running?** You don't need any of this —
> see **[remote-users.md](remote-users.md)** (open the dashboard in a browser, or run the
> examples with a tiny client setup).

---

## 1. Install the prerequisites

| Requirement | Why | macOS | Ubuntu |
|-------------|-----|-------|--------|
| **FFmpeg** | f5-TTS decodes audio through it — `tts` fails without it | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| **Python 3.11+** or **uv** | runs auris | `brew install python@3.11`, or install [uv](https://docs.astral.sh/uv/getting-started/installation/) | `sudo apt install python3.11 python3.11-venv`, or uv |
| **Git** | clone the repo | `brew install git` | `sudo apt install git` |
| **A microphone** | for `auris record` | built-in | any input device |
| **~2 GB free disk** | f5-TTS weights | — | — |

> **uv is the easy path.** If you install `uv`, you do **not** need to install Python
> yourself — the installer script uses uv to fetch Python 3.11 automatically.

Verify FFmpeg is on your PATH (this is the dependency most likely to be missing):

```bash
ffmpeg -version    # should print version info, not "command not found"
```

---

## 2. Get the code

```bash
git clone <repo-url> auris
cd auris
```

---

## 3. Install auris

```bash
./scripts/install.sh --model
```

This creates a virtual environment at `.venv/` and installs auris, the audio I/O extra
(microphone + playback), and the model runtime (PyTorch + f5-TTS). The `--model` flag is
what pulls the heavy pieces; omit it for a quick install without voice synthesis, and add
it later by re-running the same command.

If the script warns that `ffmpeg` is missing, go back to step 1 — `tts` will not work
without it.

Activate the environment (do this in every new terminal):

```bash
source .venv/bin/activate
```

Check it's wired up:

```bash
auris --help
```

---

## 4. Try a built-in voice first (no cloning)

Before cloning anything, hear auris work with a ready-made voice:

```bash
auris voices                                                      # list built-in + cloned voices
auris tts narrator-uk-f --text "Hello from auris." --out demo.wav # a built-in voice
auris play demo.wav
```

The first run downloads the Kokoro voice model (small, one time). Built-in voices need no
recording — they're the fastest way to confirm everything works. Now let's make *your* voice.

---

## 5. Clone your voice

```bash
auris record "Me"
```

This prints a short passage (the Rainbow Passage) and records ~14 seconds from your
default microphone. **Read the passage aloud, clearly and at a natural pace.** When it
finishes, your recording is transcribed, your voice is cloned, and it's stored under the
speaker name "Me".

- **First run, macOS:** your terminal will ask for **microphone permission**. Approve it
  (System Settings → Privacy & Security → Microphone), then run the command again.
- If the clip records silence, see [troubleshooting](troubleshooting.md#recording-is-silent).

The passage is just guidance for good phonetic coverage — auris **transcribes whatever you
actually say** (so the transcript always matches the audio, which f5-TTS needs). You can
read your own text with `--passage "…"`, or say anything with `--free`.

---

## 6. Generate speech and listen

```bash
auris tts "Me" --text "Hello, this is my cloned voice." --out hi.wav
auris play hi.wav
```

- The **first** `auris tts` downloads the f5-TTS model (~1.4 GB). This happens once;
  later runs are fast.
- `auris play` plays the file through your speakers on any platform.

That's the full loop: **record → clone → generate → play.**

---

## 7. (Optional) Clone from an existing recording

If you already have an audio file of a voice, you can clone from it directly — but you
must supply its **exact** transcript (f5-TTS conditions on it):

```bash
auris speakers add "Alice"
auris clone "Alice" --ref alice.wav --ref-text "the exact words spoken in alice.wav"
auris tts "Alice" --text "Testing one two three." --out alice.wav
auris play alice.wav
```

The reference must be at least 3 seconds of clear speech; ~10–15 seconds is ideal.

---

## Where your data lives

Everything stays on disk under `./data/` (created on first use):

```
data/
├── auris.db                       # speakers + reference-clip records (SQLite)
└── voices/<speaker-id>/clips/     # your reference WAVs
```

Point auris elsewhere with `AURIS_DATA_DIR=/path/to/data`. Generated output goes wherever
you pass `--out`. See [usage.md](usage.md#configuration) for all settings.

---

## Next steps

- **[usage.md](usage.md)** — every command and option in detail.
- **[troubleshooting.md](troubleshooting.md)** — fixes for the common snags.
- **Run it as a service for a team** — to serve cloned-voice TTS over HTTP (so others, or your
  research code/agents, can use it over a private network), see the
  [Running as a service](../README.md#running-as-a-service-auris-server) section in the README
  and **[deploy/README.md](deploy/README.md)** (scripted setup on a GPU box).
- Hit a wall? The errors auris prints are intentionally actionable; the troubleshooting
  guide expands on each.
