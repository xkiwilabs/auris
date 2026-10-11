import { test } from "node:test";
import assert from "node:assert/strict";
import { peaks, levelOf } from "../../auris/server/static/js/waveform.js";

test("peaks returns one value per bucket, normalised to the loudest", () => {
  assert.deepEqual(peaks(Float32Array.from([0, 0.5, -1, 0.25]), 2), [0.5, 1]);
});

test("peaks values all lie in 0..1", () => {
  const s = Float32Array.from({ length: 1000 }, (_, i) => Math.sin(i / 7) * 0.3);
  const p = peaks(s, 40);
  assert.equal(p.length, 40);
  for (const v of p) assert.ok(v >= 0 && v <= 1);
  assert.equal(Math.max(...p), 1);
});

test("peaks of silence is all zeros, not NaN", () => {
  assert.deepEqual(peaks(new Float32Array(100), 4), [0, 0, 0, 0]);
});

test("peaks copes with fewer samples than buckets", () => {
  const p = peaks(Float32Array.from([0.5, -0.25]), 4);
  assert.equal(p.length, 4);
  for (const v of p) assert.ok(v >= 0 && v <= 1);
});

test("peaks of nothing is zeros; zero buckets is empty", () => {
  assert.deepEqual(peaks(new Float32Array(0), 3), [0, 0, 0]);
  assert.deepEqual(peaks(Float32Array.from([1]), 0), []);
});

test("levelOf is 0 for silence and saturates at 1", () => {
  assert.equal(levelOf(new Float32Array(64)), 0);
  assert.equal(levelOf(new Float32Array(64).fill(1)), 1);
  assert.ok(Math.abs(levelOf(new Float32Array(64).fill(0.1)) - 0.4) < 1e-6);
  assert.equal(levelOf(new Float32Array(0)), 0);
});
