// auris/server/static/js/prefs.js — browser-local preferences; the only module touching storage.
//
// Preferences only: never a key, a token or anything else secret. They are per browser, sent
// with each request, and never mutate the server's own defaults.
"use strict";

const KEY = "auris.prefs.v1";
const DEFAULTS = {
  asrModel: "", language: "auto", voice: "", speed: 1.0, format: "mp3", mode: "full",
};

export function loadPrefs() {
  // Storage can throw outright (private mode, blocked site data) or hold junk from an older
  // version — either way the page must still boot, so fall back to the defaults.
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; }
  catch { return { ...DEFAULTS }; }
}

export function setPref(key, value) {
  const prefs = loadPrefs();
  prefs[key] = value;
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* not worth failing over */ }
  document.dispatchEvent(new CustomEvent("auris:prefs", { detail: prefs }));
  return prefs;
}
