import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PASSAGES, nextPassage, countdownStep, COUNTDOWN_S,
} from "../../auris/server/static/js/passages.js";

test("there are several passages, each a few complete sentences", () => {
  assert.ok(PASSAGES.length >= 4);
  for (const p of PASSAGES) {
    assert.ok(p.title && p.text, "every passage has a title and text");
    // Full stops between sentences give the server's cut a pause to land in.
    assert.ok(p.text.split(/[.!?](\s|$)/).filter((s) => s && s.trim()).length >= 2, p.title);
    assert.match(p.text, /[.!?]$/, p.title + " ends a sentence");
    // ~8–10 s read aloud at a relaxed pace is roughly 20–40 words.
    const words = p.text.split(/\s+/).length;
    assert.ok(words >= 18 && words <= 45, p.title + " has " + words + " words");
  }
});

test("the rainbow passage leads, matching the CLI's auris record", () => {
  assert.match(PASSAGES[0].text, /^When the sunlight strikes raindrops in the air/);
});

test("Show another cycles through every passage and wraps round", () => {
  const seen = [];
  let i = 0;
  for (let n = 0; n < PASSAGES.length; n++) { seen.push(i); i = nextPassage(i); }
  assert.deepEqual([...new Set(seen)].sort(), [...PASSAGES.keys()]);
  assert.equal(i, 0);
  assert.equal(nextPassage(-1), 0); // an unknown position starts from the top
});

test("the countdown runs Get ready, 3, 2, 1, then cues speech, then gets out of the way", () => {
  const labels = [];
  for (let t = 0; t < COUNTDOWN_S + 3; t += 0.1) {
    const s = countdownStep(t);
    if (labels[labels.length - 1] !== s.label) labels.push(s.label);
  }
  assert.deepEqual(labels, ["Get ready…", "3", "2", "1", "Speak now", null]);
  assert.equal(countdownStep(0).speaking, false);
  assert.equal(countdownStep(COUNTDOWN_S - 0.01).speaking, false);
  assert.equal(countdownStep(COUNTDOWN_S).speaking, true);
  // Seconds of speaking time, which the 3-second minimum is checked against.
  assert.equal(countdownStep(COUNTDOWN_S + 4).spoken, 4);
  assert.equal(countdownStep(1).spoken, 0);
});
