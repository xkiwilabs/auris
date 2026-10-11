// auris/server/static/js/app.js — boot, sign-in/app switch, navigation, and the shell's small
// global behaviours (the phone More sheet, ⋯ menus, jump links).
//
// Boot asks /auth/me whether a session cookie is live — the same on the server and behind
// `auris dashboard`'s localhost proxy, which forwards /auth/* too. Panels are initialised once,
// after the first successful entry into the app.
"use strict";
import { setUnauthorizedHandler, setMsg } from "./api.js";
import * as auth from "./auth.js";
import { avatarEl } from "./avatar.js";
// PANEL IMPORTS — one line per panel:
import { initSettings } from "./settings.js";
import { initVoices } from "./voices.js";
import { initSynth } from "./synth.js";
import { initTranscribe } from "./transcribe.js";
import { initAccount } from "./account.js";
import { initUsers } from "./users.js";

export const $ = (id) => document.getElementById(id);
export const state = { me: null, admin: false, voices: [], models: null, info: null };

// "account" is every signed-in user's own tab, so it is here from the start. "users" is
// admin-only and is pushed in (and its nav button revealed) the first time showApp sees
// state.admin true — see revealUsersTab below.
const TABS = ["voices", "synth", "transcribe", "settings", "account"];
const MANAGE = ["settings", "account", "users"]; // the tabs that live in the phone's More sheet
let booted = false;

function setStatus(ok, label) {
  $("status-dot").className = "dot " + (ok ? "ok" : "bad");
  $("status-label").textContent = label;
}

function setMore(open) {
  $("side").classList.toggle("more-open", open);
  $("tab-more").setAttribute("aria-expanded", String(open));
}

function revealUsersTab() {
  if (!TABS.includes("users")) TABS.push("users");
  $("tab-users").style.display = "";
  $("tab-users").onclick = () => showTab("users");
}

function showTab(name) {
  if (!TABS.includes(name)) name = "voices";
  for (const t of TABS) {
    $("panel-" + t).style.display = t === name ? "" : "none";
    $("tab-" + t).classList.toggle("active", t === name);
  }
  $("tab-more").classList.toggle("active", MANAGE.includes(name));
  setMore(false);
  window.scrollTo({ top: 0, behavior: "instant" });
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}

function showSignIn(message) {
  $("app").style.display = "none";
  $("signin").style.display = "";
  $("signin-form").style.display = "";
  $("pwchange-form").style.display = "none";
  $("signout-btn").style.display = "none";
  $("who").textContent = "";
  setStatus(false, "Signed out");
  setMsg($("signin-msg"), message || "", message ? "err" : "");
  $("signin-password").value = "";  // never leave a password in the DOM
  ($("signin-email").value ? $("signin-password") : $("signin-email")).focus();
}

// The third state: authenticated, but the account must set its own password before it can act.
// The overlay stays up and the app stays hidden — every other route answers 403 anyway, so
// showing the panels would only produce errors.
function showPasswordChange(message) {
  $("app").style.display = "none";
  $("signin").style.display = "";
  $("signin-form").style.display = "none";
  $("pwchange-form").style.display = "";
  $("signout-btn").style.display = "none";
  setStatus(false, "Password change needed");
  setMsg($("pwchange-msg"), message || "", message ? "err" : "");
  $("pwchange-current").focus();
}

async function showApp() {
  $("signin").style.display = "none";
  $("app").style.display = "";
  $("who").textContent = state.me.principal + (state.admin ? " (admin)" : "");
  $("who-avatar").replaceChildren(avatarEl(state.me.email || state.me.principal, 30));
  $("signout-btn").style.display = "";
  setStatus(true, "Connected");
  if (state.admin) revealUsersTab();
  showTab(location.hash.replace("#", "") || "voices");
  if (booted) return;
  booted = true;
  // PANEL INIT — one call per panel, in this order:
  // settings first: it populates state.info / state.models, which the other panels read.
  await initSettings(state);
  await initVoices(state);
  await initSynth(state);
  await initTranscribe(state);
  await initAccount(state);
  if (state.admin) await initUsers(state);
}

function bindShell() {
  $("tab-more").onclick = () => setMore(!$("side").classList.contains("more-open"));
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") setMore(false); });
  document.addEventListener("click", (ev) => {
    const jump = ev.target.closest("[data-jump]");
    if (jump) {
      const target = $(jump.dataset.jump);
      if (target) target.scrollIntoView({ block: "start" });
    }
    for (const menu of document.querySelectorAll("details.menu[open]")) {
      if (!menu.contains(ev.target) || ev.target.closest(".menu-list button")) menu.open = false;
    }
    if ($("side").classList.contains("more-open") && !$("side").contains(ev.target)) setMore(false);
  });
  // Only flip the status while the app is showing; the sign-in screens own it otherwise.
  document.addEventListener("auris:network", (ev) => {
    if ($("app").style.display === "none") return;
    if (!ev.detail.ok) setStatus(false, "Offline");
    else if ($("status-label").textContent === "Offline") {
      setStatus(true, "Connected");
    }
  });
}

async function boot() {
  setUnauthorizedHandler((message) => showSignIn(message || "signed out"));
  // A 403 password_change_required can arrive from any panel's call, not just sign-in.
  window.addEventListener("unhandledrejection", (ev) => {
    if (ev.reason && ev.reason.code === "password_change_required") {
      ev.preventDefault();
      showPasswordChange("Set a new password to continue.");
    }
  });
  bindShell();
  for (const t of TABS) $("tab-" + t).onclick = () => showTab(t);
  window.addEventListener("hashchange", () => showTab(location.hash.replace("#", "")));

  $("signin-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setMsg($("signin-msg"), "Signing in…", "info");
    try {
      state.me = await auth.login(
        $("signin-email").value.trim(), $("signin-password").value
      );
      $("signin-password").value = "";
      state.admin = !!state.me.admin;
      if (state.me.must_change_password) return showPasswordChange();
      await showApp();
    } catch (e) { setMsg($("signin-msg"), e.message, "err"); }
  });

  $("pwchange-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const next = $("pwchange-new").value;
    if (next !== $("pwchange-confirm").value) {
      return setMsg($("pwchange-msg"), "The two new passwords do not match.", "err");
    }
    setMsg($("pwchange-msg"), "Setting password…", "info");
    try {
      state.me = await auth.changePassword($("pwchange-current").value, next);
      state.admin = !!state.me.admin;
      for (const id of ["pwchange-current", "pwchange-new", "pwchange-confirm"]) $(id).value = "";
      await showApp();
    } catch (e) { setMsg($("pwchange-msg"), e.message, "err"); }
  });

  $("signout-btn").onclick = async () => {
    try { await auth.logout(); } catch { /* the cookie is going away either way */ }
    // A full reload, not showSignIn(): the panels keep the old principal's DOM (the admin key
    // table, a just-issued key shown once) and the next person to sign in must not inherit it.
    location.reload();
  };

  // auth.me() swallows its own 401 so the global handler doesn't fire on a cold load; every
  // other 401 anywhere in the app routes through showSignIn.
  const me = await auth.me();
  if (!me) return showSignIn();
  state.me = me;
  state.admin = !!me.admin;
  // A page reload while an account's forced change is still pending must land back on that
  // screen, not the app — every panel call it could make would just answer 403 anyway.
  if (me.must_change_password) return showPasswordChange();
  await showApp();
}

boot().catch((e) => showSignIn(e.message));
