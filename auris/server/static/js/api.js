// auris/server/static/js/api.js — the one place the page talks HTTP.
//
// Every request is same-origin and carries `X-Auris-Client: dashboard`: the server requires it
// on every cookie-authenticated /v1/* call as CSRF protection (a custom header cannot be sent
// cross-origin without a preflight this server never grants). The bearer path — the `auris
// dashboard` proxy, which injects Authorization server-side — ignores it.
//
// The page holds no credential of its own: no token is ever read from or written to
// localStorage/sessionStorage here. A 401 means the session is gone, so one handler sends the
// user back to sign-in rather than each panel inventing its own recovery.
"use strict";

const CSRF = { "X-Auris-Client": "dashboard" };
let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

// The shell shows "Offline" when the service can't be reached at all (not on an HTTP error —
// a 4xx/5xx still means the service answered). `document` is absent under node, hence the guard.
function announce(ok) {
  if (typeof document !== "undefined") {
    document.dispatchEvent(new CustomEvent("auris:network", { detail: { ok } }));
  }
}

export async function api(path, opts = {}) {
  const headers = Object.assign({}, CSRF, opts.headers || {});
  let res;
  try {
    res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
  } catch (e) {
    if (e && e.name === "AbortError") throw e; // a deliberate cancel (Stop), not an outage
    announce(false);
    throw new ApiError(0, "network", "Can't reach the service.");
  }
  announce(true);
  if (!res.ok) {
    let code = "error", message = res.statusText || ("HTTP " + res.status);
    try {
      const j = await res.json();
      code = j.error?.code || code;
      message = j.error?.message || message;
    } catch { /* not the {error:{code,message}} envelope — keep the status text */ }
    if (res.status === 401) onUnauthorized(message);
    throw new ApiError(res.status, code, message);
  }
  return res;
}

export const getJson = async (path) => (await api(path)).json();
export const postJson = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function setMsg(el, text, kind) {
  el.className = "msg " + (kind || "");
  el.textContent = text || "";
}

export function fmtSeconds(sec) { return (Math.round(sec * 10) / 10).toFixed(1) + "s"; }
