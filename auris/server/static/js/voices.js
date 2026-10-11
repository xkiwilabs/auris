// auris/server/static/js/voices.js — the voice library: cards, the detail panel, New voice.
//
// "Play" (on a card or in the detail panel) synthesises the fixed SAMPLE_TEXT in that voice —
// there is no route that serves a stored clip's audio — and caches the blob URL per voice for
// the life of the page. Every voice name reaches the DOM through textContent: a name may contain
// < > " ' (only / \ and control characters are refused), so it must never be parsed as markup.
"use strict";
import { api, getJson, setMsg, fmtSeconds } from "./api.js";
import { recordClip, micAvailable, levelMeter } from "./audio.js";
import { mayChangeVisibility } from "./voice-visibility.js";
import { splitVoices, ownerLabel, mmss, fmtBytes } from "./view-logic.js";
import { avatarEl } from "./avatar.js";
import { drawBars } from "./waveform.js";
import { createPlayer } from "./player.js";
import { confirmDialog, promptDialog } from "./dialog.js";
import { attachDropzone } from "./dropzone.js";
import { bindSegmented } from "./segmented.js";
import { $ } from "./app.js";

const SAMPLE_TEXT = "This is a test of this voice. How does it sound to you?";
const REC_MAX_S = 10; // cloning cap: f5-TTS references are capped at ~10 s
const REC_MIN_S = 3;  // the clone route refuses a clip shorter than this
const WAVE_BARS = 64;
const samples = new Map(); // voice name -> object URL of a cached sample

let selected = null;    // the voice in the detail panel
let cardPlaying = null; // the voice whose sample #sample-audio holds
let query = "";
let recorder = null;
let hasMic = false;
let detailPlayer = null;
let source = null;      // the Record / Upload switch in the New voice dialog

// Drop a cached sample and release its blob: an object URL pins the decoded audio until revoked.
function dropSample(name) {
  const url = samples.get(name);
  if (url) URL.revokeObjectURL(url);
  samples.delete(name);
}

async function sampleUrl(name) {
  if (!samples.has(name)) {
    const res = await api("/v1/audio/speech", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ input: SAMPLE_TEXT, voice: name, speed: 1.0, response_format: "mp3" }),
    });
    samples.set(name, URL.createObjectURL(await res.blob()));
  }
  return samples.get(name);
}

function textEl(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

export async function refreshVoices(state) {
  state.voices = await getJson("/v1/voices");
  renderList(state);
  document.dispatchEvent(new CustomEvent("auris:voices"));
}

// ---------------------------------------------------------------- cards

function voiceCard(state, v) {
  const li = document.createElement("li");
  li.className = "voice-card" + (v.name === selected ? " sel" : "");
  li.dataset.name = v.name;
  const main = document.createElement("button");
  main.type = "button";
  main.className = "vc-main";
  main.setAttribute("aria-pressed", String(v.name === selected));
  const meta = v.kind === "preset"
    ? v.description || "Built-in voice"
    : ownerLabel(v, state.me) + " · " + v.clips + " clip" + (v.clips === 1 ? "" : "s") +
      " · " + fmtSeconds(v.duration_s || 0);
  const text = document.createElement("span");
  text.className = "vc-text";
  text.append(textEl("span", "vc-name", v.name), textEl("span", "vc-meta", meta));
  main.append(avatarEl(v.name, 40), text);
  const kind = v.kind === "preset" ? "preset" : v.visibility === "shared" ? "shared" : "private";
  const play = document.createElement("button");
  play.type = "button";
  play.className = "play-mini";
  play.setAttribute("aria-label", "Play a sample of " + v.name);
  play.addEventListener("click", (ev) => { ev.stopPropagation(); playCard(v.name, play); });
  const foot = document.createElement("span");
  foot.className = "vc-foot";
  foot.append(textEl("span", "badge " + kind, kind), play);
  li.append(main, foot);
  li.addEventListener("click", () => select(state, v.name));
  return li;
}

function emptyCard(text, withButton) {
  const li = document.createElement("li");
  li.className = "empty-card";
  li.append(textEl("p", "", text));
  if (withButton) {
    const b = textEl("button", "btn primary", "+ New voice");
    b.type = "button";
    b.addEventListener("click", () => openNewVoice());
    li.append(b);
  }
  return li;
}

function renderList(state) {
  const { cloned, presets } = splitVoices(state.voices, query);
  const list = $("voices-list");
  list.replaceChildren(...cloned.map((v) => voiceCard(state, v)));
  if (!cloned.length) {
    const any = state.voices.some((v) => v.kind !== "preset");
    list.append(any
      ? emptyCard("No voices match “" + query.trim() + "”.", false)
      : emptyCard("No voices yet — clone one from a 10-second recording.", true));
  }
  $("presets-list").replaceChildren(...presets.map((v) => voiceCard(state, v)));
  $("presets-section").hidden = !presets.length;
  markPlaying();
}

async function playCard(name, btn) {
  const a = $("sample-audio");
  if (cardPlaying === name && !a.paused) { a.pause(); return; }
  detailPlayer.pause();
  btn.classList.add("busy");
  try {
    const url = await sampleUrl(name);
    cardPlaying = name;
    a.src = url;
    await a.play();
  } catch (e) {
    if (e.name !== "AbortError") setMsg($("voices-msg"), e.message, "err");
  } finally { btn.classList.remove("busy"); }
}

function markPlaying() {
  const a = $("sample-audio");
  for (const li of document.querySelectorAll(".voice-card")) {
    const on = li.dataset.name === cardPlaying && !a.paused;
    const b = li.querySelector(".play-mini");
    b.classList.toggle("playing", on);
    b.setAttribute("aria-label", (on ? "Pause" : "Play") + " a sample of " + li.dataset.name);
  }
}

// ---------------------------------------------------------------- detail panel

async function select(state, name) {
  selected = name;
  renderList(state);
  setMsg($("vc-msg"), "", "");
  const v = state.voices.find((x) => x.name === name);
  if (!v) { clearDetail(state); return; }
  $("voice-lib").classList.add("has-selection");
  $("vc-empty").hidden = true;
  $("vc-head").hidden = false;
  $("vc-player").hidden = false;
  $("vc-avatar").replaceChildren(avatarEl(v.name, 52));
  $("vc-title").textContent = v.name;
  $("vc-menu").hidden = v.kind === "preset";
  $("vc-menu").open = false;
  detailPlayer.clear();
  if (samples.has(name)) detailPlayer.load(samples.get(name));
  renderOwnership(state, v);
  const body = $("voice-card-body");
  if (v.kind === "preset") {
    body.replaceChildren(textEl("p", "small", v.description || "A built-in voice."));
    return;
  }
  body.replaceChildren(textEl("p", "small", "Loading clips…"));
  let clips;
  try { clips = await getJson("/v1/voices/" + encodeURIComponent(name) + "/clips"); }
  catch (e) {
    if (selected !== name) return;
    body.replaceChildren();
    setMsg($("vc-msg"), e.message, "err");
    renderOwnership(state, null);
    return;
  }
  if (selected !== name) return; // the user picked another voice while this loaded
  body.replaceChildren(clipsSection(state, name, clips));
}

function clipsSection(state, name, clips) {
  const frag = document.createDocumentFragment();
  const ol = document.createElement("ol");
  ol.className = "clips";
  for (const c of clips) {
    const li = document.createElement("li");
    const main = document.createElement("div");
    main.append(textEl("span", "clip-len", fmtSeconds(c.duration_s)),
      textEl("p", "clip-text", c.transcript || "—"));
    const del = textEl("button", "icon-btn danger", "✕");
    del.type = "button";
    del.dataset.clip = c.index;
    del.setAttribute("aria-label", "Delete clip " + c.index);
    del.addEventListener("click", () => deleteClip(state, name, c.index));
    li.append(textEl("span", "clip-idx", String(c.index)), main, del);
    ol.append(li);
  }
  const add = textEl("button", "btn block", "+ Add a clip");
  add.type = "button";
  add.id = "vc-add-clip";
  add.addEventListener("click", () => openNewVoice(name));
  frag.append(textEl("h3", "section-title", clips.length + (clips.length === 1 ? " clip" : " clips")), ol, add);
  return frag;
}

function clearDetail(state) {
  selected = null;
  $("voice-lib").classList.remove("has-selection");
  $("vc-empty").hidden = false;
  $("vc-head").hidden = true;
  $("vc-player").hidden = true;
  detailPlayer.clear();
  $("voice-card-body").replaceChildren();
  renderOwnership(state, null);
  renderList(state);
}

// `v` is null when the detail panel is showing an error instead of a voice (or nothing is
// selected): hide the row rather than leave it reflecting the previous selection.
function renderOwnership(state, v) {
  const row = $("voice-visibility-row"), box = $("voice-visibility"), owner = $("voice-owner");
  if (!v) { row.style.display = "none"; owner.textContent = ""; return; }
  if (v.kind === "preset") { row.style.display = "none"; owner.textContent = "Built-in voice"; return; }
  if (v.owner == null) {
    // visibility is inert on an unowned voice (every account can already see it) — see
    // mayChangeVisibility's comment — so there is nothing useful the toggle could do here,
    // not even for an admin.
    owner.textContent = "No owner — visible to every account; adopt this voice to make sharing settings apply.";
    row.style.display = "none";
    return;
  }
  owner.textContent = "Owned by " + v.owner;
  if (!mayChangeVisibility(v, state.me, state.admin)) { row.style.display = "none"; return; }
  row.style.display = "";
  box.checked = v.visibility === "shared";
  box.onchange = () => setVisibility(state, v.name, box);
}

async function setVisibility(state, name, box) {
  const visibility = box.checked ? "shared" : "private";
  try {
    await api("/v1/voices/" + encodeURIComponent(name), {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ visibility }),
    });
    await refreshVoices(state);
    const updated = state.voices.find((x) => x.name === name);
    if (updated) renderOwnership(state, updated);
  } catch (e) {
    box.checked = !box.checked; // the PATCH did not take effect — don't leave the box flipped
    setMsg($("vc-msg"), e.message, "err");
  }
}

async function loadDetailSample(name) {
  setMsg($("vc-msg"), "Synthesizing a sample…", "info");
  try {
    const url = await sampleUrl(name);
    if (selected !== name) return; // switched voices while it synthesised (Review Focus #2)
    $("sample-audio").pause();
    await detailPlayer.load(url, { autoplay: true });
    setMsg($("vc-msg"), "", "");
  } catch (e) { if (selected === name) setMsg($("vc-msg"), e.message, "err"); }
}

async function rename(state, name) {
  const next = await promptDialog({ title: "Rename voice", label: "New name", value: name, confirmLabel: "Rename" });
  if (!next || next === name) return;
  try {
    await api("/v1/voices/" + encodeURIComponent(name), {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: next }),
    });
    dropSample(name);
    await refreshVoices(state);
    await select(state, next);
  } catch (e) { setMsg($("vc-msg"), e.message, "err"); }
}

async function deleteClip(state, name, index) {
  const ok = await confirmDialog({
    title: "Delete clip " + index + "?",
    body: "This removes clip " + index + " of “" + name + "”. It can't be undone.",
    confirmLabel: "Delete clip", danger: true,
  });
  if (!ok) return;
  try {
    await api("/v1/voices/" + encodeURIComponent(name) + "/clips/" + index, { method: "DELETE" });
    dropSample(name);
    await refreshVoices(state);
    await select(state, name);
  } catch (e) { setMsg($("vc-msg"), e.message, "err"); } // e.g. "delete the voice instead"
}

async function deleteVoice(state, name) {
  const ok = await confirmDialog({
    title: "Delete “" + name + "”?",
    body: "This removes the voice and all of its clips. It can't be undone.",
    confirmLabel: "Delete voice", danger: true,
  });
  if (!ok) return;
  try {
    await api("/v1/voices/" + encodeURIComponent(name), { method: "DELETE" });
    dropSample(name);
    clearDetail(state);
    await refreshVoices(state);
  } catch (e) { setMsg($("vc-msg"), e.message, "err"); }
}

// ---------------------------------------------------------------- New voice dialog

function showSource(value) {
  source.set(value);
  $("nv-record").hidden = value !== "record";
  $("nv-upload").hidden = value !== "upload";
}

function showFile(file, seconds = null) {
  $("nv-file").hidden = !file;
  if (!file) return;
  $("nv-file-name").textContent = file.name;
  $("nv-file-meta").textContent = fmtBytes(file.size) + (seconds ? " · " + mmss(seconds) : "");
}

function setFile(file, seconds = null) {
  const dt = new DataTransfer();
  if (file) dt.items.add(file);
  $("clone-file").files = dt.files;
  showFile(file, seconds);
}

function resetRecordUi() {
  $("record-btn").classList.remove("active");
  $("record-btn").setAttribute("aria-label", "Start recording");
  $("rec-progress").style.width = "0%";
  $("rec-time").textContent = "0:00 / " + mmss(REC_MAX_S);
  $("record-status").textContent =
    "Read a sentence or two in your natural voice. Recording stops at " + REC_MAX_S + " seconds.";
  drawBars($("rec-wave"), new Array(WAVE_BARS).fill(0.04), 0);
}

function openNewVoice(name = null) {
  if (recorder) return;
  $("clone-form").reset();
  setFile(null);
  setMsg($("clone-msg"), "", "");
  $("clone-name").readOnly = !!name;
  if (name) $("clone-name").value = name;
  $("nv-title").textContent = name ? "Add a clip to " + name : "New voice";
  $("nv-submit").textContent = name ? "Add clip" : "Create voice";
  showSource(hasMic ? "record" : "upload");
  $("new-voice").showModal();
  resetRecordUi(); // after showModal: the canvas has no size while the dialog is closed
  (name ? (hasMic ? $("record-btn") : $("clone-drop")) : $("clone-name")).focus();
}

async function toggleRecord() {
  const btn = $("record-btn");
  if (recorder) { recorder.stop(); return; }
  setMsg($("clone-msg"), "", "");
  setFile(null);
  let meter = null, elapsed = 0;
  const levels = [];
  try {
    recorder = await recordClip({ maxSeconds: 10, onTick: (t, max) => {
      elapsed = t;
      if (meter) {
        levels.push(meter.level());
        if (levels.length > WAVE_BARS) levels.shift();
        drawBars($("rec-wave"), new Array(WAVE_BARS - levels.length).fill(0.04).concat(levels), 1);
      }
      $("rec-progress").style.width = Math.min(100, (t / max) * 100) + "%";
      $("rec-time").textContent = mmss(t) + " / " + mmss(max);
    } });
  } catch (e) { return setMsg($("clone-msg"), e.message, "err"); }
  meter = levelMeter(recorder.stream);
  btn.classList.add("active");
  btn.setAttribute("aria-label", "Stop recording");
  $("record-status").textContent = "Recording… press stop when you're done, or it stops at " + REC_MAX_S + " s.";
  const file = await recorder.file;
  recorder = null;
  meter.close();
  btn.classList.remove("active");
  btn.setAttribute("aria-label", "Record again");
  if (elapsed < REC_MIN_S) {
    $("record-status").textContent =
      "That was under " + REC_MIN_S + " seconds — the server needs at least " + REC_MIN_S + ". Record again.";
    return;
  }
  setFile(file, elapsed);
  $("record-status").textContent = "Recorded " + mmss(elapsed) + ". Add what you said if you like, then save.";
}

async function addVoice(state, ev) {
  ev.preventDefault();
  const name = $("clone-name").value.trim(), file = $("clone-file").files[0];
  if (recorder) return setMsg($("clone-msg"), "Stop the recording first.", "err");
  if (!name) return setMsg($("clone-msg"), "Enter a voice name.", "err");
  if (!file) return setMsg($("clone-msg"), "Record a clip or choose a file first.", "err");
  const fd = new FormData();
  fd.append("file", file, file.name || "clip.webm");
  const ref = $("clone-reftext").value.trim();
  if (ref) fd.append("ref_text", ref);
  $("nv-submit").disabled = true;
  setMsg($("clone-msg"), "Uploading and transcribing…", "info");
  try {
    const j = await (await api("/v1/voices/" + encodeURIComponent(name) + "/clone", { method: "POST", body: fd })).json();
    $("new-voice").close();
    dropSample(j.voice);
    await refreshVoices(state);
    await select(state, j.voice);
    setMsg($("vc-msg"), "Added clip " + j.clip_index + " — " + fmtSeconds(j.duration_s) +
      '. Transcript: "' + j.transcript + '"', "ok");
  } catch (e) { setMsg($("clone-msg"), e.message, "err"); }
  finally { $("nv-submit").disabled = false; }
}

// ---------------------------------------------------------------- init

export async function initVoices(state) {
  hasMic = window.isSecureContext && micAvailable();
  detailPlayer = createPlayer($("vc-player"), $("vc-audio"), {
    onEmptyPlay: () => { if (selected) loadDetailSample(selected); },
  });
  $("vc-audio").addEventListener("play", () => $("sample-audio").pause());
  for (const e of ["play", "pause", "ended", "emptied"]) $("sample-audio").addEventListener(e, markPlaying);
  $("new-voice-btn").addEventListener("click", () => openNewVoice());
  $("voice-search").addEventListener("input", (ev) => { query = ev.target.value; renderList(state); });
  $("vc-rename").addEventListener("click", () => { if (selected) rename(state, selected); });
  $("vc-delete").addEventListener("click", () => { if (selected) deleteVoice(state, selected); });
  $("voice-close").addEventListener("click", () => clearDetail(state));
  $("voice-backdrop").addEventListener("click", () => clearDetail(state));

  source = bindSegmented($("nv-source"), showSource);
  if (!hasMic) {
    $("nv-source").hidden = true;
    $("upload-hint").textContent = "Recording needs a secure page — open this dashboard with " +
      "`auris dashboard`, or upload a clip recorded in any app.";
  }
  attachDropzone($("clone-drop"), $("clone-file"), (f) => showFile(f),
    (f) => setMsg($("clone-msg"), "“" + f.name + "” isn't an audio file.", "err"));
  $("nv-file-clear").addEventListener("click", () => setFile(null));
  $("nv-cancel").addEventListener("click", () => $("new-voice").close());
  $("nv-close").addEventListener("click", () => $("new-voice").close());
  $("new-voice").addEventListener("close", () => { if (recorder) recorder.stop(); });
  $("clone-form").addEventListener("submit", (ev) => addVoice(state, ev));
  $("record-btn").addEventListener("click", toggleRecord);

  try { await refreshVoices(state); }
  catch (e) { $("voices-list").replaceChildren(emptyCard(e.message, false)); }
}
