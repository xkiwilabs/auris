// auris/server/static/js/view-logic.js — pure helpers the panels share. No DOM, so node can test it.
"use strict";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Whose a voice is, from the viewer's side: "mine", "shared" (someone else's shared voice, or a
// legacy unowned one — readable by everyone), "others" (someone else's private voice — the server
// only ever sends these to an admin) or "presets".
function section(v, me) {
  if (v.kind === "preset") return "presets";
  if (v.owner != null && me && v.owner === me.email) return "mine";
  if (v.owner == null || v.visibility === "shared") return "shared";
  return "others";
}

// The library's sections, after the search box (name or owner email) and the filter chips
// ("all" | "mine" | "shared" | "presets"). Whether "others" is shown at all is the page's call.
export function groupVoices(voices, me, { query = "", filter = "all" } = {}) {
  const q = String(query || "").trim().toLowerCase();
  const out = { mine: [], shared: [], others: [], presets: [] };
  for (const v of voices) {
    if (q && !v.name.toLowerCase().includes(q) && !String(v.owner || "").toLowerCase().includes(q)) {
      continue;
    }
    const s = section(v, me);
    if (filter === "all" || filter === s) out[s].push(v);
  }
  return out;
}

// What the voice pickers offer: everything, minus other people's private voices unless an admin
// has chosen to see them (the "Show others' private voices" toggle).
export function usableVoices(voices, me, showOthers) {
  return voices.filter((v) => showOthers || section(v, me) !== "others");
}

// Whole-number percentages for a blend's weights that always add up to 100 (largest remainder),
// so the sliders' read-outs never show 33 + 33 + 33. All-zero weights read as an even split.
export function blendShares(weights) {
  const w = weights.map((x) => (Number.isFinite(x) && x > 0 ? x : 0));
  const total = w.reduce((a, b) => a + b, 0);
  if (!w.length) return [];
  const exact = w.map((x) => (total ? (x / total) * 100 : 100 / w.length));
  const out = exact.map(Math.floor);
  let left = 100 - out.reduce((a, b) => a + b, 0);
  const order = exact.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of order) { if (left <= 0) break; out[i] += 1; left -= 1; }
  return out;
}

// The voice names a blend mixes, for a card's one-line summary.
export function blendSummary(voice) {
  return "blend of " + (voice.blend || []).map((c) => c.voice).join(" + ");
}

export function ownerLabel(voice, me) {
  if (voice.owner == null) return "No owner";
  if (me && me.email && voice.owner === me.email) return "You";
  return voice.owner;
}

// `limit` comes from /v1/info; 0 or missing means "unknown", which must never block typing.
export function charState(length, limit) {
  if (!limit) return "ok";
  if (length > limit) return "over";
  if (length >= limit * 0.9) return "near";
  return "ok";
}

export function userStatus(user) {
  return {
    label: user.disabled ? "Disabled" : "Active",
    tone: user.disabled ? "" : "ok",
    mustChange: !!user.must_change_password,
  };
}

// Mirrors the <input accept> rules. A file with no MIME type (common for .opus) is let through:
// the server is the real judge, and refusing it here would block a valid upload.
export function accepts(file, accept) {
  const rules = String(accept || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!rules.length) return true;
  const type = String(file.type || "").toLowerCase();
  const name = String(file.name || "").toLowerCase();
  if (!type) return true;
  return rules.some((r) =>
    r.endsWith("/*") ? type.startsWith(r.slice(0, -1)) : r.startsWith(".") ? name.endsWith(r) : type === r);
}

export function mmss(seconds) {
  const s = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

export function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
}

export function relTime(iso, now = Date.now()) {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "never";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  if (s < 7 * 86400) return Math.floor(s / 86400) + " d ago";
  return fmtDate(iso);
}
