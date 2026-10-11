// auris/server/static/js/transcript-format.js — pure; mirrors auris/server/transcript_format.py.
"use strict";

export function clock(seconds, sep) {
  const totalMs = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(totalMs / 3600000), m = Math.floor((totalMs % 3600000) / 60000);
  const s = Math.floor((totalMs % 60000) / 1000), ms = totalMs % 1000;
  const p = (n, w) => String(n).padStart(w, "0");
  return p(h, 2) + ":" + p(m, 2) + ":" + p(s, 2) + sep + p(ms, 3);
}

export function toSrt(segments) {
  return segments.map((s, i) => (i + 1) + "\n" + clock(s.start, ",") + " --> " + clock(s.end, ",") + "\n" + s.text + "\n").join("\n");
}

export function toVtt(segments) {
  return "WEBVTT\n\n" + segments.map((s) => clock(s.start, ".") + " --> " + clock(s.end, ".") + "\n" + s.text + "\n").join("\n");
}
