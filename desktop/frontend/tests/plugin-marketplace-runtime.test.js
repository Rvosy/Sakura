import assert from "node:assert/strict";
import test from "node:test";
import { canInstall, hasUpdate, recommended, createCatalogLoader } from "../settings/plugin-marketplace-runtime.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("refresh ignores an older catalog even when its source ignores cancellation", async () => {
  const requests = [], states = [];
  const loader = createCatalogLoader({ load({ signal }) {
    const request = { ...deferred(), signal }; requests.push(request); return request.promise;
  } }, state => states.push(state));
  const old = loader.load(), current = loader.load();
  assert.equal(requests[0].signal.aborted, true);
  requests[1].resolve({ state: "ready", plugins: [{ id: "current" }] }); await current;
  requests[0].resolve({ state: "ready", plugins: [{ id: "old" }] }); await old;
  assert.equal(states.at(-1).plugins[0].id, "current");
  assert.equal(states.filter(s => s.state === "ready").length, 1);
});

test("disposing aborts loading and prevents a late error from updating the page", async () => {
  const pending = deferred(), states = []; let signal;
  const loader = createCatalogLoader({ load(args) { signal = args.signal; return pending.promise; } }, s => states.push(s));
  const operation = loader.load(); loader.dispose();
  pending.reject(new Error("offline")); await operation;
  assert.equal(signal.aborted, true);
  assert.deepEqual(states.map(s => s.state), ["loading"]);
});

test("load failure can be retried and cached results retain their offline state", async () => {
  let fail = true; const states = [];
  const loader = createCatalogLoader({ async load() {
    if (fail) throw new Error("offline");
    return { state: "cached", plugins: [{ id: "cached" }] };
  } }, s => states.push(s));
  await loader.load(); assert.equal(states.at(-1).state, "error");
  fail = false; await loader.load(); assert.equal(states.at(-1).state, "cached");
});

test("unconfigured market cannot offer installation", async () => {
  const states = [];
  await createCatalogLoader(null, s => states.push(s)).load();
  assert.deepEqual(states, [{ state: "unconfigured", plugins: [] }]);
  assert.equal(canInstall({ versions: [{ number: "1.0.0" }], recommendedVersion: "1.0.0" }, null), false);
});

test("installation respects upstream recommendation, withdrawn versions and update support", () => {
  const p = { versions: [{ number: "2.0.0", compatible: false }, { number: "1.0.0" }], recommendedVersion: "1.0.0" };
  const source = { install() {} };
  assert.equal(recommended(p).number, "1.0.0"); assert.equal(canInstall(p, source), true);
  p.installed = "0.9.0"; assert.equal(canInstall(p, source), false);
  source.canUpdate = true; assert.equal(canInstall(p, source), true);
  p.installed = "1.0.0"; assert.equal(canInstall(p, source), false);
  delete p.installed;
  p.versions[1].yanked = "withdrawn"; assert.equal(canInstall(p, source), false);
  p.recommendedVersion = "2.0.0"; assert.equal(canInstall(p, source), false);
});


test("update visibility follows compatible stable recommendations even when updating is blocked", () => {
  const plugin = { installed: "1.0.0", recommendedVersion: "1.1.0", versions: [{ number: "1.1.0" }], updateBlocked: "请先停用插件再更新" };
  assert.equal(hasUpdate(plugin), true);
  assert.equal(canInstall(plugin, { install() {}, canUpdate: true }), false);
  for (const installed of [undefined, "1.1.0", "2.0.0"]) {
    assert.equal(hasUpdate({ ...plugin, installed }), false);
  }
  for (const status of [{ compatible: false }, { yanked: "withdrawn" }, { prerelease: true }]) {
    assert.equal(hasUpdate({ ...plugin, versions: [{ number: "1.1.0", ...status }] }), false);
  }
});
