// auris/server/static/js/users.js — admin: list, create, reset, disable and enable accounts.
//
// Same shape as the "API keys" card in settings.js: a one-time secret (here, a generated
// password) is written into the DOM and held nowhere else; every mutation is guarded with
// try/catch + setMsg so a refusal (e.g. 409 on the last active admin) reads as a message, not a
// silent no-op. Rows are built with textContent — an email may legally contain < > ' ".
"use strict";
import { api, getJson, postJson, setMsg } from "./api.js";
import { avatarEl } from "./avatar.js";
import { userStatus } from "./view-logic.js";
import { confirmDialog } from "./dialog.js";
import { copyWithFeedback } from "./clipboard.js";
import { $ } from "./app.js";

const changed = () => document.dispatchEvent(new CustomEvent("auris:users"));

function showIssued(password) {
  $("users-issued").textContent = password;
  $("users-issued-box").style.display = "";
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function action(label, attr, email, danger) {
  const b = el("button", danger ? "danger" : "", label);
  b.type = "button";
  b.setAttribute(attr, email);
  return b;
}

function userRow(u) {
  const st = userStatus(u);
  const acct = el("span", "acct");
  acct.append(avatarEl(u.email, 28), el("span", "", u.email));
  const status = el("span", "status-cell");
  status.append(el("span", "pill" + (st.tone ? " " + st.tone : ""), st.label));
  if (st.mustChange) status.append(el("span", "pill warn", "Must set password"));
  const menu = el("details", "menu");
  const summary = el("summary", "", "⋯");
  summary.setAttribute("aria-label", "Actions for " + u.email);
  const list = el("div", "menu-list");
  list.append(action("Reset password", "data-reset", u.email, false),
    u.disabled ? action("Enable", "data-enable", u.email, false) : action("Disable", "data-disable", u.email, true));
  menu.append(summary, list);
  const cells = [acct, el("span", "pill" + (u.role === "admin" ? " accent" : ""), u.role), status, menu];
  const tr = document.createElement("tr");
  cells.forEach((c, i) => {
    const td = document.createElement("td");
    if (i === 3) td.className = "actions";
    td.append(c);
    tr.append(td);
  });
  return tr;
}

async function renderUsers() {
  const tbody = $("users-table").querySelector("tbody");
  try {
    const rows = await getJson("/v1/users");
    tbody.replaceChildren(...rows.map(userRow));
    if (!rows.length) {
      const td = el("td", "empty", "No users yet.");
      td.colSpan = 4;
      const tr = document.createElement("tr");
      tr.append(td);
      tbody.append(tr);
    }
    tbody.querySelectorAll("button[data-reset]").forEach((b) => {
      b.onclick = () => resetPassword(b.dataset.reset);
    });
    tbody.querySelectorAll("button[data-disable]").forEach((b) => {
      b.onclick = () => disableUser(b.dataset.disable);
    });
    tbody.querySelectorAll("button[data-enable]").forEach((b) => {
      b.onclick = () => enableUser(b.dataset.enable);
    });
  } catch (e) { setMsg($("users-msg"), e.message, "err"); }
}

async function createUser(ev) {
  ev.preventDefault();
  const email = $("users-email").value.trim();
  const role = $("users-role").value;
  if (!email) return;
  try {
    const j = await (await postJson("/v1/users", { email, role })).json();
    showIssued(j.password);
    setMsg($("users-msg"), "Account for " + email + " created — copy the password now.", "ok");
    $("users-form").reset();
    await renderUsers();
    changed();
  } catch (e) { setMsg($("users-msg"), e.message, "err"); }
}

async function resetPassword(email) {
  const ok = await confirmDialog({
    title: "Reset the password for " + email + "?",
    body: "They will get a new one-time password and must set their own on next sign-in.",
    confirmLabel: "Reset password",
  });
  if (!ok) return;
  try {
    const j = await (await api("/v1/users/" + encodeURIComponent(email) + "/reset", { method: "POST" })).json();
    showIssued(j.password);
    setMsg($("users-msg"), "Password for " + email + " reset — copy it now.", "ok");
    await renderUsers();
    changed();
  } catch (e) { setMsg($("users-msg"), e.message, "err"); }
}

async function disableUser(email) {
  const ok = await confirmDialog({
    title: "Disable " + email + "?",
    body: "They will not be able to sign in or use any API key they hold. You can enable them again later.",
    confirmLabel: "Disable", danger: true,
  });
  if (!ok) return;
  try {
    await api("/v1/users/" + encodeURIComponent(email) + "/disable", { method: "POST" });
    await renderUsers();
    changed();
  } catch (e) { setMsg($("users-msg"), e.message, "err"); } // e.g. "the last active admin"
}

async function enableUser(email) {
  try {
    await api("/v1/users/" + encodeURIComponent(email) + "/enable", { method: "POST" });
    await renderUsers();
    changed();
  } catch (e) { setMsg($("users-msg"), e.message, "err"); }
}

export async function initUsers(state) {
  $("users-form").addEventListener("submit", createUser);
  $("users-copy").onclick = () =>
    copyWithFeedback($("users-copy"), $("users-issued").textContent, $("users-issued"));
  await renderUsers();
}
