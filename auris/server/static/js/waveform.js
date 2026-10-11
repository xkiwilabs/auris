// auris/server/static/js/waveform.js — PCM → bar heights, and drawing bars on a canvas.
// `peaks`/`levelOf` are pure (node-tested); `drawBars` only touches the canvas it is given.
"use strict";

export function peaks(samples, buckets) {
  const n = Math.max(0, Math.floor(buckets));
  const out = new Array(n).fill(0);
  if (!samples || !samples.length || !n) return out;
  const size = samples.length / n;
  let max = 0;
  for (let i = 0; i < n; i++) {
    const start = Math.min(samples.length - 1, Math.floor(i * size));
    const end = Math.max(start + 1, Math.floor((i + 1) * size));
    let p = 0;
    for (let j = start; j < end && j < samples.length; j++) {
      const v = Math.abs(samples[j]);
      if (v > p) p = v;
    }
    out[i] = p;
    if (p > max) max = p;
  }
  return max > 0 ? out.map((v) => v / max) : out;
}

// A loudness for the live recording meter: RMS, scaled so ordinary speech fills most of the bar.
export function levelOf(samples) {
  if (!samples || !samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

// Bars left of `progress` (0..1) use --wave-played, the rest --wave-rest; both are CSS custom
// properties on the canvas or an ancestor, so the theme owns the colours.
export function drawBars(canvas, values, progress = 0) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h || !values.length) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr);
  if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const css = getComputedStyle(canvas);
  const played = css.getPropertyValue("--wave-played").trim() || "#8b7cff";
  const rest = css.getPropertyValue("--wave-rest").trim() || "rgba(255,255,255,.16)";
  const n = values.length, gap = 2;
  const bw = Math.max(1, (w - gap * (n - 1)) / n);
  for (let i = 0; i < n; i++) {
    const bh = Math.max(2, values[i] * (h - 2));
    ctx.fillStyle = (i + 0.5) / n <= progress ? played : rest;
    ctx.fillRect(i * (bw + gap), (h - bh) / 2, bw, bh);
  }
}
