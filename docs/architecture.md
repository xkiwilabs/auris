# Architecture

How auris is put together: the commands, the dependency tiers, the layers, the conventions
that are load-bearing, and how it is tested. Read this before changing the code.

## What this is

`auris` is a **local-first, fully offline** voice-cloning + TTS tool — a command-line app plus
an optional HTTP service (`auris/server/`, `auris-server`). It does the full
**record → clone → generate** path using the [f5-TTS](https://github.com/SWivid/F5-TTS)
model, plus a browser dashboard served by the service. No audio leaves the machine.

## Commands

```bash
./scripts/install.sh           # create .venv, install auris + dev + audio deps (NO model)
./scripts/install.sh --model   # same, plus the heavy model runtime (torch, f5-tts, faster-whisper)
source .venv/bin/activate

pytest                         # fast suite — runs WITHOUT model weights or a GPU
pytest -m requires_model       # opt-in real-inference tests (need the model/kokoro extras)
pytest -m e2e                  # end-to-end: launches a real server + real models (needs a GPU)
pytest tests/cli/test_tts.py             # a single file
pytest tests/cli/test_tts.py::test_tts_generates_output_wav   # a single test

ruff check .                   # lint
ruff format .                  # format (line-length 100, target py311)

auris users add <email> --role admin   # bootstrap: the first dashboard account (password prints once)
```

The default `pytest` run excludes `requires_model` (configured via `addopts` in
`pyproject.toml`), so the standard suite never downloads weights or needs a GPU.

## Dependency tiers (this is load-bearing)

The project is split so the core library, CLI, and the entire fast test suite run with **no
heavy ML dependencies**. This is enforced by **lazy imports**, not just packaging — preserve it:

- `torch` / `f5_tts` are imported *inside* methods (`F5TTSModel.load`, `_resolve_device`), never
  at module top level.
- `faster_whisper` is imported inside `load_whisper`; `ctranslate2` inside `_cuda_available`.
- `sounddevice` (PortAudio) is an optional `audio` extra, imported lazily so headless installs work.

When adding a model or a feature that needs torch/whisper/PortAudio, **keep the import lazy** and
raise a clear "install the X extra" error if it's missing (see `transcribe.py` for the pattern).
Putting any of these at module top level will break `pytest` and every headless install.

**Nothing reachable from `auris/cli/` or `auris/client/` may import `fastapi`, `starlette` or
`uvicorn`** — they are the optional `server` extra and the shipped remote-client profile installs
`.[client,audio]`, so an import there kills `auris` at startup for every remote user. This binds
`auris/server/transcript_format.py` in particular: the CLI imports it (`auris transcribe
--format`), so it is deliberately import-light and must stay that way — put anything that needs
a `Response` or any other FastAPI type in the router instead. `fastapi` *is* installed in the dev
venv, so the suite would not notice on its own; `tests/cli/test_import_seam.py` is the guard.

### `pyproject.toml` extras
- `audio` — `sounddevice`, for `auris record` (mic) and `auris play` (playback).
- `model` — `f5-tts`, `faster-whisper`, for real inference + transcription.
- `dev` — pytest, pytest-asyncio (`asyncio_mode = "strict"`), ruff.

## Architecture

Layered, with the model behind an abstract interface so it can be swapped/mocked:

- **`auris/cli/`** — Typer app. `main.py` wires subcommands; each command is a thin module in
  `cli/commands/`. Commands are sync Typer functions that `asyncio.run(...)` an inner `_verb`
  coroutine, catch domain exceptions (`ValueError`, `LookupError`, `sf.SoundFileError`,
  `RuntimeError`), print to stderr, and `raise typer.Exit(code=1)`. Follow this exact shape when
  adding a command.
- **`auris/models/`** — `base.TTSModel` is the ABC every model implements (`load()` +
  `synthesise(text, conditioning, speed) -> float32 mono PCM`). `build_model(name, settings)` in
  `models/__init__.py` is the registry/factory. `conditioning` is a **`Conditioning =
  PreparedReference | PresetVoice`** union: cloning models take a `PreparedReference` (reference
  audio **+ its transcript**, in-context — **no embedding vector, no training step**); preset
  models take a `PresetVoice` (a built-in voice id). Each model type-guards its conditioning.
  - **Two models today: f5-TTS (cloning) and Kokoro (presets).** f5-TTS weights are
    **CC-BY-NC (non-commercial)** — fine here, research use. **Kokoro-82M** (Apache-2.0, 24 kHz
    native) provides 11 curated **preset voices** usable before cloning; it's preset-only (no
    cloning) and complements f5. `auris/models/presets.py` holds the curated table;
    `voice_library/resolve.py` maps a `voice` name → owning model + conditioning (preset names are
    reserved). Kokoro needs the `kokoro` extra + **espeak-ng** (a system dep like ffmpeg), is
    lazy-imported, and runs on `AURIS_KOKORO_DEVICE` (default GPU). The server builds **one
    `SynthesisPool` per model** (`build_pools`, `app.state.pools`); `AURIS_KOKORO_ENABLED=false`
    degrades cleanly to f5-only.
  - **Blended voices are Kokoro mixes, stored as a recipe on an ordinary `speakers` row.**
    `voice_library/blends.py` validates 2–3 distinct presets with positive weights (normalised to
    sum to 1) and serialises them to the `speakers.blend` JSON column, keeping each preset's Kokoro
    **voice id** so a blend keeps its sound if a preset name is re-pointed. The row has
    `default_model='kokoro'` and no clips, so ownership, sharing, rename and delete apply
    unchanged. `resolve.py` turns a blend row into `Resolved("kokoro", PresetVoice(heaviest id,
    its lang, mix=...), "blend")`, and `KokoroModel` sums the weighted `load_voice` tensors
    (cached per recipe). Only presets blend: f5, and every other cloner tried that takes identity
    from a recording, snaps to one speaker rather than landing in between. Writes go
    through `PUT /v1/voices/{name}/blend` (`voice_access.put_blend`: the target is found as for
    a clone, except that an admin re-mixes another account's blend in place, and `?owner=` picks
    one as for PATCH) or `auris voices blend`; a clip can never be added to a blend (`speakers.add_clip`
    refuses), a cloned voice never becomes one, and `auris voices refit` skips them.
  - **Orpheus was evaluated and dropped (don't re-add without re-checking the findings).** An
    Orpheus (Apache-2.0) integration was designed, planned, and spiked on the RTX box; the spike
    found the `orpheus-speech` package does **preset voices only — no reference cloning** — and
    its old vLLM pin forces a torch that **can't run modern (Blackwell sm_120) GPUs**. Given the
    research-primary use, Orpheus was reverted.
- **`auris/voice_library/`** — persistence. `db.py` holds the SQLite schema (`speakers`,
  `reference_clips`, `api_keys`, `users`) via `aiosqlite`, plus `migrate()` — the additive-column
  pattern for a table that may already hold data (`CREATE TABLE IF NOT EXISTS` can't add a column
  to an existing table). Guarded by `PRAGMA table_info`, not a version counter, so it's idempotent
  and also repairs a database created at some in-between commit; follow this pattern for the next
  schema change rather than a numbered migration. `storage.py` writes clip WAVs to
  `data/voices/{speaker_id}/clips/clip_NNN.wav`; `speakers.py` is the row CRUD; `cloning.py` and
  `references.py` are the shared helpers used by multiple commands (write a reference / load the
  latest reference); `users.py` holds the `users` table (email/password accounts, `role`
  admin|user, `password_version` bumped on every password change so `auth.resolve_session` can
  invalidate every other live session); `passwords.py` hashes with `hashlib.scrypt` and **must
  pass an explicit `maxmem`** — Python's default (`0`) reads to OpenSSL as a 32 MB ceiling, and
  the chosen cost parameters (`n=2**15, r=8`) need exactly that much, so the default silently
  fails with "memory limit exceeded". Each command opens its own connection and runs
  `create_tables` (idempotent).
  - **`speakers` carries `owner_id` and `visibility`** (`'private' | 'shared'`, defaulted from
    the owner by `speakers.default_visibility`: `'private'` when owned, **`'shared'` when
    unowned**). An **unowned row is always `shared`** — `_VISIBLE` ends `OR s.owner_id IS NULL`,
    so such a row is readable by every account whatever the column says, and storing `private`
    there both displays a lie and makes `auris voices adopt` silently *revoke* that access.
    `speakers.UNOWNED_IS_ALWAYS_SHARED` states the rule; `refuse_unowned_private` is the one
    implementation (creation refuses an explicit `private`+unowned, `refuse_inert_private` is the
    update-side spelling, and `migrate()` repairs such a row unconditionally). The name index is
    `UNIQUE (owner_id, name COLLATE NOCASE)`, so **names are
    unique per owner, not globally** — two accounts can each have a voice called `narrator`.
    SQLite treats `NULL` as distinct in a unique index, so that index alone does not stop two
    *unowned* rows sharing a name; a second partial index,
    `UNIQUE (name COLLATE NOCASE) WHERE owner_id IS NULL`, does that job — the single most
    load-bearing fact of this schema, and not one you'd guess from the first index alone.
    `viewer.py` holds the import-light `Viewer(user_id, is_admin)`. **`Viewer(None, False)` (an
    ownerless API key — owns nothing, not admin) and `viewer=None` (no viewer at all) are
    different things**: the latter is unscoped access, used only by the local CLI operator on the
    box; reading "no `user_id`" as "unscoped" would hand a legacy key the run of the library. An
    API key's `owner_id` is nullable (legacy only — both the HTTP route and `auris keys add` now
    require an owner at mint time), and an ownerless key can authenticate, read and synthesise but
    can never create or modify a voice. `auris clone` on the box always targets the operator's own
    (unowned) namespace, never a global by-name match, so it can never append to a signed-in
    user's voice.
- **`auris/audio/`** — `io.py` (load/save/resample), `reference.py` (fit an upload to a cloning
  reference — see the 10 s gotcha below), `record.py` (mic),
  `playback.py`, `stream.py` (background-worker streaming synthesis), `transcribe.py` (ASR).
  - **`transcript.py` holds the STT result types** — frozen `Word` / `Segment` / `Transcript` plus
    `TranscribeOptions` (`model`, `language`, `task`, `word_timestamps`, `vad_filter`,
    `initial_prompt`; `None` means "take the server default"). No heavy imports; the server
    renders these, it doesn't re-invent them.
  - **`transcribe.py` splits load from decode**, because the pool needs the halves separately:
    `load_whisper` builds a **fresh, uncached** model (N pool workers must not share one
    instance), `get_whisper` is the cached counterpart for the single-user local path,
    `run_whisper(whisper, audio, sample_rate, options)` is a **pure** decode (no settings lookup,
    no model resolution) so a worker thread can call it. `transcribe_detailed(...) -> Transcript`
    is the local entry point; `transcribe_reference(...)` is the cloning decode (with
    `reference.clone_options`), and `transcribe_audio(...) -> str` its text-only form.
  - **Cloning forces `language="en"` and `vad_filter=False`, and asks for word timestamps (one
    place: `reference.clone_options`); the STT surface auto-detects.**
    Deliberate: an accented English reference auto-detected as another language would degrade
    f5's conditioning, and the VAD can drop a leading word and desynchronise audio from
    transcript. Don't "unify" these.
  - **STT is configurable, allowlist-gated, and GPU by default.** `asr_models.py` holds the
    curated faster-whisper catalogue (`tiny.en` → `large-v3-turbo`) plus `resolve_asr_model`,
    which maps a caller's id — including OpenAI's `whisper-1` alias and `None` — onto a real
    faster-whisper id or raises `ValueError` (→ 422). `AURIS_ASR_MODEL` / `_DEVICE` /
    `_COMPUTE_TYPE` set the defaults (`auto` = GPU, float16; CPU falls back to int8);
    `AURIS_ASR_MODELS` narrows what remote callers may request (empty ⇒ the whole catalogue).
    `_resolve_runtime` exists because **ctranslate2 takes the GPU ordinal as a separate
    `device_index` and does not parse `"cuda:1"`**, and has no Metal backend (mps → cpu).
    The model cache is keyed on the whole `(model, device, index, compute_type)` tuple.
    Remote callers pick per request (`model` on `/v1/audio/transcriptions`, `asr_model` on
    the clone upload); `auris asr list|pull` browses and pre-downloads the catalogue.
- **`auris/text/chunker.py`** — sentence splitting, used by streaming.
- **`auris/server/`** — FastAPI service (`auris-server`): reuses the pure core; a `SynthesisPool`
  holds N device-pinned resident models (parallel up to N, bounded queue → `429`); routers cover
  `/v1/tts`, `/v1/tts/stream`, OpenAI `/v1/audio/speech` + `/v1/audio/transcriptions`,
  `/v1/audio/models` (the STT allowlist), and `/v1/voices`.
  - **`voice_access.py` is the only way a router reaches a voice.** It owns every voice
    permission rule, and a structural test forbids routers importing `voice_library.speakers`
    directly. The rule, compactly: a shared voice is usable by all and writable only by its owner
    or an admin; another account's private voice answers `404`, not `403`, so the status code
    can't confirm the name exists; a legacy unowned voice is readable by all and writable only by
    an admin until `auris voices adopt` claims it. **Cloning onto a name another account holds
    *privately* creates the caller's own distinct voice** (the per-owner namespace) and returns
    `201` — deliberately not an error, since a 404 or 409 there would reintroduce the
    name-squatting and existence-leak the namespace removed; the **shared** case is the one that
    is a `403`. This departs from the plan's Review Focus #2, which wanted a 404.
  - **`asr_pool.TranscriptionPool` mirrors `SynthesisPool` for STT** — `AURIS_ASR_WORKERS`
    resident device-pinned whisper models, `AURIS_QUEUE_MAXSIZE` waiters, overflow → `PoolFull`
    → `429`. A checkout is a *concurrency slot* carrying a default model: a job naming another
    allowlisted model decodes on a separately cached instance but still occupies the slot, so
    the GPU never sees more than N concurrent decodes. `build_asr_pool` runs at startup, off
    the event loop. **`pool.admit()` is the same budget applied one step earlier**: the STT
    route holds it across the decode too (`workers + queue_maxsize` slots, exhaustion →
    the same `PoolFull` → `429`), so a caller that would be rejected anyway never allocates
    a decoded float32 array; `AURIS_ASR_MAX_SECONDS` caps one request's share (→ `422`). **The clone upload transcribes through this same pool**, so cloning and
    transcription share one admission-control budget — and with `AURIS_STT_ENABLED=false` a
    clone without `ref_text` gets `503`, while one with `ref_text` still works.
  - **Each pipeline is independently switchable.** `AURIS_STT_ENABLED` / `AURIS_TTS_ENABLED`
    decide which pools the lifespan builds (both off ⇒ the server refuses to start);
    `deps.get_asr_pool` / `get_pools` turn a missing pool into `503 pipeline_disabled` naming
    the env var. `/v1/audio/models` reports `"pipelines": {"stt": …, "tts": …}` so a client can
    discover this without probing.
  - **`transcript_format.render(transcript, response_format, word_timestamps, task)`** is the one
    renderer for `json | verbose_json | text | srt | vtt`, shared by the HTTP route *and* the CLI
    (`auris transcribe --format`) so the two can't drift. It returns `(body, content_type)`; a
    `dict` body goes back as JSON.
  - **Three auth principals: `master`, `key` and `user`** (`server/auth.py`'s `Principal`
    dataclass: `kind` / `admin` / `user_id` / `must_change_password`). `master` is the shared
    `AURIS_AUTH_TOKEN` — break-glass admin, so a broken `users` table can never lock an operator
    out. `key` is a named,
    revocable API key (`voice_library/keys.py`, table `api_keys`; names `[A-Za-z0-9._-]{1,64}`,
    `admin` reserved) — a **machine** credential, **never admin whoever owns it**, so a leaked key
    can consume the service but not extend its own foothold. `user` is a signed-in **human**
    holding a session cookie (`voice_library/users.py`, table `users`), admin iff their `role` is.
    A cookie means a human; a bearer means a machine — an API key cannot open a session, and the
    master token cannot sign in at `/auth/login` either, only at `/v1/*`. `server/auth.py`'s
    `auth_dependency` accepts either a bearer or the dashboard's signed session cookie
    (`server/session.py` — stateless, keyed off the master token, so `token.sh rotate`
    invalidates every session at once); a cookie request to `/v1/*` also needs `X-Auris-Client:
    dashboard` (CSRF — a custom header can't be attached cross-origin without a CORS preflight,
    and the server never enables CORS). Changing a password bumps `password_version`, which
    `auth.resolve_session` re-checks on every request, so it invalidates every *other* live
    session for that account immediately. `routers/keys.py` (`/v1/keys` list/issue/revoke, scoped
    to the caller's own keys unless admin) and `routers/users.py` (`/v1/users` list/create/
    reset/disable/enable, admin-only) both sit behind `require_control_plane`, which refuses a
    `key` principal outright — a leaked key must never mint further credentials or accounts — and
    `/v1/users` additionally needs `require_admin`. `include_in_schema=False` on both: there is no
    reason to advertise credential management on the unauthenticated `/openapi.json`. `auris keys
    add|list|revoke` and `auris users add|list|reset|disable|enable` are the CLI equivalents and
    both refuse to run in remote mode, pointing at the admin API instead. `routers/info.py`
    (`GET /v1/info`) reports server config (pipelines, model/device info, limits) to any
    authenticated principal, for the dashboard's Settings page.
  - **`auris/server/static/`** — the dashboard: multi-file vanilla ES modules, no build step, no
    CDN. Markup lives in `index.html`, behaviour in `js/*.js` — one module per page
    (`voices`/`synth`/`transcribe`/`settings`/`account`/`users`) plus `app.js` (shell, nav),
    `api.js`/`auth.js`/`prefs.js`, and small shared UI modules: pure `view-logic.js`, `avatar.js`,
    `waveform.js` and `passages.js` (the New voice dialog's reading passages and record countdown;
    node-tested in `tests/js/`), and DOM `player.js` (waveform player over a
    hidden `<audio>`), `dialog.js` (in-page confirm/prompt — **never `window.confirm`/`prompt`**,
    a test enforces it), `dropzone.js`, `segmented.js`, `clipboard.js` (off a secure context —
    the tailnet page — it copies through a throwaway textarea, and only then falls back to
    selecting text). Styles are `app.css`, built on the
    "Aurora" tokens at its top; Inter is self-hosted in `static/fonts/` (OFL). **Keep every
    element id the runbook and `test_dashboard.py` use** — `test_every_named_import_is_exported`
    also guards the module graph, which `node --check` can't see. `package.json` is
    `{"type":"module"}` for `node --check`/`node --test` only, not a build — those checks run
    from `tests/server/test_static_js.py` and skip cleanly when `node` isn't on the machine.
    Served via `app.mount("/static", StaticFiles(...))`, never at `/`, so it can't shadow an API
    route.
- **`auris/client/`** — the **remote-client profile** (no torch). `config.py` stores the service
  URL + token at `~/.config/auris/client.env` (env vars override; the CWD is deliberately **not**
  trusted; token written atomically at 0600). `api.py` is an httpx wrapper; `mode.py`'s
  `remote_client()` returns a client iff a URL is configured. The CLI verbs (`record`/`tts`/`say`/
  `voices`/`clone`/`transcribe`) branch on it: **remote mode** calls the service (record =
  mic→upload, transcribe = file/mic→upload), else the local model path runs — same commands,
  location-transparent. `auris login` stores the URL plus, optionally, whatever bearer it's
  given — the shared master token or a named API key work identically here; `--no-token` (what
  `setup-client` runs) asks for the URL only. `proxy.py` + `auris dashboard` serve the bundled
  dashboard from **localhost** (a secure context, so the browser mic works) and forward
  `/auth/*`, `/v1/*`, `/ready` and `/health` to the service. **The proxy holds no credential**:
  the page signs in exactly as on the server, and the proxy only renames the session cookie on
  the way through, dropping `Secure` and `Domain`, and forwards no other cookie upstream. The
  browser name is `auris_session_<port>_<hash of the server URL>`: cookies are scoped per host,
  not per port, and this one lives 12 hours, so a name without the server in it would hand one
  server's session to whichever server the next proxy on that port fronts. The cost of the
  design: a host-scoped cookie is also sent to any other service on 127.0.0.1 the browser
  visits (it is HttpOnly, so no page script can read it). The page has no "proxy mode".
  Tokens/keys are never accepted as CLI args (hidden prompt / stdin); admins manage the server
  token with `scripts/token.sh show|set|rotate`.
- **`auris/config.py`** — `Settings` (pydantic-settings, env prefix `AURIS_`, reads `.env`).
  `load_settings()` constructs fresh each call so tests stay isolated. Data lives under
  `data/` (`voices/`, `auris.db`); internal/output sample rate is **24 kHz**.

### Data flow
- **clone/record**: load audio → resample to 24 kHz → word-timestamped decode of the first ~15 s
  (unless `--ref-text`) → `reference.reference_for_clone` (lead-in, **≤10 s ending in a pause**,
  transcript = the kept words) → store WAV + DB row.
- **tts/say**: `load_speaker_reference` (latest clip + transcript) → `build_model` →
  `synthesise`. `tts` writes a WAV; `say` streams chunks to live playback.
- **transcribe**: load audio (or record, **uncapped** — the 10 s cap is a cloning constraint) →
  `transcribe_detailed` locally / `POST /v1/audio/transcriptions` in remote mode → `render` to
  `text | json | verbose_json | srt | vtt`.

## Conventions & gotchas

- **The 10-second reference cap is deliberate, not arbitrary, and so is where it cuts.** f5-TTS
  re-clips references over ~12 s internally but keeps the full transcript we pass, and that
  length mismatch leaks the reference's trailing word into every generation — so don't raise the
  cap. And f5 generates the new speech as a *continuation* of the reference, so a reference that
  ends part-way through a word makes it finish that word at the start of the output. Measured
  with fixed seeds: an intact clip leaked its last word in 1 of 24 generations, the same clip
  with its last 0.15 s removed in 22 of 24; trailing silence made no difference. Hence
  `audio/reference.py`: the cut is placed by **audio energy inside a pause** (never by a blind cap,
  and never by Whisper's word end times, which run early), a clip that already stops at full
  level is backed up to its last pause, and the transcript is the words of the same decode that
  fall before the cut. An ending only counts as finished when quiet follows it — loudness proves
  nothing (a cut in the soft "s" of "Science" ends quietly and still leaks). A typed transcript
  is refused when the cut drops speech. For clips stored before this, `auris voices refit` tests
  by ear instead: it has each voice speak six sentences and counts stray leading words
  (`reference.extra_leading_words`), because most old clips were trimmed tight and so have no
  quiet tail whether or not their last word is whole.
- **`load_audio` (`audio/io.py`)** tries libsndfile first, falls back to FFmpeg for mp3/m4a/mp4.
  It deliberately re-raises libsndfile `SF_ERR_SYSTEM` (code 2 = OS open failure) instead of
  routing missing/permission-denied files to FFmpeg. FFmpeg is a real runtime dependency.
- **`runtime.ensure_valid_hashseed()`** runs in the Typer callback before every command — an
  empty `PYTHONHASHSEED` crashes f5-TTS's spawned worker processes. Leave it in place.
- Every model wrapper must output **float32 mono PCM at its declared `sample_rate`**; resample to
  24 kHz if the model's native rate differs (f5-TTS does this in `synthesise`).
- All on-disk WAVs are **16-bit PCM** (`io.save_wav` subtype `PCM_16`).

## Testing patterns

- The fast suite never touches the real model. CLI tests `monkeypatch` `build_model` to return a
  `_FakeModel` (see `tests/cli/test_tts.py`); audio/transcription tests mock the lazy imports.
- Use the `cli_data_dir` fixture (`tests/cli/conftest.py`) — it sets `AURIS_DATA_DIR` to a tmp
  dir so the CLI reads/writes an isolated `data/`.
- DB tests connect to `:memory:`.
- Real inference goes behind `@pytest.mark.requires_model`.
- **`tests/e2e/` is the end-to-end tier** (`pytest -m e2e`, excluded by default alongside
  `requires_model`; an e2e test carries only the `e2e` marker, so `-m requires_model` stays the
  quick weights check). `tests/e2e/server.py` launches a real `auris-server` subprocess on a
  temp data dir and a free port, seeds a key and a voice, and tears it down — its `build_env`
  is deliberately explicit so no stray `.env` or exported `AURIS_*` can change what is under
  test. `tests/e2e/audio.py` holds the WAV fixtures, `assert_audible` and a hand-rolled `wer()`.
  The centrepiece is the **TTS→STT round trip**, the only test that can catch a sample-rate or
  dtype bug spanning the whole audio stack.
- **Launch the server with `python -m auris.server.run`, never the `auris-server` console
  script.** The console script resolves through the venv entry point and can import a
  *different* checkout than the one under test.
- Dashboard coverage is a browser runbook, not a framework — see `docs/testing.md`.
