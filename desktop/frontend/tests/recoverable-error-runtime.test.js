import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { errorText } from "../core/error-display.js";

const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
const callback = source.slice(source.indexOf("function showRecoverableError("),
  source.indexOf("\nfunction isBubbleScrollbarHit("));

test("pet errors use the independent themed window with complete sanitized details", async () => {
  const calls = [];
  const context = vm.createContext({
    errorText,
    invoke: async (...args) => { calls.push(args); },
    runtimeDiagnostics: { reportError: () => assert.fail("unexpected window failure") },
  });
  vm.runInContext(callback, context);
  context.showRecoverableError({ code: "TTS_SERVICE_UNAVAILABLE", message: "request failed",
    diagnostic: "api_key=private-value\nTraceback\n  File voice.py, line 20" });
  await Promise.resolve();
  assert.equal(calls[0][0], "show_error_dialog");
  assert.ok(calls[0][1].payload.details.includes("voice.py"));
  assert.ok(calls[0][1].payload.details.includes("TTS_SERVICE_UNAVAILABLE"));
  assert.ok(!calls[0][1].payload.details.includes("private-value"));
});

test("failure to open the error window is logged without recursive dialogs", async () => {
  const failure = new Error("window unavailable");
  const recorded = [];
  let calls = 0;
  const context = vm.createContext({ errorText,
    invoke: async () => { calls++; throw failure; },
    runtimeDiagnostics: { reportError: (...args) => recorded.push(args) },
  });
  vm.runInContext(callback, context);
  context.showRecoverableError("original failure");
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(recorded[0][0], failure);
});
