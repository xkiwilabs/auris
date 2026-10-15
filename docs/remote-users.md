# Using a shared auris service

**This guide is for you if someone on your team runs auris on a workstation and gave you a web
link and an account (or an API key).** You don't install anything, you don't need a GPU, and no
audio ever leaves your team's machine.

**Signing in to the browser dashboard** (section 1 below) uses an **email and password** — an
account your admin created with `auris users add`. **Scripts, agents and the CLI in remote mode**
(sections 0, 2 and 3) use an **API key** instead — a separate, machine-only credential that can
never sign in to the dashboard. See [accounts.md](accounts.md) for why these are kept apart.

You only need two things, both from whoever set up the service:

| What | Looks like | Example |
|------|-----------|---------|
| **Web address** | a link with a port number | `http://your-server:8011` |
| **Your sign-in** | an email + a one-time password (dashboard), or an API key (CLI/scripts) | `you@example.com` / `auris_xY3k…` |

Older setups may still hand out one shared **token** instead of a personal key for the CLI and
scripts — everywhere below that talks about an API key, the shared token works exactly the same
way (it still isn't a dashboard credential — see section 1).

There are a few ways to use the service. **If you want to record your voice, or drive it from the
terminal/agents, install the `auris` client (next). If you just want to click around, the
no-install browser dashboard is enough.**

---

## 0. The `auris` client (recommended — gives you the mic + a CLI)

A lightweight install (no GPU, no model) that gives you the `auris` command in *remote mode* and a
**localhost dashboard where microphone recording works**.

**macOS / Linux**

```bash
git clone <repo-url> auris && cd auris
./scripts/setup-client.sh          # installs the client, then asks for the server's address
source .venv-client/bin/activate
```

**Windows (PowerShell):** `.\scripts\setup-client.ps1`, then `.\.venv-client\Scripts\Activate.ps1`.

Setup asks for one thing, the server's address. Run the `source` line in every new terminal
(otherwise `auris` is "command not found"). On a Mac, `brew install portaudio` too if setup says
it's missing — only `auris record` needs it. Then:

```bash
auris dashboard          # opens the dashboard at http://localhost — the mic Record button WORKS here
```

It opens the page in your browser, and you **sign in there** with your email and password,
exactly as at the server's own address — including choosing your own password on a brand-new
account. It asks for nothing in the terminal and holds no credential itself.

**The command-line tools** need an **API key** as well — a separate, machine-only credential
([accounts.md](accounts.md)). Make one in the dashboard (**Account → My API keys → Issue**) and
save it once with `auris config token`. The dashboard itself never needs it. Then:

```bash
auris record "Me"        # record from your mic in the terminal and upload it (clone your voice)
auris voices             # list available voices (presets + cloned)
auris tts narrator-uk-f --text "Hello." --out hello.mp3
auris say  narrator-uk-f --text "Streamed live."
auris transcribe meeting.m4a   # speech -> text, decoded on the service
```

### Turning speech into text

`auris transcribe` sends an audio file (or a fresh microphone recording) to the service and prints
the words back. Nothing is stored there.

```bash
auris transcribe meeting.m4a                                 # plain text
auris transcribe meeting.m4a --format srt --out meeting.srt  # subtitles
auris transcribe --mic --seconds 30                          # record, then transcribe
auris transcribe talk.wav --format verbose_json --word-timestamps
```

`--format` takes `text` (the default), `json`, `verbose_json`, `srt` or `vtt`. Other useful flags:
`--asr-model` (see below), `--language` (a code like `fr`, or `auto`), `--translate` (English out,
whatever went in), and `--prompt "auris, f5-TTS"` to help it spell names it doesn't know.

Two things worth knowing:

- **`--language auto` is not the same as leaving `--language` off.** Leave it off and the service
  uses whatever default its operator configured. Pass `auto` and it always detects the language
  from the audio.
- Unlike `auris record`, **`--mic` here isn't capped at ~10 seconds** — that cap only applies when
  you're cloning a voice. Record for as long as `--seconds` says.

If the service was set up without speech-to-text, `auris transcribe` says so instead of failing
oddly. Full details: [stt.md](stt.md).

Why `auris dashboard` fixes recording: it serves the page from **localhost** (a *secure context*),
which is the only way browsers allow the microphone, and passes everything else through to the
server. For agents/automation, set `AURIS_BASE_URL` + `AURIS_AUTH_TOKEN` in the environment
instead of `auris login`.

---

## 1. The dashboard — in your browser, nothing to install

This is the easy way. No setup, no code.

1. Open the **web address** in any browser (Chrome, Safari, Edge, Firefox):

   ```
   http://your-server:8011/
   ```

2. **Sign in** with your email and the password your admin gave you — not an API key: a key is a
   separate, machine-only credential, and the sign-in form doesn't take one any more (see
   [accounts.md](accounts.md) if you want the why). You're kept signed in on this browser with a
   session cookie (12 hours by default), so you don't do this every visit.

   **First time on a brand-new account?** You'll land on a "choose a new password" screen instead
   of the app — that's expected, not a bug. Pick one (12+ characters) and you're straight in. See
   the first-sign-in order in section 0 above if you're setting this up remotely and `auris
   dashboard` refused with a "must set a new password" message.

3. A sidebar on the left (on a phone, tabs along the bottom, with **More** for the rest):

   - **Voices** — a library of cards: every voice you can use, cloned voices first, then the
     built-in presets. Search by name. Press ▶ on a card to hear a sample, or click the card to
     open its panel: a waveform player, its **clips** (duration + transcript, ▶ to hear the stored
     recording the clone copies, ✕ to delete one — the last clip can't be deleted, since a voice
     needs at least one to speak), **+ Add a clip**, the **Shared with everyone** switch (on voices
     you own), and **⋯** for rename and delete. **+ New voice** opens a dialog to record or upload
     a clip. It suggests a passage to read (optional — **Show another** for a different one; the
     recording is transcribed either way). Recording starts with a short countdown while the mic
     is already listening, then runs until you press stop (30 s at most): talk for 8–15 seconds in
     a few sentences. The clone keeps up to 10 s and ends it at a pause, so a word is never cut in
     half; if the clip came out short, or had no pause to cut in, a note under the voice says so.
     **Blend voices** mixes two or three built-in voices into a new one of your own: pick the
     voices, set each one's share with its slider, and press **Save and listen** — it saves and
     plays a sample without closing, so you can adjust and save again until it sounds right. A
     blend's panel shows its mix and an **Edit mix** button instead of clips; it is private until
     you switch on **Shared with everyone**, like any voice you own.
   - **Synthesize** — type text, pick a voice, speed, **Full file** or **Stream**, and a format;
     **▶ Speak** (or Ctrl+Enter). The result plays in a waveform player with **⤓ Save**.
   - **Transcribe** — drop an audio file (or record from the mic — no 10-second cap here, that
     limit is only for cloning), pick a model/language, optionally translate to English or ask
     for word timestamps, then **Transcribe**. Copy the text or download `.txt`, `.srt` or `.vtt`.
   - **Settings** — what the server is running (models, devices, limits, allowed transcription
     models), plus your own preferences (default voice, speed, format — saved in this browser
     only, never sent anywhere but with your requests). What you last picked on **Synthesize** and
     **Transcribe** is remembered as these same defaults. They are kept per page address, so
     `auris dashboard` (`http://127.0.0.1:8765`) and the server's own address keep separate sets. If you're an admin, an **API keys**
     section here lets you issue a key for any account and see or revoke anyone's key.
   - **Account** — change your password (this ends any other browser's session for your account),
     and issue, rotate or revoke your own API keys for scripts/agents. See
     [accounts.md](accounts.md) for the full picture of accounts vs. keys.
   - **Users** (admin only) — create accounts (`auris users add`'s HTTP equivalent — the
     generated password shows once), and from each row's **⋯** reset a forgotten password or
     disable/enable the account.

4. **Sign out** from the bottom of the sidebar (under **More** on a phone) when you're done; it
   clears your session on this browser. Your account itself stays active — you (or anyone with
   your email + password) can sign in again elsewhere. (Signing out doesn't revoke an API key
   either — that's separate, from the Account page or by an admin.)

   > **If Record is blocked** on this page (plain `http` can't use the mic): use the **`auris`
   > client** and run **`auris dashboard`** — it serves the dashboard from `localhost`, where the
   > mic works (see section 0 above). Or just **upload an audio file** (record 8–15 s in any app).

> 💡 **Just want to hear something now?** Pick a **`[preset]`** voice like `narrator-uk-f` or
> `calm-m`, type a sentence, and press Speak. Presets need no cloning — cloning is only for adding
> *your own* voice.

> **For the admin — accounts and keys:** create each person's dashboard account with
> `auris users add alice@example.com` (prints the initial password once — hand it over out of
> band; she sets her own on first sign-in) or from the dashboard's **Users** page. API keys are
> separate, each one owned by an account, and still CLI-managed the same way as before:
> `auris keys add alice --owner alice@example.com` (prints the key once; `--owner` is required),
> `auris keys list` (now shows an owner column), `auris keys revoke alice` — or let people issue
> their own from their **Account** page, which is the normal path now — at the server's address
> or through `auris dashboard`, which works the same. Full detail: [accounts.md](accounts.md).
>
> **A key minted before this requirement existed has no owner.** It still authenticates and can
> still read and synthesise, but cloning, renaming and deleting are refused with a message
> pointing back here. There's no way to attach an owner in place — revoke it and issue a
> replacement: `auris keys revoke <name>` then `auris keys add <name> --owner <email>`.

That's the whole tool. The sections below are **optional**.

### Open it with one command (optional)

If you've cloned the repo, **`./scripts/dashboard.sh`** (Windows: `.\scripts\dashboard.ps1`) opens
the dashboard for you at the server's own address, where you sign in as usual. The first run asks
for the service URL (and, optionally, an API key for the example scripts below) and saves them to
a private **`client.env`** file (never committed), so you won't retype them again.

---

## 2. From your own code (optional)

If you write Python and want to call the service from a script, set up a small client first. It
installs only two lightweight packages (`requests` and `openai`) — **not** auris itself, and no
model.

**macOS / Linux**

```bash
git clone <repo-url> auris && cd auris
./scripts/setup-client.sh
source .venv-client/bin/activate
```

**Windows (PowerShell)**

```powershell
git clone <repo-url> auris; cd auris
.\scripts\setup-client.ps1
.\.venv-client\Scripts\Activate.ps1
```

Save your settings once in a **`client.env`** file (running `./scripts/dashboard.sh` creates it for
you, or make it by hand), then load it and run a ready-made example:

```bash
# macOS / Linux — create client.env once:
printf 'AURIS_BASE_URL=http://your-server:8011\nAURIS_AUTH_TOKEN=<token>\n' > client.env

set -a; source client.env; set +a        # load your saved settings
python examples/python_quickstart.py "Hello from auris." narrator-uk-f
```

```powershell
# Windows (PowerShell) — create client.env once:
"AURIS_BASE_URL=http://your-server:8011`r`nAURIS_AUTH_TOKEN=<token>" | Set-Content client.env

Get-Content client.env | % { if ($_ -match '^([^=]+)=(.*)$') { Set-Item "Env:$($matches[1])" $matches[2] } }
python examples\python_quickstart.py "Hello from auris." narrator-uk-f
```

`client.env` is gitignored, so your token never gets committed.

The **[`examples/`](../examples/)** folder has more: `clone_and_speak.py` (clone a voice, then speak),
`openai_sdk.py` (the OpenAI SDK), and `curl.sh` (plain command-line calls).

---

## 3. For apps and AI agents (OpenAI-compatible)

auris speaks the same API as OpenAI's text-to-speech, so any OpenAI client works — just point it at
the service and use your token as the API key:

```python
from openai import OpenAI

client = OpenAI(base_url="http://your-server:8011/v1", api_key="<token>")
audio = client.audio.speech.create(
    model="f5_tts",                 # any value works; the *voice* picks the engine
    voice="narrator-uk-f",          # a preset, or someone's cloned voice
    input="Generated from my research script.",
)
audio.stream_to_file("out.mp3")
```

The **voice name** decides everything: a preset name uses a built-in voice, a cloned name uses that
person's voice. Audio comes back as **mp3** by default — pass `response_format="wav"` (or `"opus"`,
`"pcm"`) if you need another format.

---

## Reference: endpoints

Most people never need this — the dashboard and examples cover it. But if you're calling the API
directly:

| Method | Path | What it does | Auth |
|--------|------|--------------|------|
| `GET`  | `/` | the browser dashboard | no |
| `GET`  | `/ready` | is the service up? | no |
| `GET`  | `/v1/voices` | list voices (presets, cloned and blends — a blend has `kind: "blend"` and its recipe in `blend`) | yes |
| `POST` | `/v1/voices/{name}/clone` | add a voice (upload audio) | yes |
| `PUT`  | `/v1/voices/{name}/blend` | create or re-mix a blend of 2–3 presets: `{"components": [{"voice": "narrator-uk-f", "weight": 60}, {"voice": "bright-uk-f", "weight": 40}], "visibility": "shared"}` (`visibility` optional; `?owner=<email>` picks which account's blend to re-mix when the name is held twice) — `201` created, `200` re-mixed | yes |
| `DELETE` | `/v1/voices/{name}` | remove a cloned voice | yes |
| `POST` | `/v1/tts` | generate speech (one file back) | yes |
| `POST` | `/v1/tts/stream` | generate speech (streamed, low latency) | yes |
| `POST` | `/v1/audio/speech` | OpenAI-compatible generation | yes |
| `POST` | `/v1/audio/transcriptions` | OpenAI-compatible speech-to-text | yes |
| `GET`  | `/v1/audio/models` | which speech-to-text models this service accepts | yes |

For the authenticated calls, send your token in a header: `Authorization: Bearer <token>`.

### Speech-to-text: `POST /v1/audio/transcriptions`

auris speaks OpenAI's transcription API too, so an OpenAI client can call this directly. By hand
it's `multipart/form-data`:

| Field | Required | Default | What it does |
|-------|----------|---------|--------------|
| `file` | yes | — | The audio. Any format FFmpeg or libsndfile reads, including the browser's webm/opus. |
| `model` | no | the service's default | A model id from `GET /v1/audio/models`. Leaving it out — or sending OpenAI's `whisper-1` — means "your default". |
| `language` | no | the service's default | A language code, or `auto` to always detect. |
| `task` | no | `transcribe` | `transcribe`, or `translate` for English output. |
| `response_format` | no | `json` | `json`, `verbose_json`, `text`, `srt`, `vtt`. |
| `prompt` | no | — | Biases the spelling of names and jargon. |
| `timestamp_granularities[]` | no | — | Send `word` for per-word timings (shown by `verbose_json`). |

```bash
curl -fsS -H "Authorization: Bearer <token>" \
  -F file=@meeting.m4a -F response_format=srt \
  http://your-server:8011/v1/audio/transcriptions -o meeting.srt
```

Errors come back as `{"error": {"code": …, "message": …}}`:

| Status | `code` | Meaning |
|--------|--------|---------|
| `400` | `bad_audio` | The file couldn't be decoded. |
| `401` | `unauthorized` | Missing or wrong token. |
| `422` | `invalid_request` | Unknown `model`, bad `task`/`response_format`, empty file, or too big. |
| `429` | `overloaded` | Everything's busy. Wait a moment and retry. |
| `503` | `pipeline_disabled` | This service runs without speech-to-text. The message names the setting (`AURIS_STT_ENABLED`) for the operator. |
| `504` | `timeout` | The service took too long. |

`GET /v1/audio/models` reports which pipelines are on — `{"pipelines": {"stt": true, "tts": true}}`
— so you can check before calling rather than guessing from a `503`.

### Choosing the speech-to-text model

When you clone a voice, the service transcribes your clip so the stored transcript matches what
you said. It uses its own default model, but you can ask for a different one — run
`auris asr list` (or `GET /v1/audio/models`) to see what the service accepts, then:

```bash
auris record "Me" --asr-model small.en          # record + upload, transcribed with small.en
auris clone  "Me" --ref clip.wav --asr-model medium.en
```

Over HTTP it's an optional form field — `asr_model` on `/v1/voices/{name}/clone`, `model` on
`/v1/audio/transcriptions`. An unknown or not-allowed id comes back as `422`; sending nothing
(or OpenAI's `whisper-1`) uses the service default. Bigger models are more accurate and slower,
and the first use of one the service hasn't downloaded yet will pause while it fetches.

---

## Something not working?

- **The page won't load / "can't reach the service"** — double-check the web address and that
  you're on the same network/VPN as the workstation (often Tailscale). Ask the person who runs it.
- **"email or password not accepted"** on the dashboard — this one message covers a wrong
  password, a mistyped email, *and* a disabled account on purpose (so a failed sign-in can't be
  used to check whether an address is registered). Double-check both fields and try again; if
  you're sure they're right, ask your admin whether your account is disabled.
- **"choose a new password" right after your first sign-in** — expected on a brand-new account:
  the password your admin gave you is temporary. Pick your own (12+ characters) and carry on.
- **An API key stopped working ("invalid bearer token")** — it may have been revoked. Ask whoever
  manages your key for a new one, or rotate it yourself from the dashboard's **Account** page if
  it's yours.
- **No `[preset]` voices in the list** — the service may be running without them; ask the operator
  (it's a one-line config + install on their end).
- **"speech-to-text is disabled on this server"** — the operator switched transcription off.
  `auris transcribe` won't work at all, and **adding a voice now needs you to type the transcript**
  (`auris clone "Me" --ref clip.wav --ref-text "the exact words in the clip"`), because the service
  can't write one for you. Ask the operator to re-enable it if you need it.
- **Microphone won't record** ("Recording is blocked…") — plain-`http` pages can't use the mic.
  Use the **`auris` client** and run **`auris dashboard`** (serves from `localhost`, where the mic
  works), or **upload an audio file** instead, or record from the terminal with `auris record`.

More fixes: **[troubleshooting.md](troubleshooting.md)**.
