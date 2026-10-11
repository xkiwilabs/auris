# Speech-to-text

Transcribe recordings with auris — on your own machine, or through a service your team runs.
For the rest of the commands see [usage.md](usage.md); if you're using someone else's service,
[remote-users.md](remote-users.md) is the friendlier starting point.

---

## What it does

Speech in, text out. Point `auris transcribe` at an audio file (or record straight from the
microphone) and get back plain text, JSON, or subtitles.

- **Batch, not live.** A whole file is decoded at once; there's no streaming/partial-results mode.
- **The same service as TTS.** `auris-server` serves speech-to-text (`/v1/audio/transcriptions`)
  alongside text-to-speech from one process, with one token and one GPU budget. Either pipeline
  can be switched off — see [Running transcription only](#running-transcription-only).
- **Nothing leaves the machine.** The engine is [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  running locally. Weights are downloaded once from HuggingFace; audio never is.

Speech-to-text needs the `model` extra (`./scripts/install.sh --model`), the same one that
installs f5-TTS. `--mic` also needs the `audio` extra.

---

## Quick start

```bash
auris transcribe meeting.m4a
auris transcribe meeting.m4a --format srt --out meeting.srt
auris transcribe --mic --seconds 30
```

Any format FFmpeg or libsndfile can read works — wav, flac, ogg, mp3, m4a, mp4, webm. A video
file contributes only its audio track.

The transcript goes to stdout by default, so it pipes:

```bash
auris transcribe interview.wav | wc -w
```

Progress and status messages go to **stderr**, so a piped transcript stays clean.

> `auris transcribe --mic` is **not** capped at 10 seconds. That cap belongs to voice *cloning*
> (`auris record`), where f5-TTS needs the reference audio and its transcript to stay aligned.
> Record for as long as you like here — `--seconds` is the only limit.

---

## `auris transcribe` reference

```bash
auris transcribe [AUDIO_FILE] [--out FILE] [--format FMT] [--asr-model ID]
                 [--language CODE] [--translate] [--word-timestamps] [--prompt TEXT]
                 [--mic] [--seconds N]
```

| Option | Default | Notes |
|--------|---------|-------|
| `AUDIO_FILE` | (none) | The file to transcribe. Required unless you pass `--mic`; passing both is an error. |
| `--out` | stdout | Write the transcript to this file instead of printing it. |
| `--format` | `text` | `text`, `json`, `verbose_json`, `srt`, `vtt` — the same set the HTTP API renders. |
| `--asr-model` | `AURIS_ASR_MODEL` | Which speech-to-text model to use (`auris asr list`). In remote mode, the service's default. |
| `--language` | auto-detect | A language code (`en`, `fr`, `de`, …), or `auto`. Needs a multilingual model — an `.en` model ignores it. |
| `--translate` | off | Translate into English instead of transcribing verbatim. Needs a multilingual model too. |
| `--word-timestamps` | off | Add per-word timings. Only `verbose_json` shows them. |
| `--prompt` | (none) | Bias the spelling of names and jargon, e.g. `--prompt "auris, f5-TTS"`. |
| `--mic` | off | Record from the default microphone instead of reading a file. Needs the `audio` extra. |
| `--seconds` | `30.0` | Recording length for `--mic`, minimum 1 s. Uncapped at the top end. |

The formats:

| `--format` | Output |
|------------|--------|
| `text` | Just the words, one blob. |
| `json` | `{"text": "…"}` — OpenAI's minimal shape. |
| `verbose_json` | Text plus `language`, `language_probability`, `duration`, per-segment timings, and `words` when `--word-timestamps` is set. |
| `srt` | SubRip subtitles (`00:00:02,160 --> …`). |
| `vtt` | WebVTT subtitles (`00:00:02.160 --> …`). |

```bash
auris transcribe talk.wav --format verbose_json --word-timestamps --out talk.json
auris transcribe lecture.mp3 --format vtt --out lecture.vtt
auris transcribe notes.m4a --prompt "auris, f5-TTS, Kokoro, ctranslate2"
```

`auris transcribe` is **location-transparent**, like the other verbs: if you've run `auris login`
(or set `AURIS_BASE_URL`), the audio is uploaded to the service and decoded there; otherwise it
runs on this machine. Same flags either way.

---

## Choosing a model

`auris asr list` prints the catalogue, marks the active model with `*`, says which weights are
already downloaded, and shows the device and precision they'll run on:

```bash
auris asr list
```

```
active: large-v3-turbo on cuda:0 (float16)
allowed for remote callers: tiny.en, base.en, distil-small.en, small.en, medium.en, distil-large-v3, large-v3-turbo

   tiny.en             39M  en    downloaded     fastest; weakest on names and numbers
   base.en             74M  en    downloaded     fast, fine for clean speech; the light choice on a CPU
   distil-small.en    166M  en    not downloaded distilled: small.en accuracy near base.en speed
   small.en           244M  en    downloaded     good accuracy/speed balance
   medium.en          769M  en    not downloaded high accuracy, English-only
   distil-large-v3    756M  en    not downloaded near large-v3 accuracy, much faster
 * large-v3-turbo     809M  multi downloaded     default — best accuracy here; multilingual

Set the default with AURIS_ASR_MODEL, the device with AURIS_ASR_DEVICE.
```

The `downloaded` / `not downloaded` column is whatever happens to be in *your* HuggingFace cache,
so yours will differ from the sample above.

Smallest first; the `.en` models are English-only, `large-v3-turbo` is the multilingual one, and
the default. It is the most accurate here, and accuracy matters most for cloning: f5-TTS treats
the reference transcript as ground truth, so a misheard name is baked into every sentence that
voice speaks. On a GPU it costs well under a second per clip and about 1.6 GB per resident worker.
**On a CPU-only machine**, set `AURIS_ASR_MODEL=base.en` or `small.en` — turbo still works there,
just slowly.

Anything not already downloaded is fetched from HuggingFace on first use, which stalls that first
request. Pre-pull instead:

```bash
auris asr pull small.en           # one model
auris asr pull --all              # the whole catalogue
auris asr pull                    # just the configured AURIS_ASR_MODEL
```

Weights land in the normal HuggingFace cache (`~/.cache/huggingface`, or `$HF_HOME`). Set the
default for every command with `AURIS_ASR_MODEL`.

In remote mode `auris asr list` asks the *service* which models it accepts (the same data as
`GET /v1/audio/models`), since the service does the decoding.

---

## Language, translation and prompts

> **`--language` and `--translate` need a multilingual model.** The default, `large-v3-turbo`,
> is one; every `.en` model in the catalogue is English-only. faster-whisper does **not** error on
> those — it quietly ignores `language` and decodes `translate` as an ordinary transcription — so
> both flags are no-ops when you pick an `.en` model. A reported language of `en` from an `.en`
> model tells you nothing: it is the only answer that model can give.

**Language.** Leave `--language` off and the language is detected from the audio; pass a code
(`--language fr`) to force it. Forcing helps when detection wobbles on short or noisy clips, and
skips the detection pass.

```bash
auris transcribe entretien.wav --asr-model large-v3-turbo --language fr
# --language auto asks for detection explicitly; verbose_json reports what it found
auris transcribe mystery.wav --asr-model large-v3-turbo --language auto --format verbose_json
```

`verbose_json` reports `language` and `language_probability`, so you can check the guess.

> **Remote mode: `auto` and omitting `--language` are not the same.** Omitting it sends nothing,
> so the service applies its own `AURIS_ASR_LANGUAGE` default — which an operator may have pinned
> to a specific language. `--language auto` explicitly asks for detection every time, whatever the
> service default is. Running locally the two are identical: both detect.

**Translation.** `--translate` asks Whisper for English output regardless of the input language.
It's translation, not transcription — the transcript won't match the words spoken.

```bash
auris transcribe entretien.wav --asr-model large-v3-turbo --translate
```

**Prompts.** `--prompt` seeds the decoder with text, which biases spelling. Use it for proper
nouns, product names and jargon the model keeps mangling:

```bash
auris transcribe standup.m4a --prompt "auris, f5-TTS, Kokoro, ctranslate2, Tailscale"
```

It's a nudge, not a rule — the model can still ignore it.

### Cloning always transcribes as English

The *cloning* path (`auris record`, `auris clone`, `POST /v1/voices/{name}/clone`) pins
`language="en"` and turns the VAD filter off, and does not expose either as an option. That's
deliberate, and it's why it differs from `auris transcribe`:

- An **accented English** reference that auto-detected as another language would produce a worse
  transcript, and f5-TTS conditions on that transcript — a bad one degrades every generation from
  that voice.
- The **VAD filter** can drop a leading word from the *transcript* (it gates the decode; the
  stored audio is untouched), desynchronising the transcript from the clip it describes. The two
  have to stay in step, so anything that can silently swallow a word is off.

Transcription has neither constraint, so it auto-detects by default and runs the VAD.

---

## The HTTP API

Both routes need the bearer token:

```bash
export AURIS_BASE_URL=http://your-server:8011
export AURIS_AUTH_TOKEN=<token>
AUTH=(-H "Authorization: Bearer ${AURIS_AUTH_TOKEN}")
```

### POST /v1/audio/transcriptions

OpenAI-compatible speech-to-text. `multipart/form-data`:

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `file` | yes | — | The audio, in any format libsndfile or FFmpeg can decode (including the browser's webm/opus). |
| `model` | no | server default | A speech-to-text id from `GET /v1/audio/models`. Omitted, empty, or OpenAI's `whisper-1` all mean "the server default". Anything else not on the allowlist → `422`. |
| `language` | no | `AURIS_ASR_LANGUAGE` | A language code, or `auto` to force detection. Silently ignored unless the decoding model is multilingual (see above). |
| `task` | no | `transcribe` | `transcribe` or `translate` (into English). Anything else → `422`. Also needs a multilingual model. |
| `response_format` | no | `json` | `json`, `verbose_json`, `text`, `srt`, `vtt`. Anything else → `422`. |
| `prompt` | no | (none) | Biases the spelling of names/jargon. |
| `timestamp_granularities[]` | no | (none) | Send `word` to include per-word timings. Only `verbose_json` renders them. |

Note the default `response_format` here is `json`, while the CLI's `--format` defaults to `text`.

```bash
# json (the default) -> {"text": "..."}
curl -fsS "${AUTH[@]}" -F file=@meeting.m4a \
  "${AURIS_BASE_URL}/v1/audio/transcriptions"

# plain text
curl -fsS "${AUTH[@]}" -F file=@meeting.m4a -F response_format=text \
  "${AURIS_BASE_URL}/v1/audio/transcriptions"

# subtitles
curl -fsS "${AUTH[@]}" -F file=@meeting.m4a -F response_format=srt \
  "${AURIS_BASE_URL}/v1/audio/transcriptions" -o meeting.srt
curl -fsS "${AUTH[@]}" -F file=@meeting.m4a -F response_format=vtt \
  "${AURIS_BASE_URL}/v1/audio/transcriptions" -o meeting.vtt

# everything: a bigger model, forced detection, word timings
curl -fsS "${AUTH[@]}" -F file=@meeting.m4a \
  -F response_format=verbose_json \
  -F model=small.en \
  -F language=auto \
  -F 'timestamp_granularities[]=word' \
  -F 'prompt=auris, f5-TTS' \
  "${AURIS_BASE_URL}/v1/audio/transcriptions"
```

`verbose_json` comes back like this:

```json
{
  "task": "transcribe",
  "language": "en",
  "language_probability": 0.98,
  "duration": 4.8,
  "text": "Hello from auris. This is a test recording.",
  "segments": [
    {"id": 1, "start": 0.0, "end": 2.16, "text": "Hello from auris.", "no_speech_prob": 0.012},
    {"id": 2, "start": 2.16, "end": 4.8, "text": "This is a test recording.", "no_speech_prob": 0.008}
  ],
  "words": [
    {"word": "Hello", "start": 0.0, "end": 0.46},
    {"word": "from", "start": 0.46, "end": 0.9},
    {"word": "auris.", "start": 0.9, "end": 2.16}
  ]
}
```

`words` only appears when you send `timestamp_granularities[]=word`.

Errors use the service's standard envelope, `{"error": {"code": …, "message": …}}`:

| Status | `code` | When |
|--------|--------|------|
| `400` | `bad_audio` | The upload couldn't be decoded. |
| `401` | `unauthorized` | Missing or wrong bearer token. |
| `422` | `invalid_request` | Unknown/not-allowed `model`, bad `task` or `response_format`, an empty file, one over `AURIS_ASR_MAX_UPLOAD_MB`, or audio longer than `AURIS_ASR_MAX_SECONDS`. |
| `429` | `overloaded` | Every transcription slot is busy *and* the wait queue is full. Returned before the upload is decoded. Retry shortly. |
| `503` | `pipeline_disabled` | This server runs without speech-to-text (`AURIS_STT_ENABLED=false`). The message names the variable. |
| `504` | `timeout` | The caller waited longer than `AURIS_ASR_TIMEOUT_S`. |

### GET /v1/audio/models

Which speech-to-text models this server will accept in a `model` field, and which pipelines it
serves:

```bash
curl -fsS "${AUTH[@]}" "${AURIS_BASE_URL}/v1/audio/models"
```

```json
{
  "default": "large-v3-turbo",
  "models": [
    {"id": "tiny.en", "params": "39M", "lang": "en", "desc": "fastest; weakest on names and numbers"},
    {"id": "base.en", "params": "74M", "lang": "en", "desc": "fast, fine for clean speech; the light choice on a CPU"},
    {"id": "distil-small.en", "params": "166M", "lang": "en", "desc": "distilled: small.en accuracy near base.en speed"},
    {"id": "small.en", "params": "244M", "lang": "en", "desc": "good accuracy/speed balance"},
    {"id": "medium.en", "params": "769M", "lang": "en", "desc": "high accuracy, English-only"},
    {"id": "distil-large-v3", "params": "756M", "lang": "en", "desc": "near large-v3 accuracy, much faster"},
    {"id": "large-v3-turbo", "params": "809M", "lang": "multi", "desc": "default — best accuracy here; multilingual"}
  ],
  "pipelines": {"stt": true, "tts": true}
}
```

`default` is what an omitted (or `whisper-1`) `model` resolves to. `models` is the allowlist —
narrowed by `AURIS_ASR_MODELS`, or the whole catalogue when that's empty. `pipelines` tells a
client which halves of the service are actually running before it calls them.

This route stays available even when speech-to-text is switched off (`"stt": false`), so a client
can discover that fact instead of guessing from a `503`.

---

## Server settings

All read from the environment with the `AURIS_` prefix (or `.env` on the workstation — see the
[deploy guide](deploy/README.md)).

| Variable | Default | Purpose |
|----------|---------|---------|
| `AURIS_ASR_MODEL` | `large-v3-turbo` | The default speech-to-text model, and the one loaded into every pool worker. `base.en` on a CPU-only machine. |
| `AURIS_ASR_DEVICE` | `auto` | `auto` (GPU when one is visible), `cpu`, `cuda`, `cuda:1`. |
| `AURIS_ASR_COMPUTE_TYPE` | `auto` | `auto` = `float16` on GPU, `int8` on CPU. Or `int8_float16`, `float32`. |
| `AURIS_ASR_MODELS` | *(whole catalogue)* | Comma-separated allowlist of ids remote callers may request. The configured default is always allowed. |
| `AURIS_ASR_WORKERS` | `1` | Resident whisper copies = transcriptions decoded in parallel. |
| `AURIS_ASR_LANGUAGE` | *(empty = detect)* | The server's default language when a request omits `language`. Only has an effect while `AURIS_ASR_MODEL` is multilingual (`large-v3-turbo`, the default); the `.en` models ignore it. |
| `AURIS_ASR_VAD` | `true` | Run faster-whisper's bundled Silero VAD filter on the speech-to-text endpoints. (Cloning never does.) |
| `AURIS_ASR_MAX_UPLOAD_MB` | `250` | Reject bigger `/v1/audio/transcriptions` uploads with `422`. An hour of 16-bit 24 kHz WAV is ~170 MB. (The clone route has its own fixed 50 MB cap → `400 bad_audio`.) |
| `AURIS_ASR_MAX_SECONDS` | `3600` | Reject audio longer than this with `422`, checked after decode and before the pool. Matches the byte cap above, which admits about an hour of uncompressed WAV. |
| `AURIS_ASR_TIMEOUT_S` | `600` | How long a caller waits for a transcription before `504`. |
| `AURIS_STT_ENABLED` | `true` | Build the transcription pool. `false` ⇒ `/v1/audio/transcriptions` returns `503`. |
| `AURIS_TTS_ENABLED` | `true` | Build the synthesis pools. `false` ⇒ the TTS routes return `503`. |

Setting **both** to `false` makes the server refuse to start rather than serve nothing.

> **`AURIS_ASR_TIMEOUT_S` is a *caller* budget, not a decode cap.** It bounds how long a request
> waits for its turn plus its decode before the service gives up and returns `504`. It does
> **not** cancel or shorten the decode itself — faster-whisper has no cancellation hook — so the
> worker slot stays occupied until that decode genuinely finishes. Timing out a request doesn't
> free capacity; it only stops the client waiting.

`AURIS_QUEUE_MAXSIZE` (default `8`) is shared with the synthesis pools: the same number is applied
to each pool separately as its wait-queue depth.

---

## Capacity and GPU placement

The service holds `AURIS_ASR_WORKERS` resident, device-pinned whisper models. Up to that many
decodes run at once; further callers wait, bounded by `AURIS_QUEUE_MAXSIZE`. Beyond that, a request
is rejected immediately with **`429 overloaded`** rather than queued forever — back off and retry.

```
AURIS_ASR_WORKERS=2          # 2 transcriptions decode in parallel
AURIS_QUEUE_MAXSIZE=8        # 8 more may wait; the 11th caller gets 429
```

That admission budget covers the **whole** request, not just its turn on the GPU: a caller that
would be rejected is rejected before its upload is decoded, so the memory a decoded float32 array
costs is bounded by the same two numbers rather than by how many requests the web server happens
to accept at once. `AURIS_ASR_MAX_UPLOAD_MB` and `AURIS_ASR_MAX_SECONDS` bound one request's share
of that.

Each resident worker holds a full copy of `AURIS_ASR_MODEL` in memory, so raising the worker count
raises VRAM use proportionally. A request naming a *different* allowlisted model still occupies one
of those slots but decodes on a separately cached instance — so concurrency stays capped at
`AURIS_ASR_WORKERS` however many distinct models are in flight, while memory does not.

**Cloning shares this pool.** `POST /v1/voices/{name}/clone` without a `ref_text` transcribes
through the same workers and the same wait queue, so a burst of transcription traffic can make a
clone queue — or return `429`. If clones must not be blocked by transcription load, give the pool
more workers or supply `ref_text` on the clone.

**On a two-GPU box, put speech-to-text on the second card** so it never contends with synthesis:

```
AURIS_GPU_DEVICES=cuda:0     # f5-TTS
AURIS_ASR_DEVICE=cuda:1      # whisper, out of the way
```

With one GPU and tight VRAM, `AURIS_ASR_DEVICE=cpu` is a reasonable trade — slower, but it leaves
the card to synthesis. (`AURIS_ASR_DEVICE=mps` falls back to CPU: ctranslate2 has no Metal
backend.)

---

## Running transcription only

A box can serve speech-to-text and nothing else — useful for a second machine that has no business
loading f5-TTS:

```
AURIS_TTS_ENABLED=false
```

The process then never loads the synthesis models at all. What the routes do:

| Route | With `AURIS_TTS_ENABLED=false` |
|-------|-------------------------------|
| `POST /v1/audio/transcriptions` | works |
| `GET /v1/audio/models` | works; reports `"pipelines": {"stt": true, "tts": false}` |
| `POST /v1/tts`, `POST /v1/tts/stream`, `POST /v1/audio/speech` | `503 pipeline_disabled`, message naming `AURIS_TTS_ENABLED` |
| `GET /v1/voices` | works (it only reads the library) |
| `POST /v1/voices/{name}/clone` | works — cloning needs transcription, not synthesis |

The mirror image, `AURIS_STT_ENABLED=false`, is a transcription-free TTS server:

| Route | With `AURIS_STT_ENABLED=false` |
|-------|-------------------------------|
| `POST /v1/audio/transcriptions` | `503 pipeline_disabled`, message naming `AURIS_STT_ENABLED` |
| `GET /v1/audio/models` | works; reports `"pipelines": {"stt": false, "tts": true}` |
| `POST /v1/voices/{name}/clone` **with** `ref_text` | works |
| `POST /v1/voices/{name}/clone` **without** `ref_text` | `503 pipeline_disabled` |

> **With `AURIS_STT_ENABLED=false`, cloning requires `ref_text`.** Clone uploads transcribe through
> the shared speech-to-text pool, so a clone that needs a transcript has nothing to transcribe
> with. Supply the exact words spoken in the clip instead — `--ref-text` on `auris clone`, or the
> `ref_text` form field over HTTP — and cloning works normally.

Setting both flags to `false` is rejected at startup.

---

## Licensing

Everything in the speech-to-text path is **MIT-licensed and ungated** — no access request, no
HuggingFace token, no non-commercial clause:

| Component | License |
|-----------|---------|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (the runtime) | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) (the inference engine under it) | MIT |
| OpenAI Whisper weights (`tiny.en` … `medium.en`) | MIT |
| `large-v3-turbo` weights | MIT |
| Distil-Whisper weights (`distil-small.en`, `distil-large-v3`) | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) (bundled with faster-whisper, used by `AURIS_ASR_VAD`) | MIT |

That is a deliberate contrast with the **text-to-speech** side: f5-TTS's weights are
**CC-BY-NC** (non-commercial), which is fine for auris's research-primary use but does constrain
what the *generated* audio can be used for. Nothing in this document carries that constraint —
transcription output is yours.

(Kokoro, the preset-voice TTS model, is Apache-2.0. See [usage.md](usage.md).)
