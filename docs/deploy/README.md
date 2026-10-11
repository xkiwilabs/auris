# Deploying auris-server (GPU workstation)

Run auris as an HTTP TTS service on an Ubuntu / NVIDIA box, reachable over a private **Tailscale**
tailnet. Three scripts do the work — no manual torch/CUDA wrangling.

## Prerequisites
- An NVIDIA GPU + recent driver. The default torch build is **cu128** (needs a CUDA-12.8-capable
  driver — check `nvidia-smi`). Older driver? override the build (see below).
- `ffmpeg` — `sudo apt install ffmpeg`
- `espeak-ng` — `sudo apt install espeak-ng` (for the built-in Kokoro preset voices)
- `uv` — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Tailscale up + logged in (`tailscale status`) for private, token-gated access.

## 1. Set up (no sudo)
```bash
git clone <repo> ~/Projects/auris && cd ~/Projects/auris
scripts/setup-workstation.sh
```
Creates `.venv` with a GPU-capable torch, installs auris (CLI + server + f5-TTS), and writes
`.env` with a generated auth token and your Tailscale bind address. For an older driver, override
the CUDA wheel index, e.g.:
```bash
AURIS_TORCH_INDEX=https://download.pytorch.org/whl/cu124 scripts/setup-workstation.sh
```

Config lives in `.env` (gitignored). Defaults: bind = your tailnet IP, port `8011`, `cuda:0`,
2 workers. Edit if needed:
```
AURIS_BIND_HOST=<tailscale-ip>   # do NOT use 0.0.0.0 on untrusted networks
AURIS_PORT=8011
AURIS_GPU_DEVICES=cuda:0         # or cuda:1, or cuda:0,cuda:1 to spread workers
AURIS_GPU_WORKERS=2              # parallel synthesis slots
AURIS_QUEUE_MAXSIZE=8            # requests waiting beyond the workers -> HTTP 429
AURIS_KOKORO_ENABLED=true        # built-in preset voices (set false for f5-only)
AURIS_KOKORO_DEVICE=auto         # auto | cuda:0 | cpu — move presets off the f5 GPU if tight
```

Once it's running, the **browser dashboard** is at `http://<bind-host>:<port>/` and the built-in
**preset voices** (Kokoro) appear in `/v1/voices` for anyone to use before cloning. If the
`kokoro` extra or `espeak-ng` isn't installed, the server logs a warning and runs f5-only — it
won't fail to start. Point teammates at [../remote-users.md](../remote-users.md).

## 2. Run as a service (sudo)
```bash
scripts/install-service.sh       # writes + enables the systemd unit, starts it
scripts/service.sh status        # | logs | restart | stop
scripts/test-service.sh          # end-to-end smoke: clone a throwaway voice + synthesise
```
The unit auto-starts on boot, restarts on failure, and is ordered after `tailscaled` so the
tailnet IP exists before it binds.

## Managing users: accounts, keys, and voice ownership

Create a dashboard **account** for each person first (`auris users add <email> --role admin` for
yourself, `--role user` for everyone else — see [../accounts.md](../accounts.md)), then issue each
person (and script) their own named **API key** instead of handing out the master token. A key is
owned by an account, and `--owner` is required at mint time:
```bash
auris keys add alice --owner alice@example.com   # prints the key once — copy it now
auris keys list                                  # name, owner, active/revoked, last used
auris keys revoke alice                          # revoke just alice, everyone else keeps working
```
Run on the workstation (`auris keys` refuses in remote mode and points at the admin HTTP API —
`GET`/`POST /v1/keys`, `DELETE /v1/keys/{name}` — instead), or from the dashboard itself: sign in
as admin and open **Models & Settings → API keys**. Keep the master token for yourself.

**Upgrading an existing box:** a key minted before `--owner` existed has no owner. It keeps
working for reads and synthesis, but it can no longer clone, rename or delete a voice — those are
refused with a message pointing the operator back here. Re-issue it: `auris keys revoke <name>`
then `auris keys add <name> --owner <email>`.

Cloned voices are owned too, and private by default. After restarting onto this version, every
*existing* voice becomes shared-and-unowned (nothing a colleague was already using stops working),
and `auris voices adopt <email>` assigns them all to one account — run this **before** anyone uses
`auris voices unshare`, because unsharing an unowned voice is refused (an unowned voice is visible
to everyone regardless of its visibility setting, so there'd be nothing to unshare until it has an
owner). `adopt` is all-or-nothing: if a legacy voice's name collides with one the target account
already owns, nothing is adopted and the error names the clash — rename or delete one side and
run it again. See [../usage.md](../usage.md#auris-voices) for `share`/`unshare`/`adopt` in full.

## Managing the master token
```bash
scripts/token.sh show            # print the current token
scripts/token.sh set             # set a token (read from a hidden prompt / stdin)
sudo scripts/token.sh rotate     # generate a NEW token, restart the service, print it
```
The master token is the `admin` credential — it's what `auris keys` and `/v1/keys` need, and it
signs every dashboard session cookie. `rotate` invalidates it immediately, **logs out every
browser session** (cookies are signed with a key derived from the master token), and restarts the
service so the change takes effect. Named API keys are unaffected by a token rotation; only the
admin's own access moves to the new token.

## Hardware notes
- **One GPU** (e.g. RTX 4090, 24 GB): `AURIS_GPU_DEVICES=cuda:0`, `AURIS_GPU_WORKERS=2`. f5-TTS is
  ~2–3 GB, so a couple of resident workers is plenty.
- **Multiple GPUs** (e.g. 2× RTX PRO 6000): spread workers with `AURIS_GPU_DEVICES=cuda:0,cuda:1`
  and raise `AURIS_GPU_WORKERS`.

## Updating
```bash
git pull && scripts/setup-workstation.sh && scripts/service.sh restart
```

## Docker (alternative)
Use the NVIDIA Container Toolkit (`nvidia-ctk runtime configure --runtime=docker`), pass the same
`.env`, and `--gpus all`. systemd is lighter for a single box and gives the same auto-restart.

## Fronting with TLS

This setup is documented as plain `http` over the tailnet, and the dashboard's session cookie is
marked `Secure` only when the *request* arrives as `https`. If you put a TLS-terminating reverse
proxy in front of auris-server, the request Uvicorn sees is still plain `http` from the proxy, so
the cookie comes back **not** `Secure` unless you also configure Uvicorn/FastAPI to trust the
proxy's forwarded headers (`--forwarded-allow-ips` / `ProxyHeaders` middleware) so it can see the
original scheme. Skipping that isn't a hard break — sign-in still works — but it's silently
weaker than it looks.
