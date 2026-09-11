import test from "node:test";
import assert from "node:assert/strict";
import { createVisualEditorHost } from "../studio/visual-editor-host.js";

const container = () => {
  const root = { children: [], append(child) { this.children.push(child); }, replaceChildren(...children) { this.children = children; } };
  root.ownerDocument = { createElement: () => ({ remove() { root.children = root.children.filter(child => child !== this); } }) };
  return root;
};
const descriptor = { presentation: { visual: { editor: "fixture:editor" } }, data: { Private_Key: 1 } };
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

test("editor assets use the authorized root without IPC and retain content until the next editor is ready", async () => {
  const next = deferred();
  const root = container();
  let calls = 0, mounts = 0, destroyed = 0, scoped;
  const host = createVisualEditorHost({ container: root, assetUrl: () => { calls++; }, loadModule: async () => ({ mountEditor: ({ host }) => {
    scoped = host;
    return { ready: ++mounts === 1 ? undefined : next.promise, collect() {}, validate() {}, destroy() { destroyed++; } };
  } }) });
  const value = { ...descriptor, assetBaseUrl: "https://assets/" };
  await host.open(value);
  assert.equal(await scoped.assetUrl("模型/a.json"), "https://assets/" + Buffer.from("模型/a.json").toString("hex"));
  assert.equal(calls, 0);
  const old = root.children[0];
  host.freeze();
  assert.equal(root.inert, true);
  assert.deepEqual(root.children, [old]);
  const opening = host.open(value);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(root.children[0], old);
  assert.equal(destroyed, 0);
  next.resolve();
  assert.equal(await opening, true);
  assert.equal(root.inert, false);
  assert.equal(root.children.length, 1);
  assert.equal(destroyed, 1);
  host.clear();
});

test("only the latest thumbnail request may update the selected resource cover", async () => {
  const pending = deferred();
  const previews = [];
  let scoped;
  const host = createVisualEditorHost({ container: container(),
    assetUrl: path => path === "old.png" ? pending.promise : Promise.resolve(path),
    onPreview: url => previews.push(url),
    loadModule: async () => ({ mountEditor: ({ host }) => { scoped = host; return { collect() {}, validate() {}, destroy() {} }; } }),
  });
  await host.open(descriptor);
  const old = scoped.previewImage("old.png");
  const latest = scoped.previewImage("new.png");
  pending.resolve("old.png");
  await Promise.all([old, latest]);
  assert.deepEqual(previews, ["new.png"]);
  await scoped.previewImage(null);
  assert.deepEqual(previews, ["new.png", null]);
  host.clear();
});

test("slow assets do not block other images or another editor; successful URLs are reused", async () => {
  const pending = deferred();
  const calls = [];
  let scoped;
  const host = createVisualEditorHost({ container: container(),
    assetUrl: path => { calls.push(path); return path === "old.png" ? pending.promise : Promise.resolve(path); },
    loadModule: async () => ({ mountEditor: ({ host }) => { scoped = host; return { collect() {}, validate() {}, destroy() {} }; } }),
  });
  await host.open(descriptor);
  const old = scoped.assetUrl("old.png");
  const duplicate = scoped.assetUrl("old.png");
  assert.equal(await scoped.assetUrl("fast.png"), "fast.png");
  assert.equal(await scoped.assetUrl("fast.png"), "fast.png");
  assert.deepEqual(calls, ["old.png", "fast.png"]);
  await host.open(descriptor);
  const current = scoped.assetUrl("current.png");
  assert.equal(await current, "current.png");
  pending.resolve("old.png");
  assert.equal(await old, null);
  assert.equal(await duplicate, null);
  assert.deepEqual(calls, ["old.png", "fast.png", "current.png"]);
  host.clear();
});

test("replaced editors cannot publish drafts or finish imports; late mounts are destroyed", async () => {
  const pending = deferred();
  const importing = deferred();
  const changes = [];
  let scoped;
  let destroyed = 0;
  const host = createVisualEditorHost({ container: container(), onChange: (data) => changes.push(data), importFiles: () => importing.promise,
    loadModule: async () => ({ mountEditor: ({ host }) => { scoped = host; return pending.promise; } }) });
  const opening = host.open(descriptor);
  await new Promise((resolve) => setTimeout(resolve, 0));
  scoped.changed({ Private_Key: 2 });
  const files = scoped.importFiles();
  host.clear();
  scoped.changed({ Private_Key: 3 });
  importing.resolve([{ resourcePath: "old.png" }]);
  pending.resolve({ collect() {}, validate() {}, destroy() { destroyed++; } });
  assert.equal(await opening, false);
  assert.deepEqual(await files, []);
  assert.deepEqual(changes, [{ Private_Key: 2 }]);
  assert.equal(destroyed, 1);
});

test("timed-out editor releases the container and revokes its services", async () => {
  const pending = deferred();
  let scoped;
  let changes = 0;
  let destroyed = 0;
  const root = container();
  const host = createVisualEditorHost({ container: root, timeoutMs: 10, onChange: () => changes++, loadModule: async () => ({ mountEditor: ({ host }) => { scoped = host; return pending.promise; } }) });
  await assert.rejects(host.open(descriptor), /VISUAL_EDITOR_TIMEOUT/);
  scoped.changed({ lost: true });
  pending.resolve({ destroy() { destroyed++; } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(changes, 0);
  assert.equal(destroyed, 1);
  assert.deepEqual(root.children, []);
});
