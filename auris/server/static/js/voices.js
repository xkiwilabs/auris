// auris/server/static/js/voices.js — the voice library: cards, the detail panel, New voice.
//
// "Play" (on a card or in the detail panel) synthesises the fixed SAMPLE_TEXT in that voice and
// caches the blob URL per voice for the life of the page. The ▶ beside each clip plays the stored
// reference itself (GET …/clips/{index}/audio) — what the clone is copying — one at a time, and
// only for the voice's owner and admins (mayPlayClips). Every voice name reaches the DOM through
// textContent: a name may contain
// < > " ' (only / \ and control characters are refused), so it must never be parsed as markup.
//
// A blended voice (kind "blend") is a recipe mixing 2–3 built-in voices, with no clips: its
// detail panel shows the mix, and the Blend voices dialog creates or re-mixes one with a PUT to
// …/blend, then plays a sample without closing so the mix can be tuned by ear.
"use strict";
import { api, getJson, setMsg, fmtSeconds } from "./api.js";
import { recordClip, micAvailable, levelMeter } from "./audio.js";
import { mayChangeVisibility, mayPlayClips, mayEditBlend } from "./voice-visibility.js";
import { groupVoices, ownerLabel, mmss, fmtBytes, blendShares, blendSummary } from "./view-logic.js";
import { PASSAGES, nextPassage, countdownStep, COUNTDOWN_S } from "./passages.js";
import { loadPrefs, setPref } from "./prefs.js";
import { avatarEl } from "./avatar.js";
import { drawBars } from "./waveform.js";
import { createPlayer } from "./player.js";
import { confirmDialog, promptDialog } from "./dialog.js";
import { attachDropzone } from "./dropzone.js";
import { bindSegmented } from "./segmented.js";
import { $ } from "./app.js";

const SAMPLE_TEXT = "This is a test of this voice. How does it sound to you?";
// Seconds of speaking time, after the countdown. Longer than the 10 s the clone keeps on purpose:
// a recording stopped on a timer usually ends mid-word, and the server cuts back to a pause.
const REC_MAX_S = 30;
const REC_MIN_S = 3;  // the clone route refuses a clip shorter than this
const WAVE_BARS = 64;
const samples = new Map(); // voice name -> object URL of a cached sample

let selected = null;    // the voice in the detail panel
let cardPlaying = null; // the voice whose sample #sample-audio holds
let query = "";
let filter = "all"; // the chips above the library: all | mine | shared | presets
let recorder = null;
let hasMic = false;
let detailPlayer = null;
let source = null;      // the Record / Upload switch in the New voice dialog
let passage = 0;        // which PASSAGES entry the New voice dialog shows
let clipPlaying = null; // {key, url} of the reference clip #clip-audio holds
let clipSeq = 0; // bumped per clip request, so a slow fetch that lost the race is dropped
let blendPlayer = null;
let blendRows = [];     // the Blend voices dialog's rows: [{voice, weight}]
let blendSeq = 0;       // bumped per save, so a slow sample can't play over a newer mix
let blendOwner = null;  // ?owner= for the PUT when re-mixing another account's blend (admin)

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
    : v.kind === "blend"
      ? ownerLabel(v, state.me) + " · " + blendSummary(v)
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

// An admin is sent every voice on the server. Other people's private ones stay out of the way
// unless they turn on "Show others' private voices" (a per-browser preference, off by default).
const showOthers = (state) => state.admin && loadPrefs().showOthers;

const GROUPS = [
  ["mine", "My voices"],
  ["shared", "Shared with me"],
  ["others", "Others' private voices"],
];

function grid(cards) {
  const ul = document.createElement("ul");
  ul.className = "card-grid";
  ul.append(...cards);
  return ul;
}

function renderList(state) {
  const groups = groupVoices(state.voices, state.me, { query, filter });
  if (!showOthers(state)) groups.others = [];
  const sections = [];
  for (const [key, title] of GROUPS) {
    if (!groups[key].length) continue;
    const sec = document.createElement("section");
    sec.className = "voice-group";
    sec.id = "group-" + key;
    const h = textEl("h2", "section-title", title);
    h.append(textEl("span", "count", String(groups[key].length)));
    sec.append(h, grid(groups[key].map((v) => voiceCard(state, v))));
    sections.push(sec);
  }
  const list = $("voices-list");
  list.replaceChildren(...sections);
  if (!sections.length && filter !== "presets") {
    let card;
    if (query.trim()) card = emptyCard("No voices match “" + query.trim() + "”.", false);
    else if (filter === "shared") card = emptyCard("Nothing has been shared with you yet.", false);
    else card = emptyCard("No voices yet — clone one from a short recording.", true);
    list.append(grid([card]));
  }
  $("presets-list").replaceChildren(...groups.presets.map((v) => voiceCard(state, v)));
  $("presets-section").hidden = !groups.presets.length;
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
  showWarnings([]);
  stopClip();
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
  if (v.kind === "blend") {
    body.replaceChildren(mixSection(state, v));
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
  body.replaceChildren(clipsSection(state, v, clips));
}

function clipsSection(state, v, clips) {
  const name = v.name, playable = mayPlayClips(v, state.me, state.admin);
  const frag = document.createDocumentFragment();
  const ol = document.createElement("ol");
  ol.className = "clips";
  for (const c of clips) {
    const li = document.createElement("li");
    const main = document.createElement("div");
    main.append(textEl("span", "clip-len", fmtSeconds(c.duration_s)),
      textEl("p", "clip-text", c.transcript || "—"));
    const play = document.createElement("button");
    play.type = "button";
    play.className = "play-mini clip-play";
    play.dataset.clip = c.index;
    play.dataset.key = clipKey(name, c.index);
    play.setAttribute("aria-label", "Play clip " + c.index);
    play.addEventListener("click", () => playClip(name, c.index, play));
    const del = textEl("button", "icon-btn danger", "✕");
    del.type = "button";
    del.dataset.clip = c.index;
    del.setAttribute("aria-label", "Delete clip " + c.index);
    del.addEventListener("click", () => deleteClip(state, name, c.index));
    li.append(textEl("span", "clip-idx", String(c.index)), main);
    if (playable) li.append(play);
    li.append(del);
    ol.append(li);
  }
  const add = textEl("button", "btn block", "+ Add a clip");
  add.type = "button";
  add.id = "vc-add-clip";
  add.addEventListener("click", () => openNewVoice(name));
  frag.append(textEl("h3", "section-title", clips.length + (clips.length === 1 ? " clip" : " clips")), ol, add);
  return frag;
}

function mixSection(state, v) {
  const frag = document.createDocumentFragment();
  const ul = document.createElement("ul");
  ul.className = "mix";
  const shares = blendShares((v.blend || []).map((c) => c.weight));
  (v.blend || []).forEach((c, i) => {
    const li = document.createElement("li");
    const bar = document.createElement("span");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.style.width = shares[i] + "%";
    bar.append(fill);
    li.append(textEl("span", "", c.voice), textEl("span", "pct", shares[i] + "%"), bar);
    ul.append(li);
  });
  frag.append(textEl("h3", "section-title", "Blended from"), ul);
  if (mayEditBlend(v, state.me, state.admin)) {
    const edit = textEl("button", "btn block", "Edit mix");
    edit.type = "button";
    edit.id = "vc-edit-mix";
    edit.addEventListener("click", () => openBlend(state, v));
    frag.append(edit);
  }
  return frag;
}

// ---------------------------------------------------------------- reference clip playback

const clipKey = (name, index) => name + "\u0000" + index; // \0 can't appear in a voice name

// Only one clip's blob is held at a time: switching clips (or voices) releases the last one.
function stopClip() {
  const a = $("clip-audio");
  a.pause();
  a.removeAttribute("src");
  a.load();
  if (clipPlaying) URL.revokeObjectURL(clipPlaying.url);
  clipPlaying = null;
  markClips();
}

async function playClip(name, index, btn) {
  const a = $("clip-audio"), key = clipKey(name, index);
  if (clipPlaying && clipPlaying.key === key) {
    if (a.paused) a.play().catch(() => {}); else a.pause();
    return;
  }
  stopClip();
  const seq = ++clipSeq;
  btn.classList.add("busy");
  try {
    const res = await api("/v1/voices/" + encodeURIComponent(name) + "/clips/" + index + "/audio");
    const blob = await res.blob();
    // Another clip was asked for (or the voice switched) while this one downloaded.
    if (seq !== clipSeq || selected !== name) return;
    const url = URL.createObjectURL(blob);
    clipPlaying = { key, url };
    detailPlayer.pause();
    $("sample-audio").pause();
    a.src = url;
    await a.play();
  } catch (e) {
    if (e.name !== "AbortError") setMsg($("vc-msg"), e.message, "err");
  } finally { btn.classList.remove("busy"); }
}

function markClips() {
  const a = $("clip-audio");
  for (const b of document.querySelectorAll(".clip-play")) {
    const on = !!clipPlaying && b.dataset.key === clipPlaying.key && !a.paused;
    b.classList.toggle("playing", on);
    b.setAttribute("aria-label", (on ? "Pause" : "Play") + " clip " + b.dataset.clip);
  }
}

function showWarnings(list) {
  const ul = $("vc-warnings");
  ul.replaceChildren(...list.map((w) => textEl("li", "", w)));
  ul.hidden = !list.length;
}

function clearDetail(state) {
  stopClip();
  showWarnings([]);
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
    body: (state.voices.find((x) => x.name === name)?.kind === "blend"
      ? "This removes the blended voice." : "This removes the voice and all of its clips.") +
      " It can't be undone.",
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

function showPassage(index) {
  passage = index;
  $("nv-passage-title").textContent = "· " + PASSAGES[index].title;
  $("nv-passage-text").textContent = PASSAGES[index].text;
}

function resetRecordUi() {
  $("record-btn").classList.remove("active");
  $("record-btn").setAttribute("aria-label", "Start recording");
  $("rec-progress").style.width = "0%";
  $("rec-countdown").textContent = "";
  $("rec-time").textContent = "0:00 / " + mmss(REC_MAX_S);
  $("record-status").textContent =
    "Talk for 8–15 seconds in a few sentences, in your natural voice, then press stop. " +
    "The clone keeps up to 10 s and ends at a pause.";
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
  let meter = null, elapsed = 0, speaking = false;
  const levels = [];
  try {
    // The mic records through the countdown too, so the clip opens on room tone instead of
    // clipping the first syllable; the server trims that lead-in back to a fraction of a second.
    recorder = await recordClip({ maxSeconds: COUNTDOWN_S + REC_MAX_S, onTick: (t) => {
      elapsed = t;
      const step = countdownStep(t);
      $("rec-countdown").textContent = step.label || "";
      if (step.speaking && !speaking) {
        speaking = true;
        $("record-status").textContent =
          "Recording… press stop when you're done (it stops by itself at " + REC_MAX_S + " s).";
      }
      if (meter) {
        levels.push(meter.level());
        if (levels.length > WAVE_BARS) levels.shift();
        drawBars($("rec-wave"), new Array(WAVE_BARS - levels.length).fill(0.04).concat(levels), 1);
      }
      $("rec-progress").style.width = Math.min(100, (step.spoken / REC_MAX_S) * 100) + "%";
      $("rec-time").textContent = mmss(step.spoken) + " / " + mmss(REC_MAX_S);
    } });
  } catch (e) { return setMsg($("clone-msg"), e.message, "err"); }
  // The dialog closed while the browser asked for the mic: its close handler found no recorder.
  if (!$("new-voice").open) { recorder.stop(); await recorder.file; recorder = null; return; }
  meter = levelMeter(recorder.stream);
  btn.classList.add("active");
  btn.setAttribute("aria-label", "Stop recording");
  $("record-status").textContent = "Get ready — start speaking when the countdown ends.";
  const file = await recorder.file;
  recorder = null;
  meter.close();
  btn.classList.remove("active");
  btn.setAttribute("aria-label", "Record again");
  $("rec-countdown").textContent = "";
  const spoken = countdownStep(elapsed).spoken;
  if (spoken < REC_MIN_S) {
    $("record-status").textContent =
      "That was under " + REC_MIN_S + " seconds of speaking — the server needs at least " +
      REC_MIN_S + ". Record again.";
    return;
  }
  setFile(file, elapsed);
  $("record-status").textContent = "Recorded " + mmss(spoken) + " of speech. Add what you said if you like, then save.";
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
    showWarnings(j.warnings || []); // e.g. a short clip, or no pause to cut in
  } catch (e) { setMsg($("clone-msg"), e.message, "err"); }
  finally { $("nv-submit").disabled = false; }
}

// ---------------------------------------------------------------- Blend voices dialog

const presetNames = (state) => state.voices.filter((v) => v.kind === "preset").map((v) => v.name);

function renderBlendRows(state) {
  const names = presetNames(state);
  const rows = blendRows.map((row, i) => {
    const div = document.createElement("div");
    div.className = "blend-row";
    const sel = document.createElement("select");
    sel.className = "blend-voice";
    sel.setAttribute("aria-label", "Voice " + (i + 1));
    for (const n of names) {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      sel.append(o);
    }
    sel.value = row.voice;
    sel.addEventListener("change", () => { row.voice = sel.value; });
    const range = document.createElement("input");
    range.type = "range"; range.min = "1"; range.max = "100"; range.step = "1";
    range.className = "blend-weight";
    range.value = String(row.weight);
    range.setAttribute("aria-label", "Share of voice " + (i + 1));
    range.addEventListener("input", () => { row.weight = Number(range.value); showShares(); });
    const out = document.createElement("output");
    out.className = "blend-share";
    const del = textEl("button", "icon-btn danger", "✕");
    del.type = "button";
    del.setAttribute("aria-label", "Remove voice " + (i + 1));
    del.hidden = blendRows.length <= 2;
    del.addEventListener("click", () => { blendRows.splice(i, 1); renderBlendRows(state); });
    div.append(sel, range, out, del);
    return div;
  });
  $("blend-rows").replaceChildren(...rows);
  $("blend-add").hidden = blendRows.length >= 3 || names.length <= blendRows.length;
  showShares();
}

function showShares() {
  const shares = blendShares(blendRows.map((r) => r.weight));
  document.querySelectorAll("#blend-rows .blend-share").forEach((o, i) => { o.textContent = shares[i] + "%"; });
}

// `v`, when given, is an existing blend to re-mix: its name is fixed and its recipe preloaded.
function openBlend(state, v = null) {
  const names = presetNames(state);
  $("blend-form").reset();
  $("bv-submit").disabled = false; // a save cut short by closing the dialog never re-enabled it
  setMsg($("blend-msg"), "", "");
  blendPlayer.clear();
  $("bv-player").hidden = true;
  $("blend-name").readOnly = !!v;
  // An admin editing someone else's blend names its owner, as PATCH does, so the PUT can't land
  // on the admin's own voice of the same name instead.
  blendOwner = v && v.owner && !(state.me && v.owner === state.me.email) ? v.owner : null;
  $("blend-name").value = v ? v.name : "";
  $("bv-title").textContent = v ? "Edit mix of " + v.name : "Blend voices";
  blendRows = v
    ? v.blend.map((c) => ({ voice: c.voice, weight: Math.max(1, Math.round(c.weight * 100)) }))
    : names.slice(0, 2).map((n) => ({ voice: n, weight: 50 }));
  // An unowned voice is always visible to everyone (see mayChangeVisibility), so the switch
  // would change nothing there; it is hidden rather than offered.
  $("blend-share-row").hidden = !!v && v.owner == null;
  $("blend-share").checked = !!v && v.visibility === "shared";
  renderBlendRows(state);
  $("blend-voice").showModal();
  (v ? $("bv-submit") : $("blend-name")).focus();
}

async function saveBlend(state, ev) {
  ev.preventDefault();
  const name = $("blend-name").value.trim();
  if (!name) return setMsg($("blend-msg"), "Enter a name for the blend.", "err");
  const voices = blendRows.map((r) => r.voice);
  if (new Set(voices).size !== voices.length) {
    return setMsg($("blend-msg"), "Pick a different built-in voice for each row.", "err");
  }
  // The PUT re-mixes whatever blend already has this name, so a new one must not quietly
  // replace one of the caller's existing voices (a cloned one the server refuses anyway).
  if (!$("blend-name").readOnly) {
    const key = name.toLowerCase();
    const clash = state.voices.find((x) => x.kind !== "preset" && x.name.toLowerCase() === key &&
      state.me && x.owner === state.me.email);
    if (clash && clash.kind === "blend") {
      const ok = await confirmDialog({
        title: "Replace the mix of “" + clash.name + "”?",
        body: "You already have a blend with this name. Saving replaces its mix.",
        confirmLabel: "Replace mix",
      });
      if (!ok) return;
    } else if (clash) {
      return setMsg($("blend-msg"), "You already have a cloned voice called “" + clash.name +
        "”. Choose another name.", "err");
    }
  }
  const body = { components: blendRows.map((r) => ({ voice: r.voice, weight: r.weight })) };
  if (!$("blend-share-row").hidden) body.visibility = $("blend-share").checked ? "shared" : "private";
  const seq = ++blendSeq;
  $("bv-submit").disabled = true;
  setMsg($("blend-msg"), "Saving and synthesizing a sample…", "info");
  try {
    const owner = blendOwner ? "?owner=" + encodeURIComponent(blendOwner) : "";
    const res = await api("/v1/voices/" + encodeURIComponent(name) + "/blend" + owner, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const info = await res.json();
    if (seq !== blendSeq) { // closed (and maybe reopened on another voice) while this saved
      dropSample(info.name);
      await refreshVoices(state);
      return;
    }
    // Saved: further saves re-mix this voice rather than starting another one.
    $("blend-name").readOnly = true;
    $("blend-name").value = info.name;
    $("bv-title").textContent = "Edit mix of " + info.name;
    dropSample(info.name);
    await refreshVoices(state);
    if (selected === info.name) await select(state, info.name);
    const url = await sampleUrl(info.name);
    if (seq !== blendSeq || !$("blend-voice").open) return;
    $("bv-player").hidden = false;
    $("sample-audio").pause();
    detailPlayer.pause();
    await blendPlayer.load(url, { autoplay: true });
    setMsg($("blend-msg"), (res.status === 201 ? "Created " : "Saved ") + info.name +
      " — playing a sample. Adjust and save again, or close when it sounds right.", "ok");
  } catch (e) { if (seq === blendSeq) setMsg($("blend-msg"), e.message, "err"); }
  finally { if (seq === blendSeq) $("bv-submit").disabled = false; }
}

function initBlend(state) {
  blendPlayer = createPlayer($("bv-player"), $("bv-audio"));
  $("blend-voice-btn").addEventListener("click", () => openBlend(state));
  $("blend-add").addEventListener("click", () => {
    const used = new Set(blendRows.map((r) => r.voice));
    const next = presetNames(state).find((n) => !used.has(n));
    if (!next || blendRows.length >= 3) return;
    blendRows.push({ voice: next, weight: 30 });
    renderBlendRows(state);
  });
  $("blend-form").addEventListener("submit", (ev) => saveBlend(state, ev));
  $("bv-cancel").addEventListener("click", () => $("blend-voice").close());
  $("bv-close").addEventListener("click", () => $("blend-voice").close());
  $("blend-voice").addEventListener("close", () => { blendSeq++; blendPlayer.pause(); });
  $("bv-audio").addEventListener("play", () => { $("sample-audio").pause(); $("vc-audio").pause(); });
}

// ---------------------------------------------------------------- init

export async function initVoices(state) {
  hasMic = window.isSecureContext && micAvailable();
  detailPlayer = createPlayer($("vc-player"), $("vc-audio"), {
    onEmptyPlay: () => { if (selected) loadDetailSample(selected); },
  });
  $("vc-audio").addEventListener("play", () => { $("sample-audio").pause(); $("clip-audio").pause(); });
  $("sample-audio").addEventListener("play", () => $("clip-audio").pause());
  for (const e of ["play", "pause", "ended", "emptied"]) $("clip-audio").addEventListener(e, markClips);
  for (const e of ["play", "pause", "ended", "emptied"]) $("sample-audio").addEventListener(e, markPlaying);
  $("new-voice-btn").addEventListener("click", () => openNewVoice());
  initBlend(state);
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
  showPassage(passage);
  $("nv-passage-next").addEventListener("click", () => showPassage(nextPassage(passage)));

  bindSegmented($("voice-filter"), (value) => { filter = value; renderList(state); });
  $("show-others-row").hidden = !state.admin;
  $("show-others").checked = !!showOthers(state);
  $("show-others").addEventListener("change", (ev) => {
    setPref("showOthers", ev.target.checked);
    renderList(state);
    // The Speak and Settings pickers follow the same choice.
    document.dispatchEvent(new CustomEvent("auris:voices"));
  });

  try { await refreshVoices(state); }
  catch (e) { $("voices-list").replaceChildren(grid([emptyCard(e.message, false)])); }
}
