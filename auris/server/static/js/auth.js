// auris/server/static/js/auth.js — sign-in, sign-out, and who the page is talking as.
//
// The password is posted once and exchanged for an HttpOnly session cookie; the page never
// keeps it (not in a variable that outlives the submit handler, and never in browser storage).
"use strict";
import { api, getJson, postJson } from "./api.js";

// `auris dashboard` serves this page from localhost and injects the bearer token server-side.
// Only that proxy answers /__proxy, so this is the one reliable "sign-in does not apply" signal.
export async function detectProxy() {
  try { const r = await fetch("/__proxy"); return r.ok && (await r.json()).proxy === true; }
  catch { return false; }
}

// null when signed out (the 401 handler is suppressed for this probe by catching it here).
export async function me() {
  try { return await getJson("/auth/me"); }
  catch (e) { if (e.status === 401) return null; throw e; }
}

// Email + password, never a key: a cookie means a human, a bearer means a machine.
export const login = async (email, password) =>
  (await postJson("/auth/login", { email, password })).json();

export const changePassword = async (current_password, new_password) =>
  (await postJson("/auth/password", { current_password, new_password })).json();

export const logout = () => postJson("/auth/logout", {});

// `/v1/keys` is open to any signed-in user now, so it is no longer an admin probe. /auth/me
// reports the role directly; in proxy mode /auth/me is not forwarded (the proxy only forwards
// /ready, /health and /v1/*, and answers everything else with its own 404 — not the server's
// 401), so `me()` is wrapped here too and any failure, not just "signed out", falls through to
// asking whether the admin-only users API answers.
export async function probeAdmin() {
  let who = null;
  try { who = await me(); } catch { /* not forwarded through the proxy, or some other error */ }
  if (who && who.role) return who.role === "admin";
  try { await api("/v1/users"); return true; }
  catch { return false; }
}
