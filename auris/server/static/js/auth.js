// auris/server/static/js/auth.js — sign-in, sign-out, and who the page is talking as.
//
// The password is posted once and exchanged for an HttpOnly session cookie; the page never
// keeps it (not in a variable that outlives the submit handler, and never in browser storage).
"use strict";
import { getJson, postJson } from "./api.js";

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
