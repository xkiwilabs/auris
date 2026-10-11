import { test } from "node:test";
import assert from "node:assert/strict";
import { copyText } from "../../auris/server/static/js/clipboard.js";

test("off a secure context, the exact text is copied rather than whatever is on screen", async () => {
  // On the plain-http tailnet page navigator.clipboard is absent. Selecting the transcript's
  // on-screen list would copy its timestamps too, so the fallback copies `text` itself.
  let copied = null;
  const made = [];
  globalThis.window = { isSecureContext: false, getSelection: () => ({ removeAllRanges() {}, addRange() {} }) };
  globalThis.document = {
    body: { append: (el) => made.push(el) },
    createElement: () => ({ style: {}, value: "", setAttribute() {}, select() { this.selected = true; }, remove() { this.removed = true; } }),
    createRange: () => ({ selectNodeContents() {} }),
    execCommand: (cmd) => { if (cmd !== "copy") return false; copied = made.at(-1).selected ? made.at(-1).value : null; return true; },
  };
  try {
    const ok = await copyText("plain words only", { textContent: "0:00 plain words only" });
    assert.equal(ok, true);
    assert.equal(copied, "plain words only");
    assert.equal(made.at(-1).removed, true);
  } finally {
    delete globalThis.window;
    delete globalThis.document;
  }
});
