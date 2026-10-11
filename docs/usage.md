# auris usage (command-line reference)

Every `auris` command and option, for using auris **on your own computer**. New here? Start with
[getting-started.md](getting-started.md). Using a service someone else runs? You want
[remote-users.md](remote-users.md) instead. Transcribing audio has its own guide,
[stt.md](stt.md). For fixes, see [troubleshooting.md](troubleshooting.md).

All commands run fully offline and store data locally. Activate your environment first:

```bash
source .venv/bin/activate
auris --help
```

---

## Concepts

- **Speaker** — a named voice profile with one or more reference clips. Identified by name
  (case-insensitive): `"Me"` and `"me"` refer to the same speaker.
- **Reference clip** — a short recording of the speaker plus its exact transcript. f5-TTS
  conditions on both at generation time (it clones *in-context*, with no training step).
- **Generation** — synthesising new text in a speaker's voice.
- **Preset voice** — a built-in voice (Kokoro) you can use **without cloning**, by passing its
  name (e.g. `narrator-uk-f`, `calm-m`) anywhere a speaker name is accepted. Run `auris voices`
  to list them. Preset names are reserved.
- **Model** — the engine. auris uses **f5-TTS** for cloning (weights **CC-BY-NC**, fine for
  research/personal use) and **Kokoro** (Apache-2.0) for the preset voices. The voice name picks
  the engine automatically. Kokoro needs the `kokoro` extra + **espeak-ng** (see getting-started).

Audio is handled internally as mono, 24 kHz. Output is written as WAV.

---

## `auris voices`

List available voices: built-in **presets** (usable now, no cloning) and your **cloned** speakers.

```bash
auris voices
auris voices --owner alice@example.com   # only this account's voices
```

Use any name shown here as the `SPEAKER` argument to `auris tts` / `auris say`.

The built-in presets (Kokoro voice in brackets):

| Preset | Voice |
|--------|-------|
| `narrator-us-f` / `narrator-us-m` | US English, female (`af_heart`) / male (`am_michael`) |
| `narrator-uk-f` / `narrator-uk-m` | UK English, female (`bf_emma`) / male (`bm_george`) |
| `bright-f` | US English, female, bright (`af_bella`) |
| `calm-m` | US English, male, low and steady (`am_puck`; `am_adam` until it proved the weakest Kokoro voice) |
| `low-f` | US English, female, lower-pitched (`af_kore`) |
| `soft-f` | US English, female, soft and slow (`af_nicole`) |
| `lively-m` | US English, male, lively (`am_fenrir`) |
| `bright-uk-f` | UK English, female, bright (`bf_isabella`) |
| `deep-uk-m` | UK English, male, deep (`bm_fable`) |

On a shared service, **a cloned voice is private by default** — visible and usable only by
whoever created it (and an admin). Its owner shares it with everyone else, and can take that back:

```bash
auris voices share NAME     # visible and usable by every signed-in account; still only the
                             # owner (or an admin) can rename, delete or add clips
auris voices unshare NAME   # back to private
```

Two or more accounts can each own a voice with the same name (names are unique **per owner**, not
globally), so a bare `NAME` can be ambiguous — pass `--owner <email>` to pick one, either before
or after the subcommand (both work identically; giving it twice with two different addresses is
refused):

```bash
auris voices share NAME --owner alice@example.com
auris voices --owner alice@example.com share NAME   # same thing
```

Unsharing a voice that has no owner at all — a legacy voice from before accounts existed — is
refused, locally and over HTTP: an unowned voice is already visible to every account regardless
of its visibility column, so "unsharing" it would change nothing any caller could observe. The
message tells you to run `auris voices adopt <email>` first (see [accounts.md](accounts.md)),
then unshare it. `share` on an unowned voice still succeeds, but prints a note that it was already
visible to all.

`auris voices adopt <email>` (admin, on the box — see [accounts.md](accounts.md)) assigns every
currently-unowned voice to one account in a single, all-or-nothing operation: if an unowned name
collides with one that account already owns, nothing is adopted and the error names the clash, so
you rename or delete one side and re-run. It does not accept `--owner` — the account is its one
argument.

This only concerns the HTTP service — a single-user local install has no accounts, so every voice
it creates is unowned and every voice it can see, it can use. For creating dashboard accounts and
the full ownership/visibility model, see [accounts.md](accounts.md).

---

## `auris record`

Record yourself speaking and clone your voice in one step. Creates the speaker if it
doesn't already exist; recording again appends another clip to the same speaker.

```bash
auris record SPEAKER [--seconds N] [--passage TEXT] [--free]
```

| Option | Default | Notes |
|--------|---------|-------|
| `SPEAKER` | (required) | Speaker name; created if new. |
| `--seconds` | `12.0` | Recording length, 3–30 s. The clone keeps up to 10 s, ending at a pause, so a little spare is good: a recording that stops mid-word loses that word. |
| `--passage` | Rainbow Passage | Text shown to read aloud (guidance only — override with your own). |
| `--free` | off | Free-form: show no passage, just say anything. |

```bash
auris record "Me"                          # read the default passage, 12 s
auris record "Me" --free --seconds 20      # say whatever you like
auris record "Me" --passage "My own text." # read your own passage
```

The recording is **transcribed automatically** (faster-whisper) and that transcription is
stored as the clip's transcript — so it always matches what you actually said, whether you
read the passage or ad-lib. The reference is then fitted exactly as for `auris clone` below.

Pick the transcription model per command with `--asr-model` (also on `auris clone`), or set
the default with `AURIS_ASR_MODEL` — see [`auris asr`](#auris-asr) below.

Requires the `audio` extra (microphone) **and** the `model` extra (transcription) —
both installed by `scripts/install.sh --model` — plus microphone permission.

---

## `auris speakers add`

Create a speaker profile without recording (for the file-based `clone` workflow).

```bash
auris speakers add NAME
```

```bash
auris speakers add "Alice"
```

---

## `auris clone`

Clone a voice from an existing audio file. The speaker must already exist
(`auris speakers add`). If you don't supply `--ref-text`, the reference is transcribed
automatically.

```bash
auris clone SPEAKER --ref FILE [--ref-text "TRANSCRIPT"]
```

| Option | Notes |
|--------|-------|
| `SPEAKER` | Must already exist. |
| `--ref` | Path to a reference audio file. WAV/FLAC/OGG load directly; MP3/M4A/MP4 and other formats are decoded via FFmpeg. Must exist and be ≥ 3 s. |
| `--ref-text` | Optional. The exact words spoken in the clip; **auto-transcribed if omitted**. |

```bash
auris clone "Alice" --ref alice.wav                                   # auto-transcribed
auris clone "Alice" --ref alice.wav --ref-text "the exact words…"     # explicit transcript
```

The reference is resampled to 24 kHz and fitted to **at most 10 s, ending inside a pause** — never
part-way through a word. (A reference that stops mid-word makes f5-TTS say that word, or part of
it, at the start of everything the voice speaks.) The transcript is the words in the part that is
kept. If you pass `--ref-text` for a recording with speech past 10 s, the clone is refused, because
your text would describe audio that is no longer there: trim the file, or leave the text out.

A clip shorter than 3 seconds is rejected. A few sentences — 8 to 15 seconds, with natural pauses
between them — gives the best clone; under 6 seconds still works but carries less of the voice,
and the command says so. Auto-transcription needs the `model` extra.

**Fixing older voices.** Voices cloned before this were capped blindly at 10 s, and a clip that
ends mid-word can't be spotted from the audio alone. So on the server box, `auris voices refit`
*tests* each voice: it speaks six sentences and listens for words that were never asked for. A
voice with two or more is cut back to its last pause, re-tested, and fixed only if that leaks
less. It also flags transcripts that cover little of the audio (usually noise — re-record those)
and short clips. `--apply` stores each fix as a new clip and keeps the old one. `--retranscribe`
also replaces transcripts the speech-to-text model now hears differently; review the report
first, because names can go either way — name the voices to limit it
(`auris voices refit opt001 --apply --retranscribe`). Needs the model extra; allow a few seconds
per voice.

Compressed and video formats (mp3, m4a, mp4, …) are decoded automatically with FFmpeg — no
manual conversion needed; a video file contributes only its audio track.

> One good clip is ideal for f5-TTS — it uses a single reference per generation, so extra
> clips don't blend into a richer voice.

---

## `auris tts`

Generate speech from text in a cloned voice **or a built-in preset** and write a WAV.

```bash
auris tts SPEAKER --text "..." --out FILE [--speed N]
```

| Option | Default | Notes |
|--------|---------|-------|
| `SPEAKER` | (required) | A cloned speaker (needs a reference clip) **or** a preset name (`auris voices`). |
| `--text` | (required) | Text to synthesise. |
| `--out` | (required) | Output WAV path. |
| `--speed` | `1.0` | Speaking rate, 0.5–2.0. |

```bash
auris tts "Me" --text "Hello, this is my cloned voice." --out hi.wav
auris tts narrator-uk-f --text "A built-in preset voice." --out preset.wav   # no cloning needed
auris tts "Me" --text "A little slower, please." --out slow.wav --speed 0.9
```

The first call downloads the f5-TTS model (~1.4 GB) once. **Requires FFmpeg** — see
[troubleshooting](troubleshooting.md#tts-fails-with-libtorchcodec--libavutil).

---

## `auris say`

Stream speech in a cloned voice and **play it live** — audio starts after the first
sentence, while the rest is still generating.

```bash
auris say SPEAKER --text "..." [--out FILE] [--speed N]
auris say SPEAKER --file script.txt
```

| Option | Default | Notes |
|--------|---------|-------|
| `SPEAKER` | (required) | Must have a reference clip. |
| `--text` / `--file` | (one required) | Text inline, or a text file to read. |
| `--out` | (none) | Optionally also save the full audio to a WAV. |
| `--speed` | `1.0` | Speaking rate, 0.5–2.0. |

The text is split into sentences; each is synthesised and played as it's ready, so you
hear the start before the end is rendered. First-audio latency is roughly the time to
generate one sentence (faster on the RTX/CUDA box than on Mac MPS). Requires the `audio`
extra (same one `record`/`play` use), plus the model runtime + FFmpeg like `tts`.

---

## `auris play`

Play a WAV through your speakers (cross-platform — no `afplay`/`aplay` needed).

```bash
auris play FILE
```

```bash
auris play hi.wav
```

Requires the `audio` extra (same one `record` uses).

---

## `auris info`

Show the active compute device, the default model, and where data is stored.

```bash
auris info
```

Example output:

```
auris 0.1.0
mode:           local (model runs on this machine)
default model:  f5_tts
device setting: auto
stt model:      large-v3-turbo on cuda:0 (float16)
data dir:       data
torch:          2.11.0+cu128
active device:  cuda  (cuda=True, mps=False)
```

`active device` is what synthesis runs on — `cuda` (NVIDIA GPU), `mps` (Apple Silicon
GPU), or `cpu`. Override detection with `AURIS_DEVICE`. If torch isn't installed, the
command says so (install it with `scripts/install.sh --model`).

---

## `auris transcribe`

Turn speech into text — the reverse of `tts`. Reads an audio file, or records from the
microphone with `--mic`.

```bash
auris transcribe [AUDIO_FILE] [--out FILE] [--format FMT] [--asr-model ID]
                 [--language CODE] [--translate] [--word-timestamps] [--prompt TEXT]
                 [--mic] [--seconds N]
```

| Option | Default | Notes |
|--------|---------|-------|
| `AUDIO_FILE` | (none) | File to transcribe — any format FFmpeg or libsndfile reads. Required unless `--mic`. |
| `--out` | stdout | Write the transcript to this file instead of printing it. |
| `--format` | `text` | `text`, `json`, `verbose_json`, `srt`, `vtt`. |
| `--asr-model` | `AURIS_ASR_MODEL` | Which model to use (`auris asr list`). |
| `--language` | auto-detect | A language code (`en`, `fr`, …), or `auto`. |
| `--translate` | off | Translate into English instead of transcribing verbatim. |
| `--word-timestamps` | off | Per-word timings; shown by `verbose_json` only. |
| `--prompt` | (none) | Bias the spelling of names/jargon, e.g. `"auris, f5-TTS"`. |
| `--mic` | off | Record from the microphone instead. Needs the `audio` extra. |
| `--seconds` | `30.0` | Recording length for `--mic`, minimum 1 s. |

```bash
auris transcribe meeting.m4a                                # plain text to stdout
auris transcribe meeting.m4a --format srt --out meeting.srt # subtitles
auris transcribe --mic --seconds 30                         # record, then transcribe
auris transcribe talk.wav --format verbose_json --word-timestamps
```

The transcript goes to stdout (status messages go to stderr, so piping stays clean).
Unlike `auris record`, `--mic` here is **not** capped at ~10 s — that cap is a cloning
constraint, not a transcription one. Needs the `model` extra.

Full guide, including the HTTP API and server settings: **[stt.md](stt.md)**.

---

## `auris asr`

Manage the speech-to-text (faster-whisper) models used by `auris transcribe` and to
transcribe reference clips.

```bash
auris asr list                          # catalogue + which are downloaded + the active one
auris asr pull small.en                 # pre-download one model
auris asr pull --all                    # pre-download the whole catalogue
```

`list` marks the active model with `*` and shows the device/precision it will run on. In
remote mode it asks the service which models *it* accepts instead, since the service does the
transcribing.

| Model | Params | Notes |
|-------|--------|-------|
| `tiny.en` | 39M | Fastest; weakest on names and numbers. |
| `base.en` | 74M | Fast, fine for clean speech; the light choice on a CPU. |
| `distil-small.en` | 166M | Distilled: `small.en` accuracy near `base.en` speed. |
| `small.en` | 244M | Good accuracy/speed balance. |
| `medium.en` | 769M | High accuracy, English-only. |
| `distil-large-v3` | 756M | Near `large-v3` accuracy, much faster. |
| `large-v3-turbo` | 809M | Default — best accuracy here; multilingual. |

Anything not already downloaded is fetched from HuggingFace on first use, so `auris asr pull`
is worth running ahead of time. Weights land in the normal HuggingFace cache
(`~/.cache/huggingface`, or `$HF_HOME`).

Transcription defaults to the GPU (`AURIS_ASR_DEVICE=auto`). On a machine where whisper would
compete with synthesis for VRAM, pin it with `AURIS_ASR_DEVICE=cpu` — a 10s clip is quick
either way.

---

## `auris keys`

Issue and revoke named API keys for remote users and scripts (admin, on the box). Each key
authenticates like the shared token — as a `Bearer` header, with `auris login`, or signing in to
the dashboard — but is named and independently revocable, and belongs to an account. See
[accounts.md](accounts.md) for accounts vs. keys in full.

```bash
auris keys add alice --owner alice@example.com   # issue a key for alice — printed ONCE, copy it now
auris keys list                                  # name, owner, active/revoked, last used
auris keys revoke alice                          # revoke it; the name stays reserved (can't be reissued)
```

`--owner <email>` is **required** — there's no account to default to, since `auris keys` has no
notion of "the caller". `auris keys list` shows an owner column; a key with `(no owner)` was
issued before this requirement existed. Names are `[A-Za-z0-9._-]{1,64}`; `admin` is reserved for
the master token. This manages the *server's* own key table, so it only makes sense on the box
running the server: in remote mode (`AURIS_BASE_URL` set) it refuses and points you at the admin
HTTP API instead (`GET`/`POST /v1/keys`, `DELETE /v1/keys/{name}`), authenticated with the master
token. Over HTTP, `POST /v1/keys` takes an optional `owner_email`; it defaults to the signed-in
caller, but the shared master token has no account of its own and **must** supply it.

A key minted before this change has no owner. It still authenticates, and can still list voices,
synthesise and transcribe — but it cannot clone, rename or delete a voice; those are refused with
a message pointing back here. There is no in-place "attach an owner" operation: revoke it and
issue a replacement with `--owner`:

```bash
auris keys revoke ci-bot
auris keys add ci-bot --owner ci@example.com
```

For creating dashboard **accounts** (a different, human credential), see
[accounts.md](accounts.md) and `auris users`.

---

## Configuration

auris reads settings from environment variables (prefix `AURIS_`). Defaults are sensible;
override only if you need to.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AURIS_DATA_DIR` | `./data` | Where speakers, clips, and the database live. |
| `AURIS_MODELS_DIR` | `./models` | Reserved for model storage. |
| `AURIS_DEVICE` | `auto` | `auto` picks CUDA → MPS → CPU. Force with `cuda`/`mps`/`cpu`. |
| `AURIS_DEFAULT_MODEL` | `f5_tts` | Model used for new speakers. |
| `AURIS_DEFAULT_SPEED` | `1.0` | Default speaking rate. |
| `AURIS_SAMPLE_RATE` | `24000` | Internal/output sample rate (Hz). |
| `AURIS_ASR_MODEL` | `large-v3-turbo` | Speech-to-text model for reference transcripts (`auris asr list`). `base.en` on a CPU-only machine. |
| `AURIS_ASR_DEVICE` | `auto` | `auto` uses the GPU when one is visible. `cpu`, `cuda`, `cuda:1`. |
| `AURIS_ASR_COMPUTE_TYPE` | `auto` | `float16` on GPU, `int8` on CPU. Override with e.g. `int8_float16`. |
| `AURIS_ASR_MODELS` | *(catalogue)* | Comma-separated allowlist of ids remote callers may request. |
| `AURIS_ASR_LANGUAGE` | *(empty = detect)* | *(server)* Default language when a request omits `language`. The CLI always detects unless you pass `--language`. |
| `AURIS_ASR_VAD` | `true` | Run the bundled Silero VAD filter when transcribing. Cloning never does. |
| `AURIS_ASR_WORKERS` | `1` | *(server)* Resident whisper copies = transcriptions decoded in parallel. |
| `AURIS_STT_ENABLED` | `true` | *(server)* Serve speech-to-text. `false` ⇒ those routes return `503`. |
| `AURIS_TTS_ENABLED` | `true` | *(server)* Serve text-to-speech. `false` ⇒ those routes return `503`. |
| `AURIS_SESSION_HOURS` | `12` | *(server)* Dashboard cookie-session lifetime, in hours. |

The `(server)` rows only affect `auris-server` — see [stt.md](stt.md) and the
[deploy guide](deploy/README.md).

```bash
AURIS_DATA_DIR=~/auris-data auris record "Me"
AURIS_DEVICE=cpu auris tts "Me" --text "Forcing CPU." --out cpu.wav
```

### Data layout

```
$AURIS_DATA_DIR/
├── auris.db                       # SQLite: speakers + reference_clips
└── voices/<speaker-id>/clips/     # reference WAVs (clip_001.wav, …)
```

Deleting `data/` resets everything. Nothing is sent off the machine.
