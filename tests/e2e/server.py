"""Launch a real auris-server for the end-to-end suite.

One launcher, two callers: the pytest fixtures in `conftest.py`, and `python -m tests.e2e.server`
for the browser runbook. Both get the same server in the same known state, which is what keeps
the two halves of the suite describing one system.

Never touches ./data, never binds 0.0.0.0, never uses the production token.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

SEEDED_VOICE = "e2e-voice"
ADMIN_VOICE = "e2e-admin-voice"  # owned by the admin account, left private (Ruling #22)
SEEDED_KEY_NAME = "e2e-client"
SEEDED_ADMIN_EMAIL = "e2e-admin@example.com"
SEEDED_USER_EMAIL = "e2e-user@example.com"
# >= passwords.MIN_LENGTH (12); settled onto both seeded accounts after provisioning so the
# rest of the suite can sign in without ever seeing the one-time generated password.
SEEDED_PASSWORD = "e2e-settled-password-00"
_READY_TIMEOUT_S = 240.0  # a cold f5 + Kokoro + whisper load is slow the first time
_STOP_GRACE_S = 10.0
_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Instance:
    base_url: str
    token: str  # master token => the `admin` principal
    key: str  # a named API key => a non-admin principal
    data_dir: Path
    log_path: Path
    admin_email: str = SEEDED_ADMIN_EMAIL
    user_email: str = SEEDED_USER_EMAIL
    admin_password: str = SEEDED_PASSWORD
    user_password: str = SEEDED_PASSWORD
    admin_voice: str = ADMIN_VOICE  # owned by admin_email, private

    def client(self, bearer: str | None = None) -> httpx.Client:
        """An httpx client pointed at this server, authenticated as `bearer` (default: admin)."""
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {bearer or self.token}"},
            timeout=180.0,
        )

    def log_tail(self, n: int = 60) -> str:
        return _tail(self.log_path, n)


def free_port() -> int:
    """Ask the OS for an unused port. Racy in principle; nothing else on this box competes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_env(
    *, port: int, data_dir: Path, token: str, overrides: dict[str, str]
) -> dict[str, str]:
    """The child's complete environment. Explicit, not inherited: a stray .env or an exported
    AURIS_* in the shell must not be able to change what is under test.

    One GPU and one worker with a queue of one, so the admission-control assertion is
    arithmetic rather than a race, and the other card stays free for the live service.
    """
    device = os.environ.get("AURIS_E2E_DEVICE", "cuda:0")
    env = {
        # Keep the interpreter itself working.
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
        "PYTHONPATH": str(_ROOT),
        "PYTHONUNBUFFERED": "1",
        # ensure_valid_hashseed(): an empty PYTHONHASHSEED crashes f5-TTS's spawned workers.
        "PYTHONHASHSEED": "0",
        # CUDA needs these to see the devices at all.
        "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
        # --- the server under test ---
        "AURIS_DATA_DIR": str(data_dir),
        "AURIS_BIND_HOST": "127.0.0.1",
        "AURIS_PORT": str(port),
        "AURIS_AUTH_TOKEN": token,
        "AURIS_GPU_DEVICES": device,
        "AURIS_GPU_WORKERS": "1",
        "AURIS_QUEUE_MAXSIZE": "1",
        "AURIS_KOKORO_DEVICE": device,
        "AURIS_ASR_DEVICE": device,
        "AURIS_ASR_WORKERS": "1",
        "AURIS_ASR_MODEL": "small.en",
    }
    env.update(overrides)
    # Drop the pass-throughs that were unset on this machine (an empty CUDA_VISIBLE_DEVICES
    # means "all devices"; an empty string would mean "none"). Overrides survive this.
    return {k: v for k, v in env.items() if v != ""}


def _tail(path: Path, n: int = 60) -> str:
    text = path.read_text(errors="replace") if path.exists() else "(no log)"
    return "\n".join(text.splitlines()[-n:])


def _wait_ready(base_url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_S
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"auris-server exited with code {proc.returncode} before becoming ready.\n"
                f"--- server log ---\n{_tail(log_path)}"
            )
        try:
            r = httpx.get(f"{base_url}/ready", timeout=5.0)
            if r.status_code == 200 and r.json().get("ready"):
                return
            last = f"{r.status_code} {r.text[:120]}"
        except httpx.HTTPError as exc:  # not listening yet
            last = str(exc)
        time.sleep(1.0)
    raise TimeoutError(
        f"auris-server was not ready within {_READY_TIMEOUT_S:.0f}s (last: {last}).\n"
        f"--- server log ---\n{_tail(log_path)}"
    )


def _settle_account(base_url: str, token: str, email: str, role: str, password: str) -> None:
    """Provision an account through the admin API, then sign in with the one-time generated
    password and immediately change it to `password` — clearing `must_change_password` and
    leaving the account on a value the rest of the suite can use without re-reading it."""
    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=180.0
    ) as admin:
        r = admin.post("/v1/users", json={"email": email, "role": role})
        r.raise_for_status()
        generated = r.json()["password"]
    with httpx.Client(
        base_url=base_url, timeout=180.0, headers={"X-Auris-Client": "dashboard"}
    ) as c:
        login = c.post("/auth/login", json={"email": email, "password": generated})
        login.raise_for_status()
        changed = c.post(
            "/auth/password",
            json={"current_password": generated, "new_password": password},
        )
        changed.raise_for_status()


def _seed(inst: Instance) -> str:
    """Settle the two seeded accounts on a known password with `must_change_password` already
    cleared, issue the named key *owned by the admin account*, clone one unowned voice, and clone
    a second voice owned by the admin account (left private). Returns the key.

    The accounts come first, now: a key minted by the master token with no owner cannot create
    or modify a voice (see `voice_access.find_for_clone`), so `as_key` must be an *owned*
    non-admin key to stay useful against the voice routes — the realistic shape, since both
    documented ways to mint a key now require an owner anyway.
    """
    from tests.e2e.audio import REFERENCE_TEXT, reference_wav

    _settle_account(inst.base_url, inst.token, SEEDED_ADMIN_EMAIL, "admin", SEEDED_PASSWORD)
    _settle_account(inst.base_url, inst.token, SEEDED_USER_EMAIL, "user", SEEDED_PASSWORD)
    wav, _ = reference_wav()
    with httpx.Client(
        base_url=inst.base_url, headers={"Authorization": f"Bearer {inst.token}"}, timeout=180.0
    ) as c:
        r = c.post("/v1/keys", json={"name": SEEDED_KEY_NAME, "owner_email": SEEDED_ADMIN_EMAIL})
        r.raise_for_status()
        key = r.json()["key"]
        # Still cloned by the master token, so this voice stays unowned (owner_id NULL). The name
        # namespace is unique per owner, and the master token's own principal has no account
        # (owner_id NULL too) — test_rename_and_its_two_refusals in test_voices.py renames a
        # temp voice created by the *same* master token to this name and asserts 409, which only
        # holds because both land in the same owner_id-NULL namespace. Owning this voice by an
        # account instead would move it to a different namespace and silently turn that 409 into
        # a 200.
        r = c.post(
            f"/v1/voices/{SEEDED_VOICE}/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
            data={"ref_text": REFERENCE_TEXT},
        )
        r.raise_for_status()

    # A second voice, this time *owned* by the admin account — the master token owns nothing,
    # so cloning with `inst.token` would produce another unowned voice. Sign in as the admin
    # account and clone through that session instead; a cookie request to /v1/* also needs the
    # CSRF header (see auris/server/auth.py::auth_dependency).
    with httpx.Client(
        base_url=inst.base_url, timeout=180.0, headers={"X-Auris-Client": "dashboard"}
    ) as admin_c:
        login = admin_c.post(
            "/auth/login", json={"email": SEEDED_ADMIN_EMAIL, "password": SEEDED_PASSWORD}
        )
        login.raise_for_status()
        r = admin_c.post(
            f"/v1/voices/{ADMIN_VOICE}/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
            data={"ref_text": REFERENCE_TEXT},
        )
        r.raise_for_status()
        # Verify the ownership and visibility rather than assuming them: new voices default to
        # private, but the admin principal is also the only one that could see this if it were
        # unowned (admin sees everything), so a silent regression here would not show up as a
        # failed clone — it would show up as a wrong 404/403 three files away.
        voices = {v["name"]: v for v in admin_c.get("/v1/voices").json()}
        voice = voices[ADMIN_VOICE]
        if voice["owner"] != SEEDED_ADMIN_EMAIL or voice["visibility"] != "private":
            raise RuntimeError(
                f"seeded {ADMIN_VOICE!r} has owner={voice['owner']!r} "
                f"visibility={voice['visibility']!r}, expected owner={SEEDED_ADMIN_EMAIL!r} "
                "visibility='private'"
            )
    return key


@contextlib.contextmanager
def instance(*, seed: bool = True, keep_data: bool = False, **overrides: str) -> Iterator[Instance]:
    """Start a real auris-server; yield an Instance; always stop it.

    The temp data dir is deleted on exit unless `keep_data` is set (`--keep` on the command
    line), which is for inspecting a failed manual run — tests never set it.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="auris-e2e-"))
    port = free_port()
    token = secrets.token_urlsafe(32)
    base_url = f"http://127.0.0.1:{port}"
    log_path = data_dir / "server.log"
    env = build_env(port=port, data_dir=data_dir, token=token, overrides=dict(overrides))

    with log_path.open("w") as log:
        proc = subprocess.Popen(
            # `-m`, never the auris-server console script: the script resolves through the
            # venv entry point and can import a different checkout than the one under test.
            [sys.executable, "-m", "auris.server.run"],
            cwd=str(_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_ready(base_url, proc, log_path)
            bare = Instance(base_url, token, "", data_dir, log_path)
            key = _seed(bare) if seed else ""
            yield Instance(base_url, token, key, data_dir, log_path)
        finally:
            _stop(proc)
            if not keep_data:
                shutil.rmtree(data_dir, ignore_errors=True)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=_STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _raise_keyboard_interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    """`python -m tests.e2e.server` — run an instance until stopped, for the browser runbook."""
    # SIGTERM is how a background task, `timeout`, or a kill actually stops this — and the
    # default action ends the process outright, skipping the context manager's cleanup and
    # leaking a temp data dir plus a GPU-resident server. Turn it into the same exception
    # Ctrl-C raises so one shutdown path serves both.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    ap = argparse.ArgumentParser(description="Run an e2e auris-server for manual/browser testing.")
    ap.add_argument("--keep", action="store_true", help="do not delete the temp data dir on exit")
    ap.add_argument("--no-seed", action="store_true", help="skip the seeded key and voice")
    args = ap.parse_args(argv)

    with instance(seed=not args.no_seed, keep_data=args.keep) as inst:
        print(
            json.dumps(
                {
                    "base_url": inst.base_url,
                    "token": inst.token,
                    "key": inst.key,
                    "voice": SEEDED_VOICE,
                    "admin_voice": inst.admin_voice,
                    "admin_email": inst.admin_email,
                    "admin_password": inst.admin_password,
                    "user_email": inst.user_email,
                    "user_password": inst.user_password,
                    "data_dir": str(inst.data_dir),
                    "log": str(inst.log_path),
                },
                indent=2,
            ),
            flush=True,
        )
        print("\nRunning. Press Ctrl-C to stop.", flush=True)
        if args.keep:
            print(f"(--keep: {inst.data_dir} will NOT be deleted)", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nstopping…", flush=True)


if __name__ == "__main__":
    main()
