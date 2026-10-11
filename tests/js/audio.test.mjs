import { test } from "node:test";
import assert from "node:assert/strict";
import { encodeWav, concatF32, playPcmStream } from "../../auris/server/static/js/audio.js";

const ascii = (view, at, n) => String.fromCharCode(...new Uint8Array(view.buffer, at, n));

test("encodeWav writes a 16-bit mono PCM header", () => {
  const buf = encodeWav(new Float32Array(10), 24000);
  const v = new DataView(buf);
  assert.equal(buf.byteLength, 44 + 20);
  assert.equal(ascii(v, 0, 4), "RIFF");
  assert.equal(v.getUint32(4, true), 36 + 20);
  assert.equal(ascii(v, 8, 4), "WAVE");
  assert.equal(ascii(v, 12, 4), "fmt ");
  assert.equal(v.getUint16(20, true), 1);      // PCM
  assert.equal(v.getUint16(22, true), 1);      // mono
  assert.equal(v.getUint32(24, true), 24000);  // sample rate
  assert.equal(v.getUint32(28, true), 48000);  // byte rate
  assert.equal(v.getUint16(34, true), 16);     // bits per sample
  assert.equal(ascii(v, 36, 4), "data");
  assert.equal(v.getUint32(40, true), 20);
});

test("encodeWav scales and clips samples", () => {
  const v = new DataView(encodeWav(Float32Array.from([0, 1, -1, 0.5, 2, -2]), 8000));
  const at = (i) => v.getInt16(44 + i * 2, true);
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(at), [0, 32767, -32768, 16383, 32767, -32768]);
});

test("concatF32 joins chunks in order", () => {
  const out = concatF32([Float32Array.from([1, 2]), new Float32Array(0), Float32Array.from([3])]);
  assert.deepEqual(Array.from(out), [1, 2, 3]);
  assert.equal(concatF32([]).length, 0);
});

test("stopping a stream twice never leaves a rejected close() unhandled", async () => {
  // A stream that ended on its own has already closed its AudioContext; the next Speak's
  // stopAll() stops it again, and close() on a closed context rejects asynchronously.
  class FakeContext {
    constructor() { this.currentTime = 0; this.closed = false; }
    close() {
      if (this.closed) return Promise.reject(new Error("Cannot close a closed AudioContext."));
      this.closed = true;
      return Promise.resolve();
    }
  }
  globalThis.window = { AudioContext: FakeContext };
  const response = {
    headers: { get: () => "24000" },
    body: { getReader: () => ({ read: () => new Promise(() => {}), cancel() {} }) },
  };
  const unhandled = [];
  const onRejection = (e) => unhandled.push(e);
  process.on("unhandledRejection", onRejection);
  try {
    const stream = playPcmStream(response);
    stream.stop();
    stream.stop();
    await stream.ended;
    await new Promise((r) => setTimeout(r, 20));
  } finally {
    process.off("unhandledRejection", onRejection);
    delete globalThis.window;
  }
  assert.deepEqual(unhandled.map(String), []);
});
