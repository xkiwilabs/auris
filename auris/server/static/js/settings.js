// auris/server/static/js/settings.js — server info (read-only), my preferences, admin keys.
//
// Runs first of all the panels: it fills `state.info` and `state.models`, which the other
// panels read (synth.js gates on `state.info.pipelines.tts`).
//
// The Admin block is rendered only when `state.admin` is true — that flag comes from the
// server (`/auth/me`'s `admin`), never from a guess in the page; the routes are admin-only
// server-side regardless. A freshly issued key is put in the DOM once and read back from there
// by the Copy button: it is never written to localStorage, sessionStorage, a cookie or anywhere
// else that outlives the page.
"use strict";
import { api, getJson, postJson, escapeHtml, setMsg } from "./api.js";
import { loadPrefs, setPref } from "./prefs.js";
import { fmtDate, relTime, usableVoices } from "./view-logic.js";
import { confirmDialog } from "./dialog.js";
import { copyWithFeedback } from "./clipboard.js";
import { $ } from "./app.js";

function tile(title, on, main, lines) {
  return '<div class="tile"><div class="tile-head"><span class="dot' + (on ? " ok" : "") + '"></span>' +
    escapeHtml(title) + '</div><div class="tile-main">' + escapeHtml(main) + "</div>" +
    '<ul class="tile-list">' + lines.map((l) => "<li>" + escapeHtml(l) + "</li>").join("") + "</ul></div>";
}

function renderInfo(info) {
  const pipelines = info.pipelines || {};
  const limits = info.limits || {};
  const a = info.asr || {};
  const tts = Object.entries(info.tts || {});
  const asrDevice = a.device + (a.device === "cuda" ? ":" + a.device_index : "");
  const allowed = a.allowed || [];
  const n = (v) => (typeof v === "number" ? v.toLocaleString() : "?");
  $("info-body").className = "";
  $("info-body").innerHTML =
    '<div class="tiles">' +
    tile("Text to speech", pipelines.tts, pipelines.tts ? "On" : "Off",
      tts.length
        ? tts.map(([m, p]) => m + " × " + p.workers + " on " + ((p.devices || []).join(", ") || "?"))
        : ["Switched off on this server"]) +
    tile("Speech to text", pipelines.stt, pipelines.stt ? a.model || "On" : "Off",
      pipelines.stt
        ? [a.workers + " worker" + (a.workers === 1 ? "" : "s") + " on " + asrDevice, "compute " + a.compute_type]
        : ["Switched off on this server"]) +
    tile("Limits", true, n(limits.max_input_chars) + " chars", [
      "per text request",
      "STT upload ≤ " + n(limits.asr_max_upload_mb) + " MB",
      "clone upload ≤ " + n(limits.max_upload_seconds) + " s",
    ]) +
    "</div>" +
    (allowed.length
      ? '<div class="chips-row"><span class="label">Allowed STT models</span>' +
        allowed.map((m) => m === a.model
          ? '<span class="chip default" title="Server default">' + escapeHtml(m) + " · default</span>"
          : '<span class="chip">' + escapeHtml(m) + "</span>").join("") + "</div>"
      : "") +
    '<p class="small version">auris ' + escapeHtml(info.version || "") + "</p>";
}

function fillSelect(sel, options, value, labelOf = (o) => o) {
  sel.innerHTML = "";
  for (const o of options) {
    const el = document.createElement("option");
    el.value = o;
    el.textContent = labelOf(o);  // textContent, so no escaping question arises
    sel.appendChild(el);
  }
  if ([...sel.options].some((o) => o.value === value)) sel.value = value;
}

function renderPrefs(state) {
  const p = loadPrefs();
  const models = state.models ? state.models.models : [];
  fillSelect($("pref-asr-model"), [""].concat(models.map((m) => m.id)), p.asrModel, (id) =>
    id
      ? id + "  (" + ((models.find((m) => m.id === id) || {}).params || "?") + ")"
      : "server default" + (state.models ? " (" + state.models.default + ")" : ""));
  $("pref-language").value = p.language;
  const voices = usableVoices(state.voices, state.me, state.admin && p.showOthers);
  fillSelect($("pref-voice"), [""].concat(voices.map((v) => v.name)), p.voice,
    (n) => n || "(none)");
  $("pref-speed").value = p.speed;
  $("pref-format").value = p.format;
  $("pref-mode").value = p.mode;
}

function bindPrefs() {
  const bind = (id, key, parse = (v) => v) => {
    $(id).onchange = () => setPref(key, parse($(id).value));
  };
  bind("pref-asr-model", "asrModel");
  bind("pref-language", "language", (v) => v.trim() || "auto");
  bind("pref-voice", "voice");
  // An emptied speed box would otherwise store NaN (JSON `null`) and break every reader.
  bind("pref-speed", "speed", (v) => parseFloat(v) || 1.0);
  bind("pref-format", "format");
  bind("pref-mode", "mode");
}

// "" means the caller themselves — POST /v1/keys owns the key by the caller when owner_email is
// absent. Other accounts come from the admin-only user list; a failure there still leaves "Me".
async function fillOwners(state) {
  const sel = $("key-owner");
  const me = state.me && state.me.email;
  sel.replaceChildren(new Option(me ? "Me (" + me + ")" : "Me", ""));
  try {
    for (const u of await getJson("/v1/users")) {
      if (!u.disabled && u.email !== me) sel.append(new Option(u.email, u.email));
    }
  } catch { /* issuing for yourself needs no list */ }
}

async function renderKeys() {
  const tbody = $("keys-table").querySelector("tbody");
  try {
    const keys = await getJson("/v1/keys");
    tbody.innerHTML = keys.map((k) =>
      "<tr><td>" + escapeHtml(k.name) + "</td><td>" + escapeHtml(k.owner || "—") + "</td><td>" +
      escapeHtml(fmtDate(k.created_at)) + "</td><td>" + escapeHtml(relTime(k.last_used_at)) +
      '</td><td class="actions">' +
      (k.revoked_at
        ? '<span class="pill">Revoked ' + escapeHtml(fmtDate(k.revoked_at)) + "</span>"
        : '<button type="button" class="btn small danger" data-revoke="' + escapeHtml(k.name) + '">Revoke</button>') +
      "</td></tr>").join("") ||
      '<tr><td colspan="5" class="empty">No keys issued yet.</td></tr>';
    tbody.querySelectorAll("button[data-revoke]").forEach((b) => {
      b.onclick = () => revoke(b.dataset.revoke);
    });
  } catch (e) { setMsg($("keys-msg"), e.message, "err"); }
}

async function issue(ev) {
  ev.preventDefault();
  const name = $("key-name").value.trim();
  if (!name) return;
  const owner = $("key-owner").value;
  try {
    const j = await (await postJson("/v1/keys", owner ? { name, owner_email: owner } : { name })).json();
    const box = $("key-issued");
    box.style.display = "";
    box.innerHTML = "<p>Key <b>" + escapeHtml(j.name) + "</b>" +
      (j.owner ? " for " + escapeHtml(j.owner) : "") + " — copy it now, you will not see this again:</p>" +
      '<div class="secret"><code id="key-plain">' + escapeHtml(j.key) + "</code>" +
      '<button type="button" class="btn small" id="key-copy">Copy</button></div>';
    $("key-copy").onclick = () => copyWithFeedback($("key-copy"), $("key-plain").textContent, $("key-plain"));
    $("key-form").reset();
    setMsg($("keys-msg"), "", "");
    await renderKeys();
  } catch (e) { setMsg($("keys-msg"), e.message, "err"); }
}

async function revoke(name) {
  const ok = await confirmDialog({
    title: "Revoke “" + name + "”?",
    body: "Anything using this key stops working immediately. This can't be undone.",
    confirmLabel: "Revoke key", danger: true,
  });
  if (!ok) return;
  try {
    await api("/v1/keys/" + encodeURIComponent(name), { method: "DELETE" });
    await renderKeys();
  } catch (e) { setMsg($("keys-msg"), e.message, "err"); }
}

export async function initSettings(state) {
  try { state.info = await getJson("/v1/info"); renderInfo(state.info); }
  catch (e) { $("info-body").className = "empty"; $("info-body").textContent = e.message; }
  // /v1/audio/models answers for any principal and reports `pipelines` itself, so this only
  // fails on a real error (network, 401); the Transcribe panel treats null as "unknown".
  try { state.models = await getJson("/v1/audio/models"); }
  catch { state.models = null; }
  renderPrefs(state);
  bindPrefs();
  document.addEventListener("auris:voices", () => renderPrefs(state));
  if (state.admin) {
    $("admin-card").style.display = "";
    $("jump-keys").hidden = false;
    $("key-form").addEventListener("submit", issue);
    await fillOwners(state);
    document.addEventListener("auris:users", () => fillOwners(state));
    await renderKeys();
  }
}
