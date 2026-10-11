// auris/server/static/js/account.js — your own password and your own API keys.
//
// `/v1/keys` returns every key *with* an owner for an admin (so the admin keys table in the
// Settings panel can manage anyone's). `renderKeys` below filters that down to just the caller's
// own keys whenever the page knows who the caller is — but in proxy mode `state.me.email` is
// never set (`/auth/me` is not forwarded; see auth.js's `probeAdmin`), so filtering by owner
// there would silently show an empty list. In that case we show the unfiltered list instead and
// label each row's owner, rather than filtering to nothing or implying every row is "mine".
"use strict";
import { api, getJson, postJson, setMsg } from "./api.js";
import { changePassword } from "./auth.js";
import { avatarEl } from "./avatar.js";
import { fmtDate, relTime } from "./view-logic.js";
import { copyWithFeedback } from "./clipboard.js";
import { confirmDialog } from "./dialog.js";
import { $ } from "./app.js";

function button(label, cls, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = cls;
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

async function renderKeys(state) {
  const rows = await getJson("/v1/keys");
  const mine = state.me && state.me.email;
  const live = rows.filter((k) => !k.revoked_at && (!mine || k.owner === mine));
  const showOwners = !mine;
  $("account-keys-title").textContent = showOwners ? "API keys" : "My API keys";
  const list = $("account-keys");
  list.replaceChildren();
  for (const k of live) {
    const li = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "key-ic";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "⚿";
    const main = document.createElement("div");
    main.className = "key-main";
    const name = document.createElement("b");
    name.textContent = k.name + (showOwners ? " — " + (k.owner || "unowned") : "");
    const meta = document.createElement("small");
    meta.textContent = "Created " + fmtDate(k.created_at) + " · " +
      (k.last_used_at ? "used " + relTime(k.last_used_at) : "never used");
    main.append(name, meta);
    li.append(icon, main);
    // Rotate issues a replacement before revoking the old one, so it needs the same owner the
    // proxy cannot supply — see initAccount's guard on the Issue form for why.
    if (!state.proxied) li.append(button("Rotate", "btn small", () => rotateKey(state, k.name)));
    li.append(button("Revoke", "btn small danger", () => revokeKey(state, k.name)));
    list.append(li);
  }
  if (!live.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No keys yet.";
    list.append(li);
  }
}

// Shown once, deliberately not stored anywhere: the server never returns it again.
function showIssued(key) {
  $("account-issued").textContent = key;
  $("account-issued-box").style.display = "";
  setMsg($("account-keys-msg"), "Copy this key now — you will not see it again.", "info");
}

async function issueKey(state, name) {
  showIssued((await (await postJson("/v1/keys", { name })).json()).key);
  await renderKeys(state);
}

// Rotate = issue the replacement first, then revoke the old one. In that order: if the revoke
// fails the person still has a working key, whereas the reverse would strand them. The new key
// gets a dated name because a revoked key's name stays taken.
async function rotateKey(state, name) {
  const ok = await confirmDialog({
    title: `Rotate “${name}”?`,
    body: "A new key is issued and this one stops working at once — anything still using it will fail.",
    confirmLabel: "Rotate", danger: true,
  });
  if (!ok) return;
  const replacement = `${name}-${new Date().toISOString().slice(0, 10)}`;
  try {
    showIssued((await (await postJson("/v1/keys", { name: replacement })).json()).key);
    await api(`/v1/keys/${encodeURIComponent(name)}`, { method: "DELETE" });
    await renderKeys(state);
  } catch (e) { setMsg($("account-keys-msg"), e.message, "err"); }
}

async function revokeKey(state, name) {
  const ok = await confirmDialog({
    title: `Revoke “${name}”?`,
    body: "It stops working at once, for anything still using it. This can't be undone.",
    confirmLabel: "Revoke", danger: true,
  });
  if (!ok) return;
  try {
    await api(`/v1/keys/${encodeURIComponent(name)}`, { method: "DELETE" });
    await renderKeys(state);
  } catch (e) { setMsg($("account-keys-msg"), e.message, "err"); }
}

async function changePasswordSubmit(ev) {
  ev.preventDefault();
  const next = $("account-pw-new").value;
  if (next !== $("account-pw-confirm").value) {
    return setMsg($("account-msg"), "The two new passwords do not match.", "err");
  }
  try {
    await changePassword($("account-pw-current").value, next);
    for (const id of ["account-pw-current", "account-pw-new", "account-pw-confirm"]) $(id).value = "";
    setMsg($("account-msg"), "Password changed. Your other sessions were signed out.", "ok");
  } catch (e) { setMsg($("account-msg"), e.message, "err"); }
}

export async function initAccount(state) {
  const who = state.proxied ? "auris dashboard" : state.me.email || state.me.principal;
  $("account-avatar").replaceChildren(avatarEl(who, 56));
  $("account-email").textContent = state.proxied ? "Signed in through auris dashboard" : who;
  $("account-role").hidden = !state.admin;
  // `auris dashboard`'s proxy injects the master bearer server-side so the token never reaches
  // the page — which also means the page never learns which account it is. A key issued or
  // rotated from here would have no owner and, since Task 5b, could not create or modify any
  // voice at all, so it is strictly worse than not offering the control. Same precedent as the
  // password-form guard just below.
  if (state.proxied) {
    $("account-key-form").style.display = "none";
    $("account-key-form-proxied").style.display = "";
  } else {
    $("account-key-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await issueKey(state, $("account-key-name").value.trim());
        $("account-key-form").reset();
      } catch (e) { setMsg($("account-keys-msg"), e.message, "err"); }
    });
  }
  // `auris dashboard`'s proxy forwards only /ready, /health and /v1/* (see client/proxy.py's
  // _PROXIED_PREFIXES), so POST /auth/password never reaches the server in proxy mode: the proxy
  // answers its own HTML 404 and the person saw a bare "Not Found" with no explanation. Show the
  // instruction instead of a control that cannot work.
  if (state.proxied) {
    $("account-pw-card").style.display = "none";
    $("account-pw-proxied").style.display = "";
    $("jump-pw").hidden = true;
  } else {
    $("account-pw-form").addEventListener("submit", changePasswordSubmit);
  }
  $("account-copy").onclick = () =>
    copyWithFeedback($("account-copy"), $("account-issued").textContent, $("account-issued"));
  try { await renderKeys(state); }
  catch (e) { setMsg($("account-keys-msg"), e.message, "err"); }
}
