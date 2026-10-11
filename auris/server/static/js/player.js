// auris/server/static/js/player.js — a waveform player over a hidden <audio> element.
//
// The <audio> stays the source of truth (tests and the runbook read its `src`); this only draws
// controls over it. The waveform comes from decoding the clip in an OfflineAudioContext; if the
// browser can't decode the format, the clip still plays and the bars stay flat.
"use strict";
import { peaks, drawBars } from "./waveform.js";
import { mmss } from "./view-logic.js";

const BUCKETS = 72;
const FLAT = new Array(BUCKETS).fill(0.12);

export function createPlayer(root, audio, { onEmptyPlay = null } = {}) {
  root.classList.add("player");
  root.innerHTML =
    '<button type="button" class="play-btn" aria-label="Play"></button>' +
    '<canvas class="wave" aria-hidden="true"></canvas>' +
    '<span class="time">0:00</span>';
  const btn = root.querySelector(".play-btn");
  const canvas = root.querySelector("canvas");
  const time = root.querySelector(".time");
  let bars = FLAT;
  let loads = 0; // bumped by every load/clear, so a slow decode can't paint over a newer clip

  const finite = (n) => Number.isFinite(n) ? n : 0;
  const progress = () => (finite(audio.duration) ? audio.currentTime / audio.duration : 0);
  const draw = () => drawBars(canvas, bars, progress());
  function sync() {
    const playing = !audio.paused && !audio.ended;
    root.classList.toggle("playing", playing);
    btn.setAttribute("aria-label", playing ? "Pause" : "Play");
    time.textContent = mmss(playing || audio.currentTime > 0 ? audio.currentTime : finite(audio.duration));
    draw();
  }

  btn.addEventListener("click", () => {
    if (!audio.getAttribute("src")) { if (onEmptyPlay) onEmptyPlay(); return; }
    if (audio.paused) audio.play().catch(() => {}); else audio.pause();
  });
  canvas.addEventListener("click", (ev) => {
    if (!finite(audio.duration)) return;
    const r = canvas.getBoundingClientRect();
    audio.currentTime = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * audio.duration;
  });
  for (const e of ["play", "pause", "timeupdate", "ended", "loadedmetadata", "emptied"]) {
    audio.addEventListener(e, sync);
  }
  if (window.ResizeObserver) new ResizeObserver(draw).observe(canvas);

  async function load(src, { autoplay = false, blob = null } = {}) {
    const mine = ++loads;
    bars = FLAT;
    audio.src = src;
    sync();
    if (autoplay) audio.play().catch(() => {});
    try {
      const data = await (blob || (await (await fetch(src)).blob())).arrayBuffer();
      const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      const decoded = await new Offline(1, 1, 44100).decodeAudioData(data);
      if (mine === loads) bars = peaks(decoded.getChannelData(0), BUCKETS);
    } catch { /* undecodable here: it still plays, with flat bars */ }
    if (mine === loads) draw();
  }

  function clear() {
    loads++;
    bars = FLAT;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    sync();
  }

  sync();
  return { load, clear, pause: () => audio.pause() };
}
