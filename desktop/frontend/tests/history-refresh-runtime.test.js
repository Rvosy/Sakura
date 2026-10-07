import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import { createHistoryLoadGuard } from "../history/history-load-guard.js";
import { validateHistoryPage } from "../history/history-presentation.js";

test("role refresh queued during pagination starts after the stale page settles", async () => {
  const source = await readFile(new URL("../history/history.js", import.meta.url), "utf8");
  const code = source.slice(0, source.indexOf("\nrefresh.addEventListener("))
    .replace(/^import[\s\S]*?;\r?\n/gm, "");
  const element = () => ({
    dataset: {}, classList: { toggle() {} }, style: { setProperty() {} },
    setAttribute() {}, replaceChildren() {}, addEventListener() {},
    clientWidth: 600, scrollTop: 0, scrollHeight: 100,
  });
  const elements = new Map();
  const calls = [];
  let releaseEarlier;
  let active = "alpha";
  const page = (id) => ({
    schemaVersion: 1,
    coreGenerationId: `gen-${id}`,
    characterId: id,
    totalCount: 51,
    entries: [{
      entryId: `${id}-entry`, turnId: "turn", kind: "human", origin: "chat",
      createdAt: "2026-10-07T12:00:00+08:00", payload: { text: id },
    }],
    beforeCursor: "older",
    hasMore: true,
  });
  const invoke = async (command, payload) => {
    calls.push({ command, payload });
    if (command === "history_bootstrap") {
      return { coreGenerationId: `gen-${active}`, characterId: active };
    }
    if (command === "history_page") {
      if (payload.request.beforeCursor) {
        return await new Promise((resolve) => { releaseEarlier = resolve; });
      }
      return page(active);
    }
  };
  const context = vm.createContext({
    window: { __TAURI__: { core: { invoke } }, addEventListener() {} },
    document: {
      querySelector(selector) {
        if (!elements.has(selector)) elements.set(selector, element());
        return elements.get(selector);
      },
      createDocumentFragment: element,
    },
    installDevtoolsShortcutGuard() {},
    waitForRuntimeFonts: () => Promise.resolve(),
    createErrorDialog: () => ({ show() {}, dispose() {} }),
    createHistoryLoadGuard,
    validateHistoryPage,
    applyTheme() {},
    projectHistoryEntries: () => [],
    ResizeObserver: class { observe() {} disconnect() {} },
    requestAnimationFrame: (callback) => callback(),
  });
  const history = await vm.runInContext(`(async () => { ${code}
    return { loadInitial, loadEarlier, resetForCharacterSwitch,
      state: () => ({loading, initialReloadPending, entries, identity}) };
  })()`, context);
  await history.loadInitial();
  const earlier = history.loadEarlier();
  assert.equal(typeof releaseEarlier, "function");

  active = "beta";
  history.resetForCharacterSwitch();
  await history.loadInitial();
  releaseEarlier(page("alpha"));
  await earlier;
  await new Promise((resolve) => setImmediate(resolve));

  const state = history.state();
  assert.equal(calls.filter((call) => call.command === "history_bootstrap").length, 2);
  assert.equal(state.loading, false);
  assert.equal(state.initialReloadPending, false);
  assert.equal(state.identity.characterId, "beta");
  assert.deepEqual(Array.from(state.entries, (entry) => entry.entryId), ["beta-entry"]);
});
