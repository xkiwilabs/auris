import { test } from "node:test";
import assert from "node:assert/strict";
import { clock, toSrt, toVtt } from "../../auris/server/static/js/transcript-format.js";

const segs = [
  { id: 0, start: 0.0, end: 1.5, text: "Hello world." },
  { id: 1, start: 1.5, end: 3725.25, text: "Second line." },
];

test("clock formats hours without wrapping and pads milliseconds", () => {
  assert.equal(clock(0, ","), "00:00:00,000");
  assert.equal(clock(1.5, ","), "00:00:01,500");
  assert.equal(clock(3725.25, "."), "01:02:05.250");
  assert.equal(clock(-1, ","), "00:00:00,000");
});

test("srt matches the server's layout", () => {
  const out = toSrt(segs);
  assert.ok(out.startsWith("1\n00:00:00,000 --> 00:00:01,500\nHello world.\n"));
  assert.ok(out.includes("2\n00:00:01,500 --> 01:02:05,250\nSecond line."));
});

test("vtt has the header and dot milliseconds", () => {
  const out = toVtt(segs);
  assert.ok(out.startsWith("WEBVTT\n\n"));
  assert.ok(out.includes("00:00:00.000 --> 00:00:01.500"));
});
