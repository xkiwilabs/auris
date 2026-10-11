from __future__ import annotations

import pathlib
import re

import auris.server

NO_AUTH = {"Authorization": ""}
STATIC = pathlib.Path(auris.server.__file__).resolve().parent / "static"


def test_root_serves_the_shell_openly(client):
    r = client.get("/", headers=NO_AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<title>auris" in body
    assert 'type="module" src="/static/js/app.js"' in body
    assert 'href="/static/app.css"' in body


def test_shell_has_tabs_panels_and_signin(client):
    body = client.get("/").text
    for marker in (
        'id="tab-voices"',
        'id="tab-transcribe"',
        'id="tab-synth"',
        'id="tab-settings"',
        'id="tab-account"',
        'id="tab-users"',
        'id="panel-voices"',
        'id="panel-transcribe"',
        'id="panel-synth"',
        'id="panel-settings"',
        'id="panel-account"',
        'id="panel-users"',
        'id="signin-form"',
        'id="signin-email"',
        'id="signin-password"',
        'id="pwchange-form"',
        'id="signout-btn"',
        'id="who"',
        'id="status-dot"',
    ):
        assert marker in body, marker


def test_shell_no_longer_has_the_token_bar(client):
    body = client.get("/").text
    assert 'id="token"' not in body and 'id="base-url"' not in body


def test_shell_carries_no_inline_script(client):
    # The page's only script is the module entry point: behaviour lives in /static/js/*.
    body = client.get("/").text
    assert body.count("<script") == 1
    assert 'type="module" src="/static/js/app.js"' in body


def test_core_modules_are_served_as_javascript(client):
    for name in ("app", "api", "auth", "prefs"):
        r = client.get(f"/static/js/{name}.js", headers=NO_AUTH)
        assert r.status_code == 200, name
        assert "javascript" in r.headers["content-type"], name


def test_api_module_sends_the_csrf_header_and_handles_401(client):
    src = client.get("/static/js/api.js").text
    assert '"X-Auris-Client": "dashboard"' in src
    assert 'credentials: "same-origin"' in src
    assert "401" in src


def test_app_module_boots_from_auth_me(client):
    assert "auth.me()" in client.get("/static/js/app.js").text
    assert "/auth/me" in client.get("/static/js/auth.js").text


def test_the_page_has_no_proxy_mode(client):
    # `auris dashboard`'s proxy used to sign in on the page's behalf and forward only /v1/*, so
    # the page had a degraded "proxied" mode: no password change, no key issue, no sign-out, an
    # admin guessed by probing. The proxy now forwards /auth/* and relays the browser's own
    # session cookie, so the page signs in the same way wherever it is served from.
    import pathlib

    js = pathlib.Path(__file__).resolve().parents[2] / "auris" / "server" / "static" / "js"
    for f in js.glob("*.js"):
        src = _code(f.read_text())
        for gone in ("proxied", "detectProxy", "probeAdmin", "/__proxy"):
            assert gone not in src, (f.name, gone)
    markup = client.get("/static/index.html", headers=NO_AUTH).text
    assert "-proxied" not in markup


def _code(src: str) -> str:
    """Drop whole-line `//` comments so a prose mention isn't read as a call."""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("//"))


def test_no_module_stores_a_secret(client):
    # The page never keeps a key or token in browser storage; prefs.js is the only module
    # allowed to touch browser storage at all, and it stores preferences only.
    for name in ("app", "api", "auth", "users"):
        src = _code(client.get(f"/static/js/{name}.js").text)
        assert "localStorage" not in src, name
        assert "sessionStorage" not in src, name
    # prefs.js is the exception, and it writes the preferences object and nothing else.
    prefs = _code(client.get("/static/js/prefs.js").text)
    assert "sessionStorage" not in prefs
    assert "localStorage.setItem(KEY, JSON.stringify(prefs))" in prefs


def test_static_mount_serves_css_openly(client):
    r = client.get("/static/app.css", headers=NO_AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert "--accent" in r.text  # the extracted theme variables


def test_static_mount_blocks_traversal(client):
    # Percent-encoded: httpx normalises a literal "/../" away client-side, so only this form
    # actually reaches the StaticFiles mount.
    assert client.get("/static/..%2fapp.py", headers=NO_AUTH).status_code in (404, 400)
    assert client.get("/static/js/..%2f..%2fapp.py", headers=NO_AUTH).status_code in (404, 400)
    assert client.get("/static/nope.css", headers=NO_AUTH).status_code == 404


def test_audio_and_synth_modules_are_served(client):
    for name in ("audio", "synth"):
        r = client.get(f"/static/js/{name}.js", headers=NO_AUTH)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
    audio = client.get("/static/js/audio.js").text
    assert "MediaRecorder" in audio and "AudioContext" in audio and "X-Sample-Rate" in audio
    synth = client.get("/static/js/synth.js").text
    assert "/v1/audio/speech" in synth and "/v1/tts/stream" in synth
    assert "initSynth" in client.get("/static/js/app.js").text


def test_voices_module_is_served_and_wired(client):
    src = client.get("/static/js/voices.js", headers=NO_AUTH).text
    for marker in ("/clips", "PATCH", "delete the voice", "auris:voices", "/audio"):
        assert marker in src, marker
    assert "initVoices" in client.get("/static/js/app.js").text


def test_recording_runs_a_countdown_and_no_longer_stops_at_ten_seconds(client):
    # A recording stopped on a 10 s timer usually ends mid-word — the cause of f5-TTS speaking a
    # stray word at the start of every generation. The user stops it now (30 s at most), the mic
    # records through a countdown so the clip opens on room tone, and the server cuts in a pause.
    src = client.get("/static/js/voices.js", headers=NO_AUTH).text
    assert "maxSeconds: 10" not in src
    assert "const REC_MAX_S = 30" in src
    assert "maxSeconds: COUNTDOWN_S + REC_MAX_S" in src
    assert "countdownStep(" in src and "rec-countdown" in src
    passages = client.get("/static/js/passages.js", headers=NO_AUTH)
    assert passages.status_code == 200 and "javascript" in passages.headers["content-type"]
    assert "When the sunlight strikes raindrops" in passages.text


def test_the_new_voice_dialog_offers_a_passage_and_explains_the_transcript_rule(client):
    body = client.get("/").text
    for marker in (
        '<dialog id="new-voice" class="modal xwide"',  # wide enough for the passage
        'id="nv-passage"',
        'id="nv-passage-title"',
        'id="nv-passage-text"',
        'id="nv-passage-next"',
        'id="rec-countdown"',
        'id="clone-reftext-hint"',
        "0:00 / 0:30",
    ):
        assert marker in body, marker
    assert "Optional" in body and "transcribed either way" in body


def test_clone_warnings_and_reference_clip_playback_are_wired(client):
    body = client.get("/").text
    assert 'id="vc-warnings"' in body and 'id="clip-audio"' in body
    src = client.get("/static/js/voices.js", headers=NO_AUTH).text
    assert "j.warnings" in src  # the clone response's warnings are shown, not dropped
    assert '"/clips/" + index + "/audio"' in src and "revokeObjectURL" in src


def test_synthesize_and_transcribe_choices_are_remembered(client):
    synth = client.get("/static/js/synth.js", headers=NO_AUTH).text
    for key in ('"voice"', '"speed"', '"format"', '"mode"'):
        assert "setPref(" + key in synth, key
    stt = client.get("/static/js/transcribe.js", headers=NO_AUTH).text
    assert 'setPref("asrModel"' in stt and 'setPref("language"' in stt
    # ...and the Settings page, which shows them as the defaults, follows along.
    assert "auris:prefs" in client.get("/static/js/settings.js", headers=NO_AUTH).text


def test_settings_module_is_served_and_wired(client):
    src = client.get("/static/js/settings.js", headers=NO_AUTH).text
    for marker in (
        "/v1/info",
        "/v1/audio/models",
        "/v1/keys",
        "you will not see this again",
        "admin-card",
    ):
        assert marker in src, marker
    app = client.get("/static/js/app.js").text
    assert app.index("initSettings") < app.index("initVoices")  # settings loads info first


def test_settings_module_keeps_the_issued_key_out_of_storage(client):
    # The one-time key is written into the DOM and nowhere else: no browser storage, no
    # cookie, and the admin card is gated on `state.admin` (which comes from the server).
    src = _code(client.get("/static/js/settings.js").text)
    assert "localStorage" not in src
    assert "sessionStorage" not in src
    assert "document.cookie" not in src
    assert "state.admin" in src


def test_transcribe_module_is_served_and_wired(client):
    src = client.get("/static/js/transcribe.js", headers=NO_AUTH).text
    for marker in (
        "/v1/audio/transcriptions",
        "verbose_json",
        "timestamp_granularities[]",
        "maxSeconds: null",
        "transcribe-disabled",
    ):
        assert marker in src, marker
    assert "initTranscribe" in client.get("/static/js/app.js").text


def test_account_module_is_served_and_wired(client):
    src = client.get("/static/js/account.js", headers=NO_AUTH).text
    for marker in ("/v1/keys", "changePassword", "account-issued", "you will not see it again"):
        assert marker in src, marker
    assert "initAccount" in client.get("/static/js/app.js").text


def test_account_module_keeps_credentials_out_of_storage(client):
    src = _code(client.get("/static/js/account.js").text)
    assert "localStorage" not in src
    assert "sessionStorage" not in src
    assert "document.cookie" not in src


def test_account_module_shows_only_your_own_keys(client):
    # /v1/keys returns every key (with its owner) to an admin. "My API keys" must filter to the
    # caller's own.
    src = client.get("/static/js/account.js", headers=NO_AUTH).text
    assert "k.owner === mine" in src
    markup = client.get("/static/index.html", headers=NO_AUTH).text
    assert '<h2 id="account-keys-title">My API keys</h2>' in markup


def test_sign_out_is_offered_to_every_signed_in_session(client):
    # The sign-in and password-change screens hide it; entering the app must always bring it
    # back. It once did so only off a "not proxied" branch.
    src = _code(client.get("/static/js/app.js").text)
    show_app = src[src.index("async function showApp") :]
    show_app = show_app[: show_app.index("\n}\n")]
    assert '$("signout-btn").style.display = "";' in show_app


def test_users_module_is_served_and_wired(client):
    src = client.get("/static/js/users.js", headers=NO_AUTH).text
    for marker in ("/v1/users", "/reset", "/disable", "/enable"):
        assert marker in src, marker
    assert "initUsers" in client.get("/static/js/app.js").text


def test_auth_module_has_login_and_change_password(client):
    src = client.get("/static/js/auth.js", headers=NO_AUTH).text
    assert "/auth/login" in src
    assert "email" in src and "password" in src
    assert "/auth/password" in src
    assert "changePassword" in src


def test_app_module_handles_the_forced_password_change(client):
    src = client.get("/static/js/app.js", headers=NO_AUTH).text
    assert "showPasswordChange" in src
    assert "must_change_password" in src
    assert "password_change_required" in src


def test_voices_module_is_wired_for_ownership(client):
    # The *decision* of who may flip a voice's visibility is a real unit test, in
    # tests/js/voices.test.mjs, against the pure mayChangeVisibility export — not a string match
    # (that pattern already rotted once, in account.js's key filter; see its comment). This is
    # just a wiring smoke test: the module imports the decision function and PATCHes visibility.
    src = client.get("/static/js/voices.js", headers=NO_AUTH).text
    assert "mayChangeVisibility" in src
    assert "voice-visibility" in src
    assert "{ visibility }" in src


def test_voices_module_escapes_the_owner_email(client):
    """Emails are user-controlled and validate_email permits < > ' and \" in the local part."""
    src = client.get("/static/js/voices.js", headers=NO_AUTH).text
    assert "escapeHtml" in src or "textContent" in src


def test_the_share_toggle_exists_in_the_markup(client):
    markup = client.get("/static/index.html", headers=NO_AUTH).text
    assert 'id="voice-visibility"' in markup
    assert 'id="voice-visibility-row"' in markup
    assert 'id="voice-owner"' in markup


_IMPORT = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w.-]+\.js)"')
_EXPORT = r"export\s+(?:async\s+)?(?:function\s*\*?|const|let|class)\s+{name}(?![\w$])"  # not \b: `$` is a name


def test_every_module_is_served_as_javascript(client):
    for path in sorted((STATIC / "js").glob("*.js")):
        r = client.get(f"/static/js/{path.name}", headers=NO_AUTH)
        assert r.status_code == 200, path.name
        assert "javascript" in r.headers["content-type"], path.name


def test_every_named_import_is_exported():
    # node --check parses one file at a time, so it can't see `import { x } from "./y.js"` where
    # y.js has no `x` — the browser refuses the whole module graph and the page is blank.
    for path in sorted((STATIC / "js").glob("*.js")):
        for names, target in _IMPORT.findall(path.read_text()):
            dep = STATIC / "js" / target
            assert dep.is_file(), f"{path.name} imports missing {target}"
            src = dep.read_text()
            for name in (n.strip().split(" as ")[0] for n in names.split(",") if n.strip()):
                assert re.search(_EXPORT.format(name=re.escape(name)), src), (
                    f"{path.name} imports {name!r}, which {target} does not export"
                )


def test_theme_defines_the_aurora_tokens(client):
    css = client.get("/static/app.css", headers=NO_AUTH).text.replace(" ", "")
    for token in (
        "--bg:#0b0d12",
        "--surf:#141821",
        "--raise:#1b202b",
        "--fg:#eef1f6",
        "--fg2:#a7afbf",
        "--fg3:#8590a2",
        "--a1:#8b7cff",
        "--a1-fill:#6d5dfc",
        "--a2:#2dd4bf",
        "--bad:#fb7185",
        "--warn:#fbbf24",
        "--accent:var(--a1)",
    ):
        assert token in css, token
    assert "@font-face" in css and "/static/fonts/inter-latin-wght-normal.woff2" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


def test_font_licence_and_icon_are_served(client):
    font = client.get("/static/fonts/inter-latin-wght-normal.woff2", headers=NO_AUTH)
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
    assert font.content[:4] == b"wOF2" and len(font.content) > 10_000
    lic = client.get("/static/fonts/OFL.txt", headers=NO_AUTH)
    assert lic.status_code == 200 and "OPEN FONT LICENSE" in lic.text.upper()
    icon = client.get("/static/icon.svg", headers=NO_AUTH)
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg+xml")
    assert "M4 13v6M10 7v18M16 3.5v25M22 9v14M28 13.5v5" in icon.text


def test_shell_is_a_sidebar_layout(client):
    body = client.get("/").text
    for marker in (
        'id="app" class="shell"',
        'id="side"',
        'id="tab-more"',
        'id="side-more"',
        'id="status-label"',
        'id="who-avatar"',
        'id="auris-grad"',
        'rel="icon" href="/static/icon.svg"',
        'class="auth-mark"',
    ):
        assert marker in body, marker
    # Main nav: Voices, Synthesize, Transcribe. Manage (inside the More sheet on a phone):
    # Settings, Account, Users.
    order = [
        body.index(f'id="{i}"')
        for i in (
            "tab-voices",
            "tab-synth",
            "tab-transcribe",
            "tab-more",
            "side-more",
            "tab-settings",
            "tab-account",
            "tab-users",
            "signout-btn",
        )
    ]
    assert order == sorted(order)
    assert "Models &amp; Settings" not in body


def test_shell_loads_nothing_from_another_origin(client):
    body = client.get("/").text
    assert not re.search(r'(?:src|href)\s*=\s*"(?:https?:)?//', body)


def test_app_module_wires_the_shell(client):
    src = client.get("/static/js/app.js", headers=NO_AUTH).text
    for marker in (
        "more-open",
        "data-jump",
        "details.menu",
        "status-label",
        "who-avatar",
        "avatarEl",
        "auris:network",
        "Offline",
    ):
        assert marker in src, marker
    assert "auris:network" in client.get("/static/js/api.js", headers=NO_AUTH).text


def test_voices_panel_is_a_card_library(client):
    body = client.get("/").text
    for marker in (
        'id="voice-lib"',
        'id="voices-list"',
        'id="presets-section"',
        'id="presets-list"',
        'id="voice-search"',
        'id="new-voice-btn"',
        'id="voices-msg"',
        'id="sample-audio"',
        'id="voice-card"',
        'id="vc-empty"',
        'id="vc-head"',
        'id="vc-title"',
        'id="vc-menu"',
        'id="vc-rename"',
        'id="vc-delete"',
        'id="voice-close"',
        'id="voice-backdrop"',
        'id="vc-player"',
        'id="vc-audio"',
        'id="voice-card-body"',
        'id="vc-msg"',
        '<dialog id="new-voice"',
        'id="nv-source"',
        'id="nv-record"',
        'id="nv-upload"',
        'id="record-btn"',
        'id="rec-wave"',
        'id="rec-progress"',
        'id="rec-time"',
        'id="clone-drop"',
        'id="nv-file"',
        'id="nv-submit"',
        'id="clone-msg"',
        '<dialog id="dialog"',
        'id="dialog-ok"',
        'id="dialog-cancel"',
        'id="dialog-input"',
    ):
        assert marker in body, marker
    # The clone form lives inside the New voice dialog, which sits outside the app shell.
    assert body.index('<dialog id="new-voice"') < body.index('id="clone-form"')
    assert body.index('id="clone-form"') < body.index("</dialog>")
    assert (
        body.index('id="content"')
        < body.index('id="voice-lib"')
        < body.index('<dialog id="new-voice"')
    )


def test_voices_module_uses_in_app_dialogs_and_guards_stale_loads(client):
    src = _code(client.get("/static/js/voices.js", headers=NO_AUTH).text)
    assert not re.search(r"\b(?:prompt|confirm)\(", src)
    for marker in (
        "confirmDialog",
        "promptDialog",
        "createPlayer",
        "attachDropzone",
        "bindSegmented",
        "levelMeter",
        "selected !== name",
        "textContent",
    ):
        assert marker in src, marker
    # names reach the DOM as text, never as markup (Review Focus #1)
    assert "innerHTML" not in src


def test_dialog_module_resolves_cancel_as_no(client):
    src = client.get("/static/js/dialog.js", headers=NO_AUTH).text
    for marker in ('"cancel"', "showModal", "settle(null)", "dialog-ok", "dialog-input"):
        assert marker in src, marker


def test_synth_panel_has_the_new_controls(client):
    body = client.get("/").text
    for marker in (
        'id="char-count"',
        'id="speak-live"',
        'id="speak-live-time"',
        'id="synth-player"',
        'id="synth-empty"',
        'id="mode-seg"',
        'id="format-seg"',
        'id="voice-avatar"',
        'id="speed-out"',
        'id="speed" type="range"',
        'id="mode-select"',
        'id="format-select"',
    ):
        assert marker in body, marker


def test_synth_module_wires_the_new_controls(client):
    src = client.get("/static/js/synth.js", headers=NO_AUTH).text
    for marker in (
        "max_input_chars",
        "ctrlKey",
        "metaKey",
        "encodeWav",
        "onChunk",
        "mirrorSelect",
        "createPlayer",
        "Stream finished",
        "Stopped",
    ):
        assert marker in src, marker


def test_transcribe_panel_has_a_drop_zone_and_transcript_card(client):
    body = client.get("/").text
    for marker in (
        'id="stt-drop"',
        'id="stt-file"',
        'id="stt-filerow"',
        'id="stt-filename"',
        'id="stt-filemeta"',
        'id="stt-run-btn"',
        'id="stt-copy"',
        '<ol id="stt-segments"',
        'id="stt-text"',
        'id="stt-dl-txt"',
        'id="stt-dl-srt"',
        'id="stt-dl-vtt"',
        'id="stt-translate" type="checkbox" class="switch"',
        'id="stt-words" type="checkbox" class="switch"',
    ):
        assert marker in body, marker
    # the file input sits inside its drop zone, visually hidden — not removed
    assert (
        body.index('id="stt-drop"') < body.index('id="stt-file"') < body.index('id="stt-filerow"')
    )


def test_transcribe_module_uses_the_drop_zone_and_clipboard(client):
    src = client.get("/static/js/transcribe.js", headers=NO_AUTH).text
    for marker in ("attachDropzone", "copyWithFeedback", "durationOf", "stt-filerow"):
        assert marker in src, marker
    clip = client.get("/static/js/clipboard.js", headers=NO_AUTH).text
    assert "isSecureContext" in clip and "selectNodeContents" in clip


def test_settings_panel_is_single_column_with_jump_links(client):
    body = client.get("/").text
    for marker in (
        'id="panel-settings" class="panel narrow"',
        'data-jump="settings-server"',
        'data-jump="settings-prefs"',
        'data-jump="admin-card"',
        'id="settings-server"',
        'id="settings-prefs"',
        'id="key-owner"',
        'id="jump-keys"',
    ):
        assert marker in body, marker


def test_settings_module_renders_tiles_and_issues_owned_keys(client):
    src = _code(client.get("/static/js/settings.js", headers=NO_AUTH).text)
    for marker in (
        "tile(",
        "owner_email",
        "confirmDialog",
        "copyWithFeedback",
        "auris:users",
        "relTime",
    ):
        assert marker in src, marker
    assert "kv(" not in src
    assert not re.search(r"\b(?:prompt|confirm)\(", src)


def test_account_panel_has_a_profile_header_and_jump_links(client):
    body = client.get("/").text
    for marker in (
        'id="panel-account" class="panel narrow"',
        'id="account-avatar"',
        'id="account-email"',
        'id="account-role"',
        'data-jump="account-pw-card"',
        'data-jump="account-keys-card"',
        'id="jump-pw"',
        'id="account-keys-card"',
        'id="account-copy"',
        'id="account-keys-msg"',
    ):
        assert marker in body, marker


def test_account_module_renders_the_new_key_list(client):
    src = client.get("/static/js/account.js", headers=NO_AUTH).text
    for marker in (
        "avatarEl",
        "copyWithFeedback",
        "relTime",
        "account-keys-msg",
        "Rotate",
        "Revoke",
    ):
        assert marker in src, marker


def test_users_panel_has_a_create_card_and_accounts_table(client):
    body = client.get("/").text
    for marker in ('id="panel-users" class="panel narrow"', 'id="users-copy"', 'id="users-msg"'):
        assert marker in body, marker
    assert "must change password</th>" not in body  # the status column merges that flag


def test_users_module_uses_menus_dialogs_and_merged_status(client):
    src = client.get("/static/js/users.js", headers=NO_AUTH).text
    for marker in (
        "details",
        "menu-list",
        "Must set password",
        "userStatus",
        "confirmDialog",
        "auris:users",
        "data-disable",
        "data-enable",
        "data-reset",
    ):
        assert marker in src, marker


def test_no_module_uses_blocking_browser_dialogs():
    # window.prompt/confirm freeze the page (and browser automation); dialog.js replaces them.
    for path in sorted((STATIC / "js").glob("*.js")):
        src = _code(path.read_text())
        assert not re.search(r"\b(?:prompt|confirm|alert)\(", src), path.name


def test_legacy_styles_are_gone(client):
    css = client.get("/static/app.css", headers=NO_AUTH).text
    assert "legacy" not in css
    for selector in (".kv", ".issued", ".two-col", "ul#voices-list", ".row {"):
        assert selector not in css, selector


def test_stop_stays_live_until_a_stream_finishes_playing(client):
    # The server can finish sending long before playback ends (Kokoro streams a paragraph in
    # half a second), so speak()'s `finally` — which disables Stop — must wait for playback
    # (`stream.ended`), not just for the response to be read (`stream.done`).
    src = client.get("/static/js/synth.js", headers=NO_AUTH).text
    body = src[src.index("async function speakStream") : src.index("function stopAll")]
    assert "await stream.ended" in body
    assert body.index("await stream.done") < body.index("await stream.ended")


def test_stop_aborts_the_request_and_stale_runs_leave_the_page_alone(client):
    # Stop must cancel a synthesis still in flight (full mode, or a stream before its headers),
    # and a run superseded by Stop + Speak must not autoplay or unlock the newer one.
    src = client.get("/static/js/synth.js", headers=NO_AUTH).text
    assert "new AbortController()" in src and "signal" in src
    stop_all = src[src.index("function stopAll") : src.index("async function speak()")]
    assert ".abort()" in stop_all
    full = src[src.index("async function speakFull") : src.index("async function speakStream")]
    assert "run !== runs" in full
    stream = src[src.index("async function speakStream") : src.index("function stopAll")]
    assert "stopped = false" not in stream  # reset in speak(), before the request, not after it
    speak = src[src.index("async function speak()") : src.index("function updateCount")]
    assert "finally" in speak and "run === runs" in speak[speak.index("finally") :]


def test_account_key_revoke_and_rotate_ask_first(client):
    src = client.get("/static/js/account.js", headers=NO_AUTH).text
    assert 'import { confirmDialog } from "./dialog.js"' in src
    for fn in ("async function rotateKey", "async function revokeKey"):
        body = src[src.index(fn) :]
        body = body[: body.index("\n}\n")]
        assert "await confirmDialog(" in body, fn


def test_voices_page_has_sections_filters_and_an_admin_only_others_toggle(client):
    markup = client.get("/static/index.html", headers=NO_AUTH).text
    for value in ("all", "mine", "shared", "presets"):
        assert f'data-value="{value}"' in markup[markup.index('id="voice-filter"') :], value
    # Admin-only, and off unless chosen: the row starts hidden and the box unchecked.
    row = markup[markup.index('id="show-others-row"') :].split(">", 1)[0]
    assert "hidden" in row
    box = markup[markup.index('id="show-others"') :].split(">", 1)[0]
    assert "checked" not in box
    src = _code(client.get("/static/js/voices.js", headers=NO_AUTH).text)
    assert "groupVoices(" in src and "showOthers" in src
    assert '$("show-others-row").hidden = !state.admin' in src
    # The pickers honour the same toggle, so a hidden voice can't be picked elsewhere.
    for page in ("synth.js", "settings.js"):
        assert "usableVoices(" in _code(client.get(f"/static/js/{page}", headers=NO_AUTH).text), (
            page
        )
