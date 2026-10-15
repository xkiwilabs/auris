import { test } from "node:test";
import assert from "node:assert/strict";
import { mayChangeVisibility, mayPlayClips, mayEditBlend } from "../../auris/server/static/js/voice-visibility.js";

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

test("only the owner and admins may play a voice's recordings", () => {
  const me = { email: "a@example.com" };
  assert.equal(mayPlayClips({ owner: "a@example.com" }, me, false), true);
  assert.equal(mayPlayClips({ owner: "b@example.com" }, me, false), false); // shared or not
  assert.equal(mayPlayClips({ owner: null }, me, false), false); // unowned: admin-only
  assert.equal(mayPlayClips({ owner: "b@example.com" }, me, true), true);
  assert.equal(mayPlayClips({ owner: null }, me, true), true);
  assert.equal(mayPlayClips({ owner: "a@example.com" }, null, false), false);
});

test("a blend is re-mixable by its owner and admins only; an unowned one by admins", () => {
  const me = { email: "a@example.com" };
  assert.equal(mayEditBlend({ owner: "a@example.com" }, me, false), true);
  assert.equal(mayEditBlend({ owner: "b@example.com", visibility: "shared" }, me, false), false);
  assert.equal(mayEditBlend({ owner: "b@example.com" }, me, true), true);
  assert.equal(mayEditBlend({ owner: null }, me, false), false);
  assert.equal(mayEditBlend({ owner: null }, me, true), true);
});
