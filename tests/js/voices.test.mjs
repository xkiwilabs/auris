import { test } from "node:test";
import assert from "node:assert/strict";
import { mayChangeVisibility } from "../../auris/server/static/js/voice-visibility.js";

test("the owner may change their own voice's visibility", () => {
  const voice = { owner: "a@example.com" };
  const me = { email: "a@example.com" };
  assert.equal(mayChangeVisibility(voice, me, false), true);
});

test("an admin may change any owned voice's visibility", () => {
  const voice = { owner: "a@example.com" };
  const me = { email: "b@example.com" };
  assert.equal(mayChangeVisibility(voice, me, true), true);
});

test("another account's voice is not writable by a non-admin", () => {
  const voice = { owner: "a@example.com" };
  const me = { email: "b@example.com" };
  assert.equal(mayChangeVisibility(voice, me, false), false);
});

test("an unowned voice has no toggle, even for an admin", () => {
  const voice = { owner: null };
  const me = { email: "b@example.com" };
  assert.equal(mayChangeVisibility(voice, me, true), false);
});

test("a viewer the page can't identify gets no toggle", () => {
  // Every signed-in page knows its email now (the localhost proxy forwards /auth/me too), so a
  // missing one means no session at all — not an excuse to offer the control.
  const voice = { owner: "a@example.com" };
  assert.equal(mayChangeVisibility(voice, { principal: "x" }, false), false);
  assert.equal(mayChangeVisibility(voice, null, false), false);
});
