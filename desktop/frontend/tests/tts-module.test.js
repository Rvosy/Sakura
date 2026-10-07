import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createTtsControllerHost } from "../audio/tts-controller.js";

test("installed speech module drives playback through the public shell adapter", async () => {
  const source = readFileSync(new URL("../../../plugins/builtin/sakura_tts_hub/frontend/playback.mjs", import.meta.url), "utf8");
  const calls = [], listeners = new Map();
  const controller = createTtsControllerHost({
    invoke: async (name, payload) => {
      calls.push([name, payload]);
      if (name === "plugin_frontend_module") return { pluginId: "sakura.tts", source };
    },
    listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); },
  }, source => import(`data:text/javascript,${encodeURIComponent(source)}`));
  await controller.rebind("generation-a");
  controller.beginReply("reply", []);
  assert.equal(calls[0][1].serviceKey, "sakura.tts");
  assert.ok(calls.some(([name]) => name === "tts_begin_reply"));
  controller.dispose();
  assert.equal(listeners.size, 0);
});

test("unavailable plugin preserves diagnostics and lets text presentation continue", async () => {
  const diagnostics = [], starts = [];
  let prepared = false;
  const controller = createTtsControllerHost({
    invoke: async () => { throw new Error("PLUGIN_FRONTEND_UNAVAILABLE"); },
    onDiagnostic: error => diagnostics.push(error),
  });
  await controller.rebind("generation-a");
  await controller.beforeSegment({}, 0, { prepareVisual: () => { prepared = true; }, onStarted: event => starts.push(event) });
  assert.equal(prepared, true);
  assert.deepEqual(starts, [{ state: "skipped" }]);
  assert.match(diagnostics[0], /PLUGIN_FRONTEND_UNAVAILABLE/);
  assert.equal(await controller.afterSegment(), false);
});

for (const invalidation of ["cancel", "dispose", "beginReply"]) {
  test(`missing speech module cannot reveal a late visual after ${invalidation}`, async () => {
    let resume;
    const prepared = new Promise(resolve => { resume = resolve; });
    const starts = [];
    const controller = createTtsControllerHost({
      invoke: async () => { throw new Error("PLUGIN_FRONTEND_UNAVAILABLE"); },
    });
    controller.beginReply("old", []);
    const pending = controller.beforeSegment({}, 0, {
      prepareVisual: () => prepared, onStarted: event => starts.push(event),
    });
    controller[invalidation]("new", []);
    resume();
    await pending;
    assert.deepEqual(starts, []);
  });
}

test("existing generation rebind recovers a startup failure and replaces the module after Core restart", async () => {
  const source = readFileSync(new URL("../../../plugins/builtin/sakura_tts_hub/frontend/playback.mjs", import.meta.url), "utf8");
  const calls = [], listeners = new Set(), diagnostics = [];
  let available = false;
  const controller = createTtsControllerHost({
    invoke: async (name, payload) => {
      calls.push([name, payload]);
      if (name === "plugin_frontend_module") {
        if (!available) throw new Error("STALE_GENERATION");
        return { source };
      }
    },
    listen: async (name, callback) => {
      const entry = { name, callback };
      listeners.add(entry);
      return () => listeners.delete(entry);
    },
    onDiagnostic: message => diagnostics.push(message),
  }, source => import(`data:text/javascript,${encodeURIComponent(source)}`));
  assert.equal(calls.length, 0, "window construction does not request a module before Core is bound");
  assert.equal(await controller.rebind("generation-a"), false);
  available = true;
  assert.equal(await controller.rebind("generation-a"), true);
  assert.equal(listeners.size, 2);
  controller.beginReply("before-restart", []);
  assert.equal(await controller.rebind("generation-a"), true);
  assert.equal(calls.filter(([name]) => name === "plugin_frontend_module").length, 2);

  assert.equal(await controller.rebind("generation-b"), true);
  assert.equal(listeners.size, 2, "replacement releases the earlier native listeners");
  controller.beginReply("after-restart", []);
  assert.deepEqual(calls.filter(([name]) => name === "tts_begin_reply").map(([, args]) => args.payload.operationId),
    ["before-restart", "after-restart"]);
  assert.match(diagnostics[0], /STALE_GENERATION/);
  controller.dispose();
  assert.equal(listeners.size, 0);
});

for (const invalidation of ["dispose", "rebind"]) {
  test(`late module startup releases listeners and cannot publish after ${invalidation}`, async () => {
    const source = readFileSync(new URL("../../../plugins/builtin/sakura_tts_hub/frontend/playback.mjs", import.meta.url), "utf8");
    const listeners = new Set(), states = [];
    let enter, resume, delay = true;
    const entered = new Promise(resolve => { enter = resolve; });
    const pendingListener = new Promise(resolve => { resume = resolve; });
    const controller = createTtsControllerHost({
      invoke: async () => ({ source }),
      listen: async (name, callback) => {
        if (delay) {
          delay = false;
          enter();
          await pendingListener;
        }
        const entry = { name, callback };
        listeners.add(entry);
        return () => listeners.delete(entry);
      },
      onPlaybackState: value => states.push(value),
    }, source => import(`data:text/javascript,${encodeURIComponent(source)}`));
    const old = controller.rebind("old-generation");
    await entered;
    if (invalidation === "dispose") controller.dispose();
    else assert.equal(await controller.rebind("current-generation"), true);
    const beforeLate = states.slice();
    resume();
    assert.equal(await old, false);
    assert.deepEqual(states, beforeLate);
    assert.equal(listeners.size, invalidation === "dispose" ? 0 : 2);
    controller.dispose();
    assert.equal(listeners.size, 0);
  });
}
