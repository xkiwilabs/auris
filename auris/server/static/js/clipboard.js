// auris/server/static/js/clipboard.js — copy text, with a manual fallback off a secure context.
//
// The dashboard is often served over plain http on the tailnet, where navigator.clipboard does
// not exist. There, the exact text is copied through a throwaway textarea (execCommand still works
// off a secure context); only if that fails is `fallbackEl` selected on screen for Ctrl+C.
"use strict";

const labels = new WeakMap();
const timers = new WeakMap();

export async function copyText(text, fallbackEl) {
  if (window.isSecureContext && navigator.clipboard) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall back */ }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed"; ta.style.opacity = "0"; ta.style.pointerEvents = "none";
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  if (ok) return true;
  if (fallbackEl) {
    const range = document.createRange();
    range.selectNodeContents(fallbackEl);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  return false;
}

export async function copyWithFeedback(btn, text, fallbackEl) {
  if (!labels.has(btn)) labels.set(btn, btn.textContent);
  const ok = await copyText(text, fallbackEl);
  btn.textContent = ok ? "Copied" : "Press Ctrl+C";
  clearTimeout(timers.get(btn));
  timers.set(btn, setTimeout(() => { btn.textContent = labels.get(btn); }, 2000));
  return ok;
}
