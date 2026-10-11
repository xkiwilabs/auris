# auris troubleshooting

Fixes for the snags you're most likely to hit. Each entry shows the symptom, the cause,
and the fix.

---

## Cloned voice is too fast, or has extra words at the start

**Symptom** — generated speech is rushed, and/or begins with words you didn't ask for
(often the tail of what you said while recording the reference).

**Cause** — the reference clip's **transcript doesn't match its audio**. f5-TTS requires
them to match. This happened with older clips where the fixed passage was stored as the
transcript but you said something different (e.g. an intro before the passage).

**Fix** — re-record or re-clone; auris now **transcribes the recording automatically**, so
the transcript always matches:

```bash
auris record "Me"                 # or: auris record "Me" --free
auris clone "Me" --ref my.wav     # --ref-text now optional (auto-transcribed)
```

(Old mismatched clips stay in the database; just record again to add a correct one — `tts`
uses the most recent clip.)

---

## `tts` fails with libtorchcodec / libavutil

**Symptom** — `auris tts` ends in a long traceback containing:

```
RuntimeError: Could not load libtorchcodec
Library not loaded: @rpath/libavutil.60.dylib  (no such file)
```

(often preceded by `pydub: Couldn't find ffmpeg or avconv`).

**Cause** — **FFmpeg is not installed.** f5-TTS decodes the reference audio through
torchaudio, which needs FFmpeg's shared libraries (`libavutil`, …).

**Fix** — install FFmpeg, then re-run the same command:

```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Ubuntu
```

The model is already downloaded, so the retry is quick. Verify with `ffmpeg -version`.

---

## Recording is silent

**Symptom** — `auris record` completes but the cloned voice is empty/garbled, or the clip
is silent.

**Cause** — your terminal app doesn't have microphone permission, or the wrong input
device is selected.

**Fix (macOS)** — System Settings → Privacy & Security → Microphone → enable your terminal
app (Terminal, iTerm, VS Code, …). Quit and reopen the terminal, then record again.

**Fix (any OS)** — check your system's input device and level, and confirm the default
input is the mic you're speaking into.

---

## `command not found: auris`

**Cause** — the virtual environment isn't active in this shell.

**Fix**:

```bash
source .venv/bin/activate
```

You need to do this in every new terminal. Confirm with `which auris`.

---

## `command not found: pip`

**Cause** — the `.venv` was created by `uv`, which doesn't install `pip` by default.

**Fix** — use the installer (recommended), or `uv pip`:

```bash
./scripts/install.sh --model
# or, into an existing venv:
uv pip install --python .venv -e ".[dev,audio,model]"
```

---

## `recording/playback needs the 'audio' extra`

**Cause** — `sounddevice` / PortAudio isn't installed (you did a core-only install).

**Fix**:

```bash
pip install -e ".[audio]"      # or re-run scripts/install.sh
sudo apt install libportaudio2 # Linux only, if import still fails
```

---

## `speaker 'X' not found`

**Cause** — no speaker by that name. Names are case-insensitive but must otherwise match.

**Fix** — create it first, or use `auris record` (which creates it for you):

```bash
auris speakers add "X"
# then: auris clone "X" --ref ... --ref-text "..."
```

---

## `X has no reference clip — run 'auris clone' first`

**Cause** — the speaker exists but has no recording yet, so there's nothing to synthesise
from.

**Fix** — add a reference with `auris record "X"` or `auris clone "X" --ref ...`.

---

## `reference audio must be at least 3 seconds`

**Cause** — the reference clip is too short for a usable clone.

**Fix** — provide at least 3 seconds; aim for ~10–15 seconds of clear, continuous speech.

---

## Model download is slow / `unauthenticated requests to the HF Hub`

**Cause** — the first `tts` downloads ~1.4 GB from Hugging Face; the warning about
unauthenticated requests is informational (you may hit rate limits on a shared network).

**Fix** — let it finish once; it's cached afterwards. Optionally set a token to raise rate
limits:

```bash
export HF_TOKEN=hf_...
```

---

## `Fatal Python error: config_init_hash_seed` during tts

**Symptom** — `auris tts` prints `Wrote <file>.wav`, then a
`Fatal Python error: config_init_hash_seed: PYTHONHASHSEED must be ...` and
`resource_tracker: process died unexpectedly`.

**Cause** — `PYTHONHASHSEED` is set to an invalid value (often an empty string) in your
shell; f5-TTS's multiprocessing workers abort on it during teardown. **Your audio is
already written and fine** — this happens after the file is saved.

**Fix** — auris normalizes this automatically now. If you still see it (e.g. when running
the model outside auris), set a valid value:

```bash
export PYTHONHASHSEED=0
```

Run `auris info` to confirm your device; the crash does not affect output.

---

## Slow or failing on Apple Silicon (MPS)

**Symptom** — very slow synthesis, or an MPS-related error.

**Fix** — allow CPU fallback for unsupported ops, or force a device:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 auris tts "Me" --text "..." --out hi.wav
AURIS_DEVICE=cpu auris tts "Me" --text "..." --out hi.wav   # force CPU
```

auris auto-detects CUDA → MPS → CPU; `AURIS_DEVICE` overrides it.

---

## `auris play` produces no sound

**Cause** — output routed to the wrong device, or volume muted.

**Fix** — check your system's output device and volume. As a fallback you can still use a
system player: `afplay hi.wav` (macOS) or `aplay hi.wav` (Linux).

---

## GPU service: `CUDA error: no kernel image is available` (recent GPU)

**Symptom** — on a recent card (RTX PRO 6000 **Blackwell**, RTX 50-series), torch loads but any
GPU compute fails: `RuntimeError: CUDA error: no kernel image is available for execution on the
device`.

**Cause** — the installed PyTorch predates your GPU's architecture (Blackwell = `sm_120`).
Default/older torch wheels only ship kernels up to `sm_90`. (Installing some packages — e.g. an
old vLLM — can silently pin an old torch and trigger this.)

**Fix** — install a torch built for a newer CUDA. `scripts/setup-workstation.sh` does this for you
(cu128 by default). Manually:

```bash
uv pip install --python .venv torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -c "import torch; print(torch.cuda.is_available()); torch.randn(8, device='cuda').sum()"
```

For an older driver that can't do CUDA 12.8, use `.../whl/cu124` instead.

---

## `No module named pip` when running an install/setup script

**Symptom** — setup fails with `.venv/bin/python: No module named pip`.

**Cause** — you ran the script with **`sudo`**. Under sudo the PATH loses `~/.local/bin`, so `uv`
isn't found and the script falls back to `python -m pip` — but a `uv`-created venv has no `pip`.

**Fix** — run setup **without** `sudo`. The service scripts call `sudo` only for the systemd
parts that need it.

---

## Service crash-loops: `address already in use`

**Symptom** — `scripts/service.sh status` shows `activating (auto-restart)` /
`status=1/FAILURE`; the logs end with `[Errno 98] ... address already in use`.

**Cause** — another auris-server instance (often a leftover foreground/`nohup` run) already holds
the bind address + port.

**Fix** — stop the stray instance, then restart:

```bash
pkill -f "venv/bin/auris-server"      # kills foreground/nohup instances
scripts/service.sh restart
scripts/service.sh status             # should be 'active (running)', restart count steady
```

---

## Service won't start: `AURIS_AUTH_TOKEN must be set`

**Symptom** — `auris-server` exits immediately at startup.

**Cause** — the server refuses to run without an auth token. `scripts/setup-workstation.sh`
generates one into `.env`; if you skipped that, the variable is empty.

**Fix** — set `AURIS_AUTH_TOKEN` in `.env` (or the systemd EnvironmentFile). Read the current one
with `grep AURIS_AUTH_TOKEN .env`.

---

## Preset (Kokoro) voice fails: `espeak-ng` not found

**Symptom** — synthesising with a built-in preset voice (e.g. `narrator-uk-f`) errors, often
mentioning `espeak`, `phonemizer`, or a missing G2P backend.

**Cause** — Kokoro needs the **espeak-ng** system package for grapheme-to-phoneme conversion (the
same class of dependency as ffmpeg). The `kokoro` pip extra alone isn't enough.

**Fix**:

```bash
sudo apt install espeak-ng        # Ubuntu
brew install espeak-ng            # macOS
```

`scripts/setup-workstation.sh` warns if it's missing; install it and restart the service.

---

## Preset voice returns 404, or presets are missing from `/v1/voices`

**Cause** — the Kokoro pool isn't running: either `AURIS_KOKORO_ENABLED=false`, or the `kokoro`
extra isn't installed, so the service started f5-only.

**Fix** — install the extra (`pip install -e ".[kokoro]"` or re-run
`scripts/setup-workstation.sh`), ensure `AURIS_KOKORO_ENABLED` isn't set to false, and restart.
On a tight GPU you can move Kokoro off the f5 card with `AURIS_KOKORO_DEVICE=cpu` (it's small and
fast on CPU).

---

## Dashboard: no Record option / mic doesn't work

**Symptom** — in the browser dashboard, **+ New voice** offers only an upload, with the hint
"Recording needs a secure page — open this dashboard with `auris dashboard`…", and Transcribe has
no **● Record from microphone** link. Or, where recording is offered, it fails with "Microphone
permission denied — allow it for this site, or upload a file."

**Cause** — browsers only allow microphone capture on a **secure context**: `https://…` or
`http://localhost`. The shared service is served over **plain http** on a tailnet IP
(`http://your-server:8011`), so the browser disables the mic. This is a browser rule, not an
auris bug.

**Fix — use `auris dashboard`.** Install the client (`./scripts/setup-client.sh`) and run
**`auris dashboard`**. It serves the dashboard from **`http://localhost`** (a secure context), so
the **Record** button works — no HTTPS, no certificates, and your token stays in the local proxy.
See [remote-users.md](remote-users.md).

**No client installed?** Just **upload an audio file** instead of recording: capture ~10 s in any
app (Voice Memos, your phone), then drop it on **+ New voice** (or **+ Add a clip** on an existing voice) in the dashboard.
Uploading works fine over http. (You can also record + upload from the terminal with
`auris record "Me"` once the client is installed.)

---

## Still stuck?

The error messages auris prints are meant to tell you the next step. If something here
doesn't match what you see, re-run with the full output and check that:

1. FFmpeg is installed (`ffmpeg -version`).
2. The venv is active (`which auris`).
3. The model extra is installed (`auris tts` is what needs it).
4. For the service: it's running (`scripts/service.sh status`) and the GPU works
   (`.venv/bin/python -c "import torch; print(torch.cuda.is_available())"`).
