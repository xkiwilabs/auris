// auris/server/static/js/transcribe.js — the Transcribe panel.
"use strict";
import { api, setMsg, fmtSeconds } from "./api.js";
import { recordClip, micAvailable, durationOf } from "./audio.js";
import { loadPrefs } from "./prefs.js";
import { toSrt, toVtt, clock } from "./transcript-format.js";
import { mmss, fmtBytes } from "./view-logic.js";
import { attachDropzone } from "./dropzone.js";
import { copyWithFeedback } from "./clipboard.js";
import { $ } from "./app.js";

let recorder = null;
let last = null; // last verbose_json result, for Copy and the download buttons

function fillModels(state) {
  const sel = $("stt-model"), p = loadPrefs();
  sel.innerHTML = "";
  if (!state.models) return;
  for (const m of state.models.models) {
    const o = document.createElement("option");
    o.value = m.id; o.textContent = m.id + "  (" + m.params + (m.lang === "multi" ? ", multilingual" : "") + ")";
    sel.appendChild(o);
  }
  sel.value = p.asrModel || state.models.default;
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// The chosen file is whatever #stt-file holds; this only describes it.
function showFile(file, seconds = null) {
  $("stt-filerow").hidden = !file;
  if (!file) return;
  $("stt-filename").textContent = file.name;
  $("stt-filemeta").textContent = fmtBytes(file.size) + (seconds ? " · " + mmss(seconds) : "");
  if (seconds) return;
  durationOf(file).then((s) => {
    if (s && $("stt-file").files[0] === file) $("stt-filemeta").textContent = fmtBytes(file.size) + " · " + mmss(s);
  });
}

function render(result, elapsed) {
  last = result;
  $("stt-result").style.display = "";
  $("stt-text").value = result.text;
  $("stt-segments").replaceChildren(...result.segments.map((s) => {
    const li = document.createElement("li");
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = mmss(s.start);
    ts.title = clock(s.start, ".") + " → " + clock(s.end, ".");
    const p = document.createElement("p");
    p.textContent = s.text.trim();
    li.append(ts, p);
    return li;
  }));
  const rtf = result.duration ? (elapsed / result.duration).toFixed(2) : "?";
  $("stt-stats").textContent = fmtSeconds(result.duration) + " of audio · " + (result.language || "?") +
    (result.language_probability ? " (" + Math.round(result.language_probability * 100) + "%)" : "") +
    " · " + elapsed.toFixed(1) + "s · RTF " + rtf + (result.task === "translate" ? " · translated" : "");
}

async function run(state) {
  const file = $("stt-file").files[0];
  if (!file) return setMsg($("stt-msg"), "Choose a file or record something first.", "err");
  const fd = new FormData();
  fd.append("file", file, file.name || "audio.webm");
  fd.append("model", $("stt-model").value);
  const lang = $("stt-language").value.trim();
  fd.append("language", lang || "auto");
  fd.append("task", $("stt-translate").checked ? "translate" : "transcribe");
  fd.append("response_format", "verbose_json");
  if ($("stt-words").checked) fd.append("timestamp_granularities[]", "word");
  $("stt-run-btn").disabled = true; $("stt-result").style.display = "none"; $("stt-stats").textContent = "";
  setMsg($("stt-msg"), "Transcribing…", "info");
  const t0 = performance.now();
  try {
    const res = await api("/v1/audio/transcriptions", { method: "POST", body: fd });
    render(await res.json(), (performance.now() - t0) / 1000);
    setMsg($("stt-msg"), "", "");
  } catch (e) {
    setMsg($("stt-msg"), e.status === 429 ? "Server busy — try again in a moment." : e.message, "err");
  } finally { $("stt-run-btn").disabled = false; }
}

async function toggleRecord() {
  const btn = $("stt-record-btn");
  if (recorder) { recorder.stop(); return; }
  let elapsed = 0;
  try {
    recorder = await recordClip({ maxSeconds: null, onTick: (t) => {
      elapsed = t;
      $("stt-record-status").textContent = "Recording… " + mmss(t);
    } });
  } catch (e) { return setMsg($("stt-msg"), e.message, "err"); }
  btn.textContent = "■ Stop recording";
  const file = await recorder.file;
  recorder = null;
  btn.textContent = "● Record from microphone";
  const dt = new DataTransfer(); dt.items.add(file); $("stt-file").files = dt.files;
  showFile(file, elapsed);
  $("stt-record-status").textContent = "";
}

export async function initTranscribe(state) {
  // `/v1/audio/models` always answers 200 (even with STT off) and reports `pipelines.stt`
  // itself, so that — or the equivalent `state.info.pipelines.stt` from /v1/info — is the
  // signal for "switched off", not `state.models` being null (a network/401 hiccup fetching
  // it isn't the same thing, and fillModels() below already copes with a null state.models).
  const stt = state.models ? state.models.pipelines?.stt : state.info?.pipelines?.stt;
  if (stt === false) {
    $("transcribe-disabled").style.display = ""; $("panel-transcribe").classList.add("disabled");
    $("stt-run-btn").disabled = true;
    return;
  }
  fillModels(state);
  $("stt-language").value = loadPrefs().language || "auto";
  document.addEventListener("auris:prefs", () => fillModels(state));
  if ((state.proxied || window.isSecureContext) && micAvailable()) $("stt-record-btn").style.display = "";
  $("stt-record-btn").onclick = toggleRecord;
  attachDropzone($("stt-drop"), $("stt-file"), (f) => { setMsg($("stt-msg"), "", ""); showFile(f); },
    (f) => setMsg($("stt-msg"), "“" + f.name + "” isn't an audio file.", "err"));
  $("stt-run-btn").onclick = () => run(state);
  $("stt-copy").onclick = () => last && copyWithFeedback($("stt-copy"), last.text, $("stt-segments"));
  $("stt-dl-txt").onclick = () => last && download("transcript.txt", last.text + "\n", "text/plain");
  $("stt-dl-srt").onclick = () => last && download("transcript.srt", toSrt(last.segments), "text/plain");
  $("stt-dl-vtt").onclick = () => last && download("transcript.vtt", toVtt(last.segments), "text/vtt");
}
