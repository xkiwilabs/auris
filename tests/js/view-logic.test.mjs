import { test } from "node:test";
import assert from "node:assert/strict";
import {
  splitVoices, ownerLabel, charState, userStatus, accepts, mmss, fmtBytes, fmtDate, relTime,
} from "../../auris/server/static/js/view-logic.js";

const voices = [
  { name: "Ayeh", kind: "cloned", owner: "a@example.com" },
  { name: "narrator-uk-f", kind: "preset", owner: null },
  { name: "Gaurav", kind: "cloned", owner: "b@example.com" },
];

test("splitVoices separates cloned voices from presets", () => {
  const { cloned, presets } = splitVoices(voices);
  assert.deepEqual(cloned.map((v) => v.name), ["Ayeh", "Gaurav"]);
  assert.deepEqual(presets.map((v) => v.name), ["narrator-uk-f"]);
});

test("splitVoices filters by name or owner, case-insensitively, ignoring surrounding space", () => {
  assert.deepEqual(splitVoices(voices, "  ayE ").cloned.map((v) => v.name), ["Ayeh"]);
  assert.deepEqual(splitVoices(voices, "b@EXAMPLE").cloned.map((v) => v.name), ["Gaurav"]);
  assert.deepEqual(splitVoices(voices, "uk").presets.map((v) => v.name), ["narrator-uk-f"]);
  assert.deepEqual(splitVoices(voices, "zzz"), { cloned: [], presets: [] });
});

test("ownerLabel says You, the owner's email, or No owner", () => {
  assert.equal(ownerLabel(voices[0], { email: "a@example.com" }), "You");
  assert.equal(ownerLabel(voices[0], { email: "c@example.com" }), "a@example.com");
  assert.equal(ownerLabel(voices[0], null), "a@example.com");
  assert.equal(ownerLabel({ owner: null }, { email: "a@example.com" }), "No owner");
});

test("charState warns at 90% and flags anything over the limit", () => {
  assert.equal(charState(0, 1000), "ok");
  assert.equal(charState(899, 1000), "ok");
  assert.equal(charState(900, 1000), "near");
  assert.equal(charState(1000, 1000), "near");
  assert.equal(charState(1001, 1000), "over");
  assert.equal(charState(50000, 0), "ok"); // unknown limit: never block
});

test("userStatus merges state and the must-set-password flag", () => {
  assert.deepEqual(userStatus({ disabled: false, must_change_password: true }),
    { label: "Active", tone: "ok", mustChange: true });
  assert.deepEqual(userStatus({ disabled: true, must_change_password: false }),
    { label: "Disabled", tone: "", mustChange: false });
});

test("accepts honours MIME wildcards, exact types and extensions", () => {
  const f = (name, type) => ({ name, type });
  assert.equal(accepts(f("a.wav", "audio/wav"), "audio/*"), true);
  assert.equal(accepts(f("a.txt", "text/plain"), "audio/*"), false);
  assert.equal(accepts(f("a.opus", ""), "audio/*"), true); // unknown type: let the server decide
  assert.equal(accepts(f("a.m4a", "audio/x-m4a"), "audio/*,video/*"), true);
  assert.equal(accepts(f("a.MP3", "application/octet-stream"), ".mp3"), true);
  assert.equal(accepts(f("a.txt", "text/plain"), ""), true); // no accept attribute: anything
});

test("mmss formats seconds as m:ss and copes with junk", () => {
  assert.equal(mmss(0), "0:00");
  assert.equal(mmss(6.4), "0:06");
  assert.equal(mmss(65), "1:05");
  assert.equal(mmss(3600), "60:00");
  assert.equal(mmss(NaN), "0:00");
  assert.equal(mmss(Infinity), "0:00");
  assert.equal(mmss(-3), "0:00");
});

test("fmtBytes picks a sensible unit", () => {
  assert.equal(fmtBytes(512), "512 B");
  assert.equal(fmtBytes(1536), "1.5 KB");
  assert.equal(fmtBytes(3.4 * 1024 * 1024), "3.4 MB");
});

test("fmtDate renders a local calendar date, or a dash", () => {
  assert.equal(fmtDate(new Date(2026, 9, 10, 12).toISOString()), "10 Oct 2026");
  assert.equal(fmtDate(null), "—");
  assert.equal(fmtDate("not a date"), "—");
});

test("relTime is coarse and falls back to a date after a week", () => {
  const now = new Date(2026, 9, 10, 12).getTime();
  const ago = (s) => new Date(now - s * 1000).toISOString();
  assert.equal(relTime(null, now), "never");
  assert.equal(relTime(ago(20), now), "just now");
  assert.equal(relTime(ago(5 * 60), now), "5 min ago");
  assert.equal(relTime(ago(3 * 3600), now), "3 h ago");
  assert.equal(relTime(ago(2 * 86400), now), "2 d ago");
  assert.equal(relTime(ago(30 * 86400), now), fmtDate(ago(30 * 86400)));
  assert.equal(relTime(ago(-60), now), "just now"); // clock skew: never "in the future"
});
