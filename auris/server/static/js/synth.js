// auris/server/static/js/synth.js — the Synthesize panel.
//
// Full mode fetches a file and plays it in the waveform player. Stream mode plays PCM through an
// AudioContext as it arrives (no file exists yet, so a live "Speaking…" meter stands in), then
// wraps what it received in a WAV so the result can be replayed and saved like a full one.
"use strict";
import { api, setMsg } from "./api.js";
import { playPcmStream, encodeWav, concatF32 } from "./audio.js";
import { loadPrefs, setPref } from "./prefs.js";
import { charState, mmss, usableVoices } from "./view-logic.js";
import { avatarEl } from "./avatar.js";
import { createPlayer } from "./player.js";
import { mirrorSelect } from "./segmented.js";
import { $ } from "./app.js";

const SAMPLE_TEXT = "This is a test of this voice. How does it sound to you?";
let current = null;   // {stop()} for an in-flight stream
let aborter = null;   // AbortController for the current run's request; Stop aborts it
let lastUrl = null;   // object URL of the last output (player + save link)
let player = null;
let liveTimer = null;
let runs = 0;         // bumped per Speak (any mode), so an old run finishing late can't touch a newer one
let stopped = false;  // Stop was pressed during the current run
let busy = false;
let limit = 0;        // max_input_chars from /v1/info; 0 = unknown
let syncMode = () => {}, syncFormat = () => {};

function fmtTiming(sec, text) {
  const n = Math.max(1, text.trim().split(/\s+/).filter(Boolean).length);
  return sec.toFixed(2) + "s total · " + (sec / n).toFixed(3) + "s/word (" + n + " word" + (n === 1 ? "" : "s") + ")";
}

function job() {
  return {
    voice: $("voice-select").value,
    text: $("text-input").value.trim() || SAMPLE_TEXT,
    speed: parseFloat($("speed").value) || 1.0,
  };
}

function setOutput(blob, fmt, autoplay) {
  if (lastUrl) URL.revokeObjectURL(lastUrl); // else every synthesis pins its blob for the page's life
  const url = (lastUrl = URL.createObjectURL(blob));
  $("synth-empty").hidden = true;
  $("synth-player").hidden = false;
  player.load(url, { autoplay, blob });
  const dl = $("download");
  dl.href = url; dl.download = "auris." + fmt; dl.style.display = "";
}

function showLive(t0) {
  $("synth-empty").hidden = true;
  $("synth-player").hidden = true;
  $("speak-live").hidden = false;
  $("speak-live-time").textContent = "0:00";
  clearInterval(liveTimer);
  liveTimer = setInterval(() => { $("speak-live-time").textContent = mmss((performance.now() - t0) / 1000); }, 250);
}

function hideLive() {
  clearInterval(liveTimer);
  liveTimer = null;
  $("speak-live").hidden = true;
  $("synth-player").hidden = !lastUrl;
  $("synth-empty").hidden = !!lastUrl;
}

async function speakFull(run, signal) {
  const { voice, text, speed } = job();
  const fmt = $("format-select").value;
  const t0 = performance.now();
  const res = await api("/v1/audio/speech", {
    method: "POST", headers: { "content-type": "application/json" }, signal,
    body: JSON.stringify({ input: text, voice, speed, response_format: fmt }),
  });
  const blob = await res.blob();
  if (run !== runs || stopped) return;
  setOutput(blob, fmt, true);
  setMsg($("speak-msg"), "Done — " + Math.round(blob.size / 1024) + " KB · " + fmtTiming((performance.now() - t0) / 1000, text), "ok");
}

async function speakStream(run, signal) {
  const { voice, text, speed } = job();
  const t0 = performance.now();
  const res = await api("/v1/tts/stream", {
    method: "POST", headers: { "content-type": "application/json" }, signal,
    body: JSON.stringify({ text, voice, speed }),
  });
  if (run !== runs || stopped) return;
  const chunks = [];
  showLive(t0);
  const stream = (current = playPcmStream(res, { onChunk: (f32) => chunks.push(f32) }));
  stream.ended.then(() => {
    if (current === stream) current = null;
    if (run !== runs) return;
    hideLive();
    const pcm = concatF32(chunks);
    if (pcm.length) setOutput(new Blob([encodeWav(pcm, stream.rate)], { type: "audio/wav" }), "wav", false);
  });
  await stream.done;
  if (!stopped) {
    setMsg($("speak-msg"), "Stream finished · " + fmtTiming((performance.now() - t0) / 1000, text), "ok");
  }
  // Playback outlives the download — Kokoro can send a whole paragraph in half a second — so
  // Stop (which speak()'s `finally` disables) has to stay live until the last buffer has played.
  await stream.ended;
  if (stopped && run === runs) {
    setMsg($("speak-msg"), "Stopped · " + fmtTiming((performance.now() - t0) / 1000, text), "info");
  }
}

function stopAll() {
  if (busy) stopped = true;
  if (current) { stopped = true; current.stop(); current = null; }
  if (aborter) { aborter.abort(); aborter = null; }
  $("player").pause();
  hideLive();
  busy = false;
  $("stop-btn").disabled = true;
  updateCount();
}

async function speak() {
  if (!$("voice-select").value) return setMsg($("speak-msg"), "No voice selected.", "err");
  const run = ++runs;
  stopAll();
  stopped = false;
  const ctl = (aborter = new AbortController());
  busy = true;
  $("speak-btn").disabled = true; $("stop-btn").disabled = false; $("download").style.display = "none";
  setMsg($("speak-msg"), "Synthesizing…", "info");
  try {
    if ($("mode-select").value === "stream") await speakStream(run, ctl.signal);
    else await speakFull(run, ctl.signal);
  } catch (e) {
    if (run !== runs) return;
    hideLive();
    if (stopped) setMsg($("speak-msg"), "Stopped.", "info");
    else setMsg($("speak-msg"), e.message, "err");
  } finally {
    // A run that Stop + Speak superseded must not unlock the newer one.
    if (run === runs) {
      if (aborter === ctl) aborter = null;
      busy = false; $("stop-btn").disabled = true; updateCount();
    }
  }
}

function updateCount() {
  const n = $("text-input").value.length;
  const st = charState(n, limit);
  const el = $("char-count");
  el.textContent = limit ? n.toLocaleString() + " / " + limit.toLocaleString() : n.toLocaleString() + " characters";
  el.className = "small count " + st;
  $("speak-btn").disabled = busy || st === "over";
}

function showVoiceAvatar() {
  const v = $("voice-select").value;
  $("voice-avatar").replaceChildren(...(v ? [avatarEl(v, 32)] : []));
}

function showSpeed() {
  $("speed-out").textContent = (parseFloat($("speed").value) || 1).toFixed(2) + "×";
}

function showModeRules() {
  const streaming = $("mode-select").value === "stream";
  $("format-seg").classList.toggle("disabled", streaming);
  $("format-note").hidden = !streaming;
}

function applyPrefs(p) {
  $("speed").value = p.speed; $("format-select").value = p.format; $("mode-select").value = p.mode;
  const sel = $("voice-select");
  if (p.voice && p.voice !== sel.value && [...sel.options].some((o) => o.value === p.voice)) {
    sel.value = p.voice;
    showVoiceAvatar();
  }
  syncMode(); syncFormat(); showSpeed(); showModeRules();
}

// What you pick here is remembered (prefs.js), so a reload or a restarted `auris dashboard` comes
// back to it — and the Settings page shows it as the default. Setting a value in code fires no
// `change`, so applyPrefs answering the `auris:prefs` event this sends can't loop.
function rememberChoices() {
  $("voice-select").addEventListener("change", () => setPref("voice", $("voice-select").value));
  $("speed").addEventListener("change", () => setPref("speed", parseFloat($("speed").value) || 1.0));
  $("format-select").addEventListener("change", () => setPref("format", $("format-select").value));
  $("mode-select").addEventListener("change", () => setPref("mode", $("mode-select").value));
}

function syncVoices(state) {
  const sel = $("voice-select"), keep = sel.value || loadPrefs().voice;
  sel.innerHTML = "";
  for (const v of usableVoices(state.voices, state.me, state.admin && loadPrefs().showOthers)) {
    const o = document.createElement("option");
    o.value = v.name; o.textContent = v.kind === "preset" || v.kind === "blend" ? v.name + "  (" + v.kind + ")" : v.name;
    sel.appendChild(o);
  }
  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  showVoiceAvatar();
}

export async function initSynth(state) {
  player = createPlayer($("synth-player"), $("player"));
  syncMode = mirrorSelect($("mode-seg"), $("mode-select"));
  syncFormat = mirrorSelect($("format-seg"), $("format-select"));
  limit = (state.info && state.info.limits && state.info.limits.max_input_chars) || 0;
  applyPrefs(loadPrefs());
  syncVoices(state);
  document.addEventListener("auris:voices", () => syncVoices(state));
  document.addEventListener("auris:prefs", (e) => applyPrefs(e.detail));
  $("voice-select").addEventListener("change", showVoiceAvatar);
  rememberChoices();
  $("mode-select").addEventListener("change", showModeRules);
  $("speed").addEventListener("input", showSpeed);
  $("text-input").addEventListener("input", updateCount);
  $("text-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      if (!$("speak-btn").disabled) speak();
    }
  });
  $("speak-btn").onclick = speak;
  $("stop-btn").onclick = stopAll;
  updateCount();
  if (state.info && state.info.pipelines && !state.info.pipelines.tts) {
    $("synth-disabled").style.display = ""; $("panel-synth").classList.add("disabled");
    $("speak-btn").disabled = true;
  }
}
