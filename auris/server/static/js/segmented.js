// auris/server/static/js/segmented.js — a row of buttons acting as one choice (aria-pressed).
"use strict";

export function bindSegmented(root, onChange = () => {}) {
  const buttons = [...root.querySelectorAll("button[data-value]")];
  const set = (value) => {
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.value === value));
  };
  for (const b of buttons) {
    b.addEventListener("click", () => { set(b.dataset.value); onChange(b.dataset.value); });
  }
  return { set };
}

// A segmented control standing in for a hidden <select>: the select stays the source of truth
// (prefs.js and the panel read `.value`), so a click writes it and fires `change`. Setting the
// select's value in code fires nothing — call the returned sync() afterwards.
export function mirrorSelect(root, select) {
  const seg = bindSegmented(root, (value) => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const sync = () => seg.set(select.value);
  select.addEventListener("change", sync);
  sync();
  return sync;
}
