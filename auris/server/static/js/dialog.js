// auris/server/static/js/dialog.js — in-page confirm/prompt on one native <dialog>.
//
// Replaces window.confirm/prompt, which block the page (and freeze browser automation). Esc,
// Cancel, and opening a second dialog over the first all resolve as "no" — a destructive action
// only ever happens on an explicit click of the confirm button.
"use strict";

const byId = (id) => document.getElementById(id);
let resolver = null;
let wired = false;

function settle(value) {
  const resolve = resolver;
  resolver = null;
  const d = byId("dialog");
  if (d.open) d.close();
  if (resolve) resolve(value);
}

function wire() {
  if (wired) return;
  wired = true;
  byId("dialog-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    settle(byId("dialog-field").hidden ? "" : byId("dialog-input").value);
  });
  byId("dialog-cancel").addEventListener("click", () => settle(null));
  byId("dialog").addEventListener("cancel", (ev) => { ev.preventDefault(); settle(null); });
}

function open({ title, body = "", confirmLabel = "OK", danger = false, input = null }) {
  wire();
  settle(null); // never stack: a dialog already open is cancelled, not confirmed
  byId("dialog-title").textContent = title;
  byId("dialog-body").textContent = body;
  byId("dialog-body").hidden = !body;
  const ok = byId("dialog-ok");
  ok.textContent = confirmLabel;
  ok.classList.toggle("danger", danger);
  byId("dialog-field").hidden = !input;
  if (input) {
    byId("dialog-label").textContent = input.label;
    byId("dialog-input").value = input.value || "";
  }
  return new Promise((resolve) => {
    resolver = resolve;
    byId("dialog").showModal();
    if (input) { byId("dialog-input").focus(); byId("dialog-input").select(); }
    else (danger ? byId("dialog-cancel") : ok).focus();
  });
}

export function confirmDialog({ title, body = "", confirmLabel = "OK", danger = false }) {
  return open({ title, body, confirmLabel, danger }).then((v) => v !== null);
}

export function promptDialog({ title, label, value = "", confirmLabel = "Save" }) {
  return open({ title, confirmLabel, input: { label, value } })
    .then((v) => (v === null ? null : v.trim() || null));
}
