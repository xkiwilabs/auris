# Testing auris

Four tiers. The first is what you run constantly; the rest are opt-in, because they need
weights, a GPU, or a browser.

| Tier | Command | Proves | Cost |
|---|---|---|---|
| fast | `pytest` | every unit; no weights, no GPU | ~15 s |
| model | `pytest -m requires_model` | the models load and infer | ~1 min |
| e2e | `pytest -m e2e` | the shipped server, client and CLI hold together | ~30 s warm |
| dashboard | browser runbook (maintainers) | every dashboard journey in a real browser | ~10 min |

The default `pytest` run excludes both `requires_model` and `e2e` (`addopts` in
`pyproject.toml`), so it never downloads weights and never needs a GPU.

## The e2e suite

`pytest -m e2e` launches a **real `auris-server` subprocess** on a free port with a temporary
data directory and a freshly generated token, waits for `/ready`, seeds an API key and a cloned
voice, then drives it over HTTP exactly as a client would. It never touches `./data`, and never
talks to the production service.

```bash
pytest -m e2e                                      # the whole suite
pytest -m e2e tests/e2e/test_transcription.py -q   # one file
AURIS_E2E_DEVICE=cuda:1 pytest -m e2e              # use the second GPU
```

**Pick a free GPU.** The suite pins one device (default `cuda:0`) and loads f5-TTS, Kokoro and
whisper onto it. If the production service is warm on `cuda:0`, set `AURIS_E2E_DEVICE=cuda:1` —
otherwise the two compete for memory and a cold load can fail.

**When a run fails at start-up**, the error message contains the tail of the server's own log,
which almost always names the cause (port in use, missing weights, out of memory). That log
lives in the instance's temp data directory, which is deleted on exit.

What it covers:

- health, `/ready`, and `/v1/info`;
- all three auth principals (master token, API key, user session) and the cookie + CSRF contract;
- accounts end to end: signing in, a wrong password and an unknown email answering identically, a
  fresh account's forced password change blocking every other route until it's done, a password
  change ending every *other* live session for that account, an admin-only `/v1/users`, and a
  disabled account losing access immediately — plus the full key lifecycle (a user's own key
  works as a bearer and is invisible to other users; revoking a key kills its live session);
- cloning with *and* without a transcript (the second runs the real whisper pool), clips, the
  last-clip refusal, rename and delete;
- synthesis: full, OpenAI-shaped (mp3 and wav), and streaming — asserting the audio is real and
  non-silent, not merely that the status was 200;
- the **TTS→STT round trip** under a word-error-rate bound, plus every transcript format;
- admission control (a burst gets `429`) and both pipeline switches;
- the remote `ServiceClient` and the CLI in remote mode, as real subprocesses.

### Running the server by hand

The same launcher runs standalone — useful for poking at a throwaway instance, and what the
dashboard runbook uses:

```bash
python -m tests.e2e.server          # prints base_url / token / key / accounts, runs until stopped
python -m tests.e2e.server --keep   # ... and leaves the temp data dir behind for inspection
```

Its printed JSON includes a seeded admin and a seeded non-admin account (`admin_email` /
`admin_password`, `user_email` / `user_password`) — both already past their forced first
password change, so the dashboard runbook can sign straight in with them.

**Always `python -m`, never the `auris-server` console script.** The console script resolves
through the venv's entry point and can import a *different* checkout than the one you are
testing — which is exactly the kind of bug that costs an afternoon.

## The dashboard pass

The maintainers run a browser acceptance pass by hand, driven through the Claude-in-Chrome
extension from a runbook kept with the development tooling rather than in this repository. It
starts a throwaway server, opens a tab, and walks forty-one journeys — email/password sign-in
(good and bad), every page, the voice library (sample, add a clip, delete a middle clip, the
last-clip refusal, rename, search), the New voice dialog, synthesis full and streamed (and
stopped mid-stream), transcription with srt/vtt export, settings and its jump links, a user's own
key from the Account page (issue, rotate, revoke), the admin-only Users page (create and disable
an account), a fresh account's forced password change blocking every page until it's done,
sign-out, voice ownership and visibility, a hostile voice name, and the phone layout — each as an
action plus a JavaScript probe with an expected value. It finishes with a pass/fail table and a
console-error check.

There is no Playwright and no browser binary to maintain: this suite runs by hand a few times a
month, on one machine, with a person in front of it. If that ever changes, the journey table in
the skill converts to specs mechanically.

## Production

`scripts/test-service.sh` is a different thing and stays as it is: a post-deploy smoke test that
curls the **running** service (clone a throwaway voice, synthesise, delete it). Run it after
`sudo systemctl restart auris-server`.
