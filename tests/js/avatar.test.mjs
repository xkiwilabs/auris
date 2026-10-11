import { test } from "node:test";
import assert from "node:assert/strict";
import { initials, gradientFor } from "../../auris/server/static/js/avatar.js";

test("initials: two words give two first letters", () => {
  assert.equal(initials("Mike Richardson"), "MR");
  assert.equal(initials("narrator-uk-f"), "NU");
});

test("initials: one word gives its first two letters; a trailing number gives its last two digits", () => {
  assert.equal(initials("Ayeh"), "AY");
  assert.equal(initials("x"), "X");
  assert.equal(initials("opt001"), "01");
});

test("initials: an email uses the part before the @", () => {
  assert.equal(initials("mary.j.doe@example.com"), "MJ");
});

test("initials: empty or blank names fall back to a question mark", () => {
  assert.equal(initials(""), "?");
  assert.equal(initials("   "), "?");
  assert.equal(initials(null), "?");
});

test("gradientFor is deterministic, case-insensitive and returns two colours", () => {
  assert.deepEqual(gradientFor("Ayeh"), gradientFor("Ayeh"));
  assert.deepEqual(gradientFor("Ayeh"), gradientFor("ayeh"));
  const g = gradientFor("anything");
  assert.equal(g.length, 2);
  for (const c of g) assert.match(c, /^#[0-9a-f]{6}$/);
});

test("gradientFor spreads names across more than one pair", () => {
  const seen = new Set(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"].map((n) => gradientFor(n).join()));
  assert.ok(seen.size > 2);
});
