<div align="center">

<h1><img src="auris/server/static/icon.svg" width="44" height="44" alt="" align="center">&nbsp;&nbsp;a u r i s</h1>

### Clone a voice from a few seconds of audio — then speak any text in that voice.

**Local-first · 100% offline · no cloud, no accounts, nothing ever uploaded.**

<p>
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/100%25-offline-2ea44f" alt="100% offline">
  <img src="https://img.shields.io/badge/voices-f5--TTS%20%2B%20Kokoro-8957e5" alt="f5-TTS + Kokoro">
</p>

**[Remote users](docs/remote-users.md)&nbsp;·&nbsp;[Getting started](docs/getting-started.md)&nbsp;·&nbsp;[Deploy](docs/deploy/README.md)&nbsp;·&nbsp;[Usage](docs/usage.md)&nbsp;·&nbsp;[Troubleshooting](docs/troubleshooting.md)**

</div>

---

auris takes a short sample of someone speaking and generates new speech in their voice, entirely on
your own hardware. Nothing is uploaded — no cloud, no accounts, no audio ever leaves the machine.

Use it whichever way suits you:

- 🌐 a **web dashboard** in your browser — nothing to install,
- 💻 a **command-line tool** (`auris`) on your own computer,
- 🔌 an **HTTP API** (OpenAI-compatible) for your apps and research agents.

It also ships with **ready-to-use voices**, so you can make speech immediately — before cloning anything.

---

## 👉 Start here — which one are you?

Find the row that fits, do the **Quick start**, then open its **guide**. You can ignore the rest of
this page.

| You are… | Quick start | Full guide |
|----------|-------------|------------|
| 🌐 **A user of a service your team already runs.** Someone set up auris on a workstation and gave you a **web link and an account**. No GPU needed. | Open the link in a browser and sign in with your email and password — or, for mic recording + a CLI, `./scripts/setup-client.sh` then `auris dashboard`. | **[→ Remote user guide](docs/remote-users.md)** |
| 🖥️ **Setting up the shared service** for your team, on a GPU / Ubuntu workstation. | `git clone <repo-url> auris && cd auris`<br>`scripts/setup-workstation.sh`<br>`scripts/install-service.sh`<br>`auris users add you@example.com --role admin` | **[→ Deploy guide](docs/deploy/README.md)** |
| 💻 **Using auris on your own computer** — clone a voice and generate speech from the terminal. | `git clone <repo-url> auris && cd auris`<br>`./scripts/install.sh --model` | **[→ Getting started](docs/getting-started.md)** |
| 🔧 **A developer** who wants to change or extend auris. | `./scripts/install.sh` then `pytest` | **[→ Architecture](docs/architecture.md)** |

> **Not sure?** If you didn't set anything up yourself and someone handed you a **web address and
> a sign-in** (an email + password, or an API key/token), you're the first row — the
> [remote user guide](docs/remote-users.md) is all you need.

---

## What auris can do

- **Clone a voice** from about 10 seconds of speech — record it live or upload an audio file.
- **Generate speech** from any text in that voice — save it to a file or stream it as it plays.
- **Use built-in voices** (`narrator-uk-f`, `calm-m`, …) with no cloning, to get going instantly.
- **On a shared service, your cloned voices are yours** — private by default, shareable with a
  flag when you want a teammate to use one.
- **Reach it three ways** — a browser dashboard, the `auris` command line, and an OpenAI-compatible
  API your code and agents can call.
- **Stay private** — everything runs on your machine or your team's workstation; audio never leaves it.

---

## A 30-second taste

**In the browser** (remote users): open the dashboard, pick a voice, type, press **Speak**.

**On your own computer** (command line):

```bash
auris voices                                   # see built-in voices + your cloned ones
auris tts narrator-uk-f --text "Hello there." --out hello.wav   # a built-in voice, no cloning
auris record "Me"                              # record ~14s and clone your own voice
auris say "Me" --text "Now it sounds like me." # stream it live in your voice
```

**From code** (any machine, against a running service):

```python
from openai import OpenAI
client = OpenAI(base_url="http://your-server:8011/v1", api_key="<token>")
client.audio.speech.create(model="f5_tts", voice="narrator-uk-f",
                           input="Generated from my script.").stream_to_file("out.mp3")
```

---

## Documentation

**Using auris**
- **[Remote user guide](docs/remote-users.md)** — use a service your team runs (dashboard, examples,
  API). No install, no GPU.
- **[Getting started](docs/getting-started.md)** — set up auris on your own computer and clone your
  first voice, step by step.
- **[Usage reference](docs/usage.md)** — every command and option.
- **[Speech-to-text](docs/stt.md)** — `auris transcribe`, model choice, the transcription API.
- **[Troubleshooting](docs/troubleshooting.md)** — fixes for the snags you're most likely to hit.

**Running the shared service**
- **[Deploy guide](docs/deploy/README.md)** — stand up the HTTP service on a GPU workstation.
- **[Accounts](docs/accounts.md)** — create the first dashboard account (`auris users add
  <email> --role admin`), accounts vs. API keys, resetting a forgotten password.

**For developers**
- **[Architecture](docs/architecture.md)** — how auris is put together, and the conventions that
  are load-bearing.

---

## How it works (the short version)

You give auris a short **reference clip** of a voice plus the words spoken in it. To generate, it
listens to that reference and speaks your new text in the same voice — there's no training step and
nothing is sent anywhere. One clean ~10-second clip is all it needs; auris uses a speaker's most
recent clip.

Two engines sit behind the same simple interface: **f5-TTS** does the voice **cloning**, and
**Kokoro** provides the **built-in preset voices**. You never pick an engine — the voice name does.

---

## Models & licensing

- **[f5-TTS](https://github.com/SWivid/F5-TTS)** (cloning) — weights are **CC-BY-NC (non-commercial)**,
  which is fine for **research and personal use** (what auris is for).
- **[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)** (preset voices) — **Apache-2.0**.

auris's own code is **MIT** (see [LICENSE](LICENSE)); the model weights keep their own licenses
(see [NOTICE](NOTICE)). Generated voices from f5-TTS must not be used commercially.

### Responsible use

Cloning a voice is cloning part of someone's identity. Only clone a voice with the clear consent
of the person it belongs to, and don't use auris to impersonate anyone, deceive people, get round
voice-based security, or make content someone hasn't agreed to. When you share generated speech,
say that it is synthetic. Make sure your use also complies with the law where you are.

---

## Develop

```bash
./scripts/install.sh          # dev + recording deps (no model) into .venv
source .venv/bin/activate
pytest                        # fast suite — no model weights or GPU needed
pytest -m requires_model      # opt-in real-inference tests (needs the model extras)
pytest -m e2e                 # end-to-end: a real server, real models, real HTTP (needs a GPU)
ruff check . && ruff format .
```

The four test tiers — fast, model, end-to-end and the browser dashboard pass — are described in
**[docs/testing.md](docs/testing.md)**.

Architecture and conventions: **[docs/architecture.md](docs/architecture.md)**. Contributing (setup, tests, style, PR process): **[CONTRIBUTING.md](CONTRIBUTING.md)**.
