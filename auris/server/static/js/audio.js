// auris/server/static/js/audio.js — mic capture, gap-free PCM playback, and small audio utilities.
"use strict";
import { levelOf } from "./waveform.js";

export function micAvailable() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

/** Start recording. Resolves once the mic is open; `file` resolves when stopped (or at maxSeconds). */
export async function recordClip({ maxSeconds = null, onTick = () => {} } = {}) {
  if (!micAvailable()) throw new Error("Microphone unavailable here — upload a file instead.");
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { throw new Error("Microphone permission denied — allow it for this site, or upload a file."); }

  const chunks = [];
  const rec = new MediaRecorder(stream);
  const start = Date.now();
  let timer = null;
  const file = new Promise((resolve) => {
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      const ext = (blob.type.split("/")[1] || "webm").split(";")[0];
      resolve(new File([blob], "recording." + ext, { type: blob.type }));
    };
  });
  rec.start();
  timer = setInterval(() => {
    const elapsed = (Date.now() - start) / 1000;
    if (maxSeconds !== null && elapsed >= maxSeconds) { rec.stop(); return; }
    onTick(elapsed, maxSeconds);
  }, 100);
  return { stop: () => { if (rec.state === "recording") rec.stop(); }, file, stream };
}

/** A live 0..1 loudness reading from a mic stream, for the recording meter. Call close() after. */
export function levelMeter(stream) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  return {
    level() { analyser.getFloatTimeDomainData(buf); return levelOf(buf); },
    close() { ctx.close().catch(() => {}); },
  };
}

/** Duration in seconds from the file's metadata, or null when the browser can't say. */
export function durationOf(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const a = new Audio();
    const done = (v) => { URL.revokeObjectURL(url); resolve(v); };
    a.preload = "metadata";
    a.onloadedmetadata = () => done(Number.isFinite(a.duration) ? a.duration : null);
    a.onerror = () => done(null);
    a.src = url;
  });
}

/** Play a streaming s16le PCM response as it arrives, scheduling buffers back-to-back. */
export function playPcmStream(response, { onChunk = () => {} } = {}) {
  const rate = parseInt(response.headers.get("X-Sample-Rate") || "24000", 10);
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const reader = response.body.getReader();
  let playhead = ctx.currentTime + 0.05;
  let stopped = false;
  let finish = () => {};
  const ended = new Promise((resolve) => { finish = resolve; });

  const done = (async () => {
    let leftover = new Uint8Array(0);
    while (!stopped) {
      const { done, value } = await reader.read();
      if (done) break;
      const merged = new Uint8Array(leftover.length + value.length);
      merged.set(leftover); merged.set(value, leftover.length);
      const usable = merged.length - (merged.length % 2);
      if (usable > 0) {
        const i16 = new Int16Array(merged.buffer, 0, usable / 2);
        const f32 = new Float32Array(i16.length);
        for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
        onChunk(f32);
        const buf = ctx.createBuffer(1, f32.length, rate);
        buf.copyToChannel(f32, 0);
        const src = ctx.createBufferSource();
        src.buffer = buf; src.connect(ctx.destination);
        const t = Math.max(ctx.currentTime, playhead);
        src.start(t); playhead = t + buf.duration;
      }
      leftover = merged.slice(usable);
    }
    // Release the context once the last scheduled buffer has played (browsers cap how many
    // can be open). stop() may already have closed it — close() on a closed context rejects.
    const remaining = Math.max(0, playhead - ctx.currentTime) * 1000;
    setTimeout(() => { ctx.close().catch(() => {}); finish(); }, remaining + 100);
  })();
  done.catch(() => finish()); // a failed read must not leave `ended` hanging forever

  return {
    done,
    ended,
    rate,
    // cancel() and close() reject asynchronously (an errored stream; a context the natural end
    // already closed), so a try/catch alone would leave the rejection unhandled.
    stop() {
      stopped = true;
      try { reader.cancel().catch(() => {}); } catch {}
      try { ctx.close().catch(() => {}); } catch {}
      finish();
    },
  };
}

export function concatF32(chunks) {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

/** 16-bit PCM mono WAV — lets a finished stream be replayed and saved. */
export function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}
