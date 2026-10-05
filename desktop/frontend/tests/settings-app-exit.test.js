import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { executeSettingsClose } from "../settings/close-flow.js";

const source = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
const handler = source.slice(source.indexOf("let exitRequestInFlight = false;"), source.indexOf("\nfunction markInvalid("));

for (const decision of ["discard", "save", "stay"]) {
  test(`app exit ${decision} remains usable with an unavailable Core`, async () => {
    const calls = [], errors = [];
    let closing = false;
    const unavailable = async () => { throw new Error("SETTINGS_CORE_UNAVAILABLE"); };
    const context = vm.createContext({
      settingsCloseFlowPromise: Promise.resolve({ executeSettingsClose }),
      computeDirty: () => true,
      chooseUnsavedClose: async () => decision,
      saveRuntimeSettings: unavailable,
      runtimeAppearanceController: { cancelPreview: unavailable },
      runtimeCharacterFeature: { discard: unavailable, waitForPreview: unavailable },
      setSubmissionBusy: () => {}, setError: error => { if (error) errors.push(error); },
      notify: () => {}, bypassCloseGuard: false, settingsWindowClosing: false,
      beginSettingsWindowClose: () => { closing = true; },
      invoke: async (command, args) => { calls.push([command, args]); },
    });
    vm.runInContext(handler, context);
    await context.requestAppExitClose({ payload: 7 });
    assert.equal(calls.at(-1)[0], "resolve_settings_exit");
    assert.equal(calls.at(-1)[1].revision, 7);
    assert.equal(calls.at(-1)[1].discard, decision === "discard");
    assert.equal(closing, decision === "discard");
    assert.equal(errors.length, decision === "save" ? 1 : 0);
  });
}
