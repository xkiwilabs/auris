// auris/server/static/js/avatar.js — initials on a per-name gradient, so a voice or person keeps
// the same colour everywhere it appears. Decorative: the name is always shown as text beside it.
"use strict";

const PAIRS = [
  ["#8b7cff", "#2dd4bf"], ["#f472b6", "#8b7cff"], ["#38bdf8", "#818cf8"], ["#2dd4bf", "#a3e635"],
  ["#fb923c", "#f472b6"], ["#fbbf24", "#fb7185"], ["#a78bfa", "#38bdf8"], ["#34d399", "#38bdf8"],
];

function hash(s) { // FNV-1a, 32-bit
  let h = 0x811c9dc5;
  for (const ch of s) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  return h;
}

export function initials(name) {
  const base = String(name || "").split("@")[0];
  const words = base.trim().split(/[\s._-]+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  const w = words[0];
  const digits = w.match(/\d+$/);
  if (digits && digits[0].length < w.length) return digits[0].slice(-2).padStart(2, "0");
  return w.slice(0, 2).toUpperCase();
}

export function gradientFor(name) {
  return PAIRS[hash(String(name || "").toLowerCase()) % PAIRS.length];
}

export function avatarEl(name, size = 36) {
  const el = document.createElement("span");
  const [a, b] = gradientFor(name);
  el.className = "avatar";
  el.setAttribute("aria-hidden", "true");
  el.style.setProperty("--av-size", size + "px");
  el.style.background = "linear-gradient(135deg, " + a + ", " + b + ")";
  el.textContent = initials(name);
  return el;
}
