import assert from "node:assert/strict";
import test from "node:test";
import { createAsrInputTest } from "../settings/asr-input-test.js";

function element() {
  const events = new Map();
  return { hidden: false, value: 0, textContent: "", disabled: false,
    addEventListener: (name, handler) => events.set(name, handler),
    fire: async (name) => events.get(name)?.(),
  };
}
function fixture() {
  const controls = Object.fromEntries(["asrTestStart", "asrTestCancel", "asrTestResult", "asrTestLevel"].map((id) => [id, element()]));
  const events = new Map(), keys = new Map(), calls = [];
  let state = "recording";
  const testInput = createAsrInputTest({
    document: { getElementById: (id) => controls[id], addEventListener: (name, handler) => keys.set(name, handler), removeEventListener: (name) => keys.delete(name) },
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "asr_prepare") return { recordingId: args.payload.recordingId, state: "ready" };
      if (name === "asr_capture_start") return { state: "recording" };
      if (name === "asr_capture_stop") { state = "succeeded"; return { state: "recognizing" }; }
      if (name === "asr_poll") return { state, text: state === "succeeded" ? "真实引擎返回的测试文字" : undefined };
      return { state: "cancelled" };
    },
    listen: async (name, handler) => { events.set(name, handler); return () => events.delete(name); },
    readProvider: () => "thirdparty.asr", readDevice: () => "device:selected",
  });
  return { controls, calls, events, keys, testInput };
}

test("plugin input test uses test-only provider/device overrides and writes only its local result", async () => {
  const f = fixture();
  await f.testInput.activate();
  const payload = f.calls.find(([name]) => name === "asr_prepare")[1].payload;
  assert.equal(payload.purpose, "test");
  assert.equal(payload.providerId, "thirdparty.asr");
  assert.equal(payload.inputDeviceId, "device:selected");
  f.events.get("sakura://asr-level")({ payload: { recordingId: payload.recordingId, sequence: 1, level: 0.7 } });
  assert.equal(f.controls.asrTestLevel.value, 0.7);
  await f.testInput.activate();
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.equal(f.controls.asrTestResult.textContent, "真实引擎返回的测试文字");
  assert.equal(f.calls.some(([name]) => name.includes("save") || name.includes("chat") || name.includes("send")), false);
  f.testInput.dispose();
});

test("Esc cancels microphone test before the surrounding plugin dialog closes", async () => {
  const f = fixture();
  await f.testInput.activate();
  let prevented = false, stopped = false;
  f.keys.get("keydown")({ key: "Escape", preventDefault: () => { prevented = true; }, stopImmediatePropagation: () => { stopped = true; } });
  await Promise.resolve();
  assert.equal(prevented, true); assert.equal(stopped, true);
  assert.equal(f.testInput.active(), false);
  assert.equal(f.controls.asrTestLevel.hidden, true);
  assert.ok(f.calls.some(([name]) => name === "asr_cancel"));
  f.testInput.dispose();
});
