import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { CloseDecision, executeSettingsClose } from "../settings/close-flow.js";

const settingsEntry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

function fixture({ dirty, decision }) {
  const calls = [];
  return {
    calls,
    options: {
      dirty,
      choose: async () => {
        calls.push("choose");
        return decision;
      },
      save: async () => calls.push("save"),
      discard: async () => calls.push("discard"),
      close: async () => calls.push("close"),
      stay: async () => calls.push("stay"),
    },
  };
}

test("clean settings close immediately without a prompt or redundant preview cancellation", async () => {
  const { calls, options } = fixture({ dirty: false, decision: CloseDecision.STAY });
  assert.equal(await executeSettingsClose(options), CloseDecision.CLOSE);
  assert.deepEqual(calls, ["close"]);
});

test("unsaved settings can save then close exactly once", async () => {
  const { calls, options } = fixture({ dirty: true, decision: CloseDecision.SAVE });
  assert.equal(await executeSettingsClose(options), CloseDecision.SAVE);
  assert.deepEqual(calls, ["choose", "save", "close"]);
});

test("unsaved settings can discard then close or stay open", async () => {
  const discarded = fixture({ dirty: true, decision: CloseDecision.DISCARD });
  assert.equal(await executeSettingsClose(discarded.options), CloseDecision.DISCARD);
  assert.deepEqual(discarded.calls, ["choose", "discard", "close"]);

  const stayed = fixture({ dirty: true, decision: CloseDecision.STAY });
  assert.equal(await executeSettingsClose(stayed.options), CloseDecision.STAY);
  assert.deepEqual(stayed.calls, ["choose", "stay"]);
});

test("save failure preserves the open window and never falls through to close", async () => {
  const { calls, options } = fixture({ dirty: true, decision: CloseDecision.SAVE });
  options.save = async () => {
    calls.push("save");
    throw new Error("save failed");
  };
  await assert.rejects(executeSettingsClose(options), /save failed/);
  assert.deepEqual(calls, ["choose", "save"]);
});

test("the title-bar close path offers save, discard, and return through the same cancel flow", () => {
  assert.match(settingsEntry, /设置有未保存的改动，是否保存后关闭？/);
  for (const label of ["保存", "不保存", "返回"]) {
    assert.match(settingsEntry, new RegExp(`textContent = "${label}"`));
  }
  assert.match(settingsEntry, /settings-close-requested", requestCancelClose/);
  assert.match(settingsEntry, /fields\.cancelButton[\s\S]*?requestCancelClose/);
  const closeWindow = settingsEntry.slice(
    settingsEntry.indexOf("async function closeSettingsWindow()"),
    settingsEntry.indexOf("let closeRequestInFlight"),
  );
  assert.match(closeWindow, /resolve_settings_close/);
  assert.doesNotMatch(closeWindow, /cancelPreview/);
});
