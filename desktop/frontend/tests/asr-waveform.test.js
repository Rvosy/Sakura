import assert from "node:assert/strict";
import test from "node:test";
import { createAsrWaveform } from "../audio/asr-waveform.js";

test("rendered speech has distinct heights, responds to pauses and stops with capture", () => {
  let frame, strokes = [], startY;
  const context = {
    setTransform() {}, clearRect() { strokes = []; }, beginPath() {}, stroke() {},
    moveTo(_x, y) { startY = y; }, lineTo(_x, y) { strokes.push(y - startY); },
  };
  const canvas = { clientWidth: 300, clientHeight: 36, getContext: () => context };
  const waveform = createAsrWaveform({ canvas, window: {
    devicePixelRatio: 2,
    getComputedStyle: () => ({ color: "blue" }),
    requestAnimationFrame: callback => { frame = callback; return 1; },
    cancelAnimationFrame: () => { frame = null; },
  } });
  waveform.start();
  frame(0);
  const baseline = strokes[0];
  let now = 0;
  const sample = level => {
    waveform.push(level);
    frame(now += 80);
    return strokes[0];
  };
  const quiet = sample(.25), normal = sample(.5), loud = sample(.7);
  assert.ok(quiet > baseline);
  assert.ok(normal > quiet * 2, "quiet speech and normal speech must be visually distinct");
  assert.ok(loud > normal * 1.5, "louder syllables must retain headroom");
  assert.ok(loud > canvas.clientHeight * .7, "loud speech should use the available height");
  assert.ok(loud < canvas.clientHeight);
  assert.equal(sample(0), baseline, "pause must not inherit a long synthetic decay");
  assert.equal(sample(Number.NaN), baseline);
  waveform.stop();
  assert.equal(frame, null);
  waveform.push(1);
  waveform.start();
  frame(0);
  assert.equal(strokes[0], baseline, "a new recording must not inherit old audio");
  waveform.stop();
});
