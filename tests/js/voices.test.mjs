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

test("proxy mode shows the control and lets the server be the real gate", () => {
  // In proxy mode state.me has no email (/auth/me is not forwarded), so the page cannot know
  // whether the viewer owns this voice — it shows the control and the server answers 403 if not.
  const voice = { owner: "a@example.com" };
  const me = { principal: "proxy" };
  assert.equal(mayChangeVisibility(voice, me, false), true);
});
