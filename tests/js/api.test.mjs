import { test } from "node:test";
import assert from "node:assert/strict";
import { api } from "../../auris/server/static/js/api.js";

test("an aborted request rejects as an AbortError, not as 'service unreachable'", async () => {
  // Stop aborts the in-flight synthesis; reporting that as a network failure would flip the
  // sidebar to "Offline" and show "Can't reach the service." for a deliberate cancel.
  const realFetch = globalThis.fetch;
  globalThis.fetch = () => Promise.reject(new DOMException("The operation was aborted.", "AbortError"));
  try {
    await assert.rejects(api("/v1/audio/speech", { method: "POST" }), { name: "AbortError" });
  } finally {
    globalThis.fetch = realFetch;
  }
});
