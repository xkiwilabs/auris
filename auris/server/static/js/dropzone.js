// auris/server/static/js/dropzone.js — drag-and-drop plus click-to-browse over a hidden file
// input. The input keeps its id and stays the source of truth, so `input.files[0]` is what a
// form reads, and the runbook can still inject a file into it directly.
"use strict";
import { accepts } from "./view-logic.js";

export function attachDropzone(zone, input, onFile, onReject = () => {}) {
  const pick = () => input.click();
  zone.tabIndex = 0;
  zone.setAttribute("role", "button");
  zone.addEventListener("click", (ev) => { if (ev.target !== input) pick(); });
  zone.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); }
  });
  zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (ev) => {
    ev.preventDefault();
    zone.classList.remove("over");
    const file = ev.dataTransfer && ev.dataTransfer.files[0];
    if (!file) return;
    if (!accepts(file, input.accept)) { onReject(file); return; } // keep whatever was chosen before
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  input.addEventListener("change", () => {
    const file = input.files[0] || null;
    if (file && !accepts(file, input.accept)) { input.value = ""; onReject(file); onFile(null); return; }
    onFile(file);
  });
}
