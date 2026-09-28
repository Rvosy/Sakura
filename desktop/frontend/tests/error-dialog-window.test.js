import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../error-dialog/error-dialog.js", import.meta.url), "utf8"))
  .replace(/^import .*;\r?\n/gm, "");
const settle = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

async function fixture() {
  const shown = [], calls = [], listeners = new Map();
  const snapshot = { title: "朗读失败", message: "", details: "TTS_SYNTHESIS_FAILED", themeTokens: {} };
  let modal, bootstrap = () => Promise.resolve(snapshot), close = () => Promise.resolve();
  const invoke = command => {
    calls.push(command);
    if (command === "error_dialog_bootstrap") return bootstrap();
    if (command === "close_error_dialog") return close();
    return Promise.resolve();
  };
  const context = {
    document: {},
    window: {
      __TAURI__: { core: { invoke }, event: { listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); } } },
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
    createErrorDialog: options => { modal = options; return { show: value => shown.push(value), dispose() {} }; },
    applyTheme() {},
    waitForRuntimeFonts: () => Promise.resolve(),
  };
  await vm.runInNewContext(`(async () => {\n${source}\n})()`, context);
  return {
    shown, calls, snapshot,
    setBootstrap: callback => { bootstrap = callback; },
    setClose: callback => { close = callback; },
    refresh: () => listeners.get("sakura://error-dialog-updated")(),
    close: () => modal.onClose(),
  };
}

test("closing the error window invalidates pending refresh and ignores updates until destroyed", async () => {
  const ui = await fixture();
  const refresh = deferred(), closing = deferred();
  ui.setBootstrap(() => refresh.promise);
  ui.setClose(() => closing.promise);
  ui.refresh();
  ui.close();
  ui.refresh();
  refresh.resolve({ ...ui.snapshot, details: "late failure" });
  await settle();
  assert.equal(ui.shown.length, 1);
  assert.equal(ui.calls.filter(command => command === "reveal_error_dialog").length, 1);
  assert.equal(ui.calls.filter(command => command === "error_dialog_bootstrap").length, 2);
  closing.resolve();
  await settle();
});

test("failed native close restores a usable dialog and permits another close attempt", async () => {
  const ui = await fixture();
  const closing = deferred();
  closing.promise.catch(() => {});
  ui.setClose(() => closing.promise);
  ui.close();
  const failure = new Error("fixture native close failure");
  closing.reject(failure);
  await settle();
  assert.equal(ui.shown.length, 2);
  assert.equal(ui.shown[1].error, failure);
  ui.setClose(() => Promise.resolve());
  await ui.close();
  assert.equal(ui.calls.filter(command => command === "close_error_dialog").length, 2);
});

test("a refresh rejected after closing does not reopen the dialog or reject unhandled", async () => {
  const ui = await fixture();
  const refresh = deferred();
  ui.setBootstrap(() => refresh.promise);
  ui.refresh();
  await ui.close();
  refresh.reject(new Error("fixture window destroyed"));
  await settle();
  assert.equal(ui.shown.length, 1);
  assert.equal(ui.calls.filter(command => command === "reveal_error_dialog").length, 1);
});
