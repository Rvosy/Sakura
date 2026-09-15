import test from "node:test";
import assert from "node:assert/strict";
import { createRendererHost } from "../pet/renderer-host.js";
import { createChatPresentationReducer } from "../chat/chat-presentation.js";

const binding = (id = "a") => ({ schemaVersion: 2, visual: { bindingId: id.repeat(32), resourceId: "numeric-1", renderer: "fixture:renderer", data: { maxAngle: 30 }, assets: {} } });
const control = (id = "a") => ({ version: 1, bindingId: id.repeat(32), resourceId: "numeric-1", state: { angle: 12 }, actions: [{ wave: true }] });
const container = () => {
  const root = { children: [], append(child) { this.children.push(child); }, replaceChildren(...children) { this.children = children; } };
  root.ownerDocument = { createElement: () => ({ remove() { root.children = root.children.filter(child => child !== this); } }) };
  return root;
};
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

test("switching a form does not restore revoked resources from the retired renderer", async () => {
  let restores = 0;
  const host = createRendererHost({
    container: container(), services: { cancelSurface: () => restores++ },
    loadModule: async () => ({ mount: ({ host: scoped }) => ({
      applyState() {},
      cancel() { void scoped.cancelSurface(); },
      destroy() {},
    }) }),
  });
  await host.bind(binding());
  host.begin("old-operation");
  await host.bind(binding("b"));
  assert.equal(restores, 0);
  host.begin("new-operation");
  host.cancel();
  assert.ok(restores > 0, "ordinary interruption still restores the current surface");
  restores = 0;
  host.begin("next-operation");
  host.freeze("generation_changed");
  assert.equal(restores, 0);
  host.destroy();
});

test("generation changes revoke old controls but retain the surface until replacement is ready", async () => {
  const next = deferred();
  const root = container();
  let mounts = 0, destroyed = 0, oldSignal;
  const host = createRendererHost({ container: root, loadModule: async () => ({ mount: ({ signal }) => {
    const number = ++mounts;
    if (number === 1) oldSignal = signal;
    return { ready: number === 1 ? undefined : next.promise, applyState() {}, cancel() {}, destroy() { destroyed++; } };
  } }) });
  await host.bind(binding());
  const previous = root.children[0];
  host.freeze("generation_changed");
  assert.equal(oldSignal.aborted, true);
  assert.equal(host.current(), null);
  assert.deepEqual(root.children, [previous]);
  assert.equal(destroyed, 0);
  const opening = host.bind(binding("b"));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(root.children[0], previous);
  assert.equal(destroyed, 0);
  next.resolve();
  assert.equal(await opening, true);
  assert.equal(root.children.length, 1);
  assert.notEqual(root.children[0], previous);
  assert.equal(destroyed, 1);
  host.destroy();
});

test("only actual segment starts execute state and one-shot actions; duplicate and old targets are ignored", async () => {
  const events = [];
  const host = createRendererHost({ container: container(), loadModule: async () => ({ mount: () => ({ ready: Promise.resolve(), applyState: (state) => events.push(state), perform: (action) => events.push(action), cancel() {}, destroy() {} }) }) });
  await host.bind(binding());
  host.begin("op-1");
  assert.deepEqual(events, []);
  assert.equal(await host.play(control(), "op-1", 0), true);
  assert.equal(await host.play(control(), "op-1", 0), false);
  assert.deepEqual(events, [{ angle: 12 }, { wave: true }]);
  host.cancel();
  assert.equal(await host.play(control(), "op-1", 1), false);
  await host.bind(binding("b"));
  host.begin("op-2");
  assert.equal(await host.play(control(), "op-2", 0), false);
  assert.equal(await host.play(control("b"), "op-2", 0), true);
  host.destroy();
});

test("cancelling an in-flight state cannot perform its pending actions", async () => {
  const pending = deferred();
  let actions = 0;
  let signal;
  const host = createRendererHost({ container: container(), loadModule: async () => ({ mount: () => ({ ready: Promise.resolve(), applyState: (_state, context) => { signal = context.signal; return pending.promise; }, perform: () => actions++, destroy() {} }) }) });
  await host.bind(binding());
  host.begin("op");
  const playing = host.play(control(), "op", 0);
  await Promise.resolve();
  host.cancel();
  pending.resolve();
  assert.equal(await playing, false);
  assert.equal(signal.aborted, true);
  assert.equal(actions, 0);
  host.destroy();
});

test("late mounts are destroyed and cannot invoke host services after replacement", async () => {
  const pending = deferred();
  let scoped;
  let destroyed = 0;
  let invoked = 0;
  const host = createRendererHost({ container: container(), services: { surface: () => invoked++ }, loadModule: async () => ({ mount: ({ host }) => { scoped = host; return pending.promise; } }) });
  const mounting = host.bind(binding());
  await new Promise((r) => setTimeout(r, 0));
  host.clear();
  pending.resolve({ applyState() {}, destroy: () => destroyed++ });
  assert.equal(await mounting, false);
  await scoped.surface();
  assert.equal(invoked, 0);
  assert.equal(destroyed, 1);
});

test("history reducer preserves segment identities and controls for visual review", () => {
  const reducer = createChatPresentationReducer({ initialMessage: "你好" });
  const identity = { generationId: "g", generationNumber: 1, operationId: "op" };
  reducer.reduce({ ...identity, type: "lifecycle", status: "ready", revision: 1 });
  reducer.reduce({ ...identity, type: "chat.started" });
  reducer.reduce({ ...identity, type: "chat.completed", reply: { segments: [{ text: "one", control: control() }, { text: "two", control: { broken: true } }] } });
  reducer.finishTyping();
  reducer.reviewReplyAt(0, "one");
  assert.deepEqual(reducer.current().replyHistorySegments[0].control, control());
  assert.equal(Object.hasOwn(reducer.current().replyHistorySegments[1], "control"), false);
  assert.equal(reducer.current().bubbleText, "one");
});

test("a rejected control preserves the renderer and reports a recoverable diagnostic", async () => {
  const errors = [];
  const unavailable = [];
  let destroyed = false;
  let angle = 0;
  const host = createRendererHost({ container: container(), onError: (code, error, stage) => errors.push({ code, error, stage }), onUnavailable: (code) => unavailable.push(code),
    loadModule: async () => ({ mount: () => ({
      applyState(state) { if (state.angle < 0) throw new Error("invalid state"); angle = state.angle; },
      destroy() { destroyed = true; },
    }) }),
  });
  await host.bind(binding());
  host.begin("op");
  assert.equal(await host.play(control(), "op", 0), true);
  assert.equal(await host.play({ ...control(), state: { angle: -1 } }, "op", 1), false);
  assert.equal(angle, 12);
  assert.equal(destroyed, false);
  assert.deepEqual(unavailable, []);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, "VISUAL_CONTROL_EXECUTION_FAILED");
  assert.equal(errors[0].stage, "visual.control.execute");
  assert.match(errors[0].error.stack, /invalid state/);
  assert.equal(await host.play({ ...control(), state: { angle: 20 } }, "op", 2), true);
  assert.equal(angle, 20);
  host.destroy();
});


test("renderer startup keeps the original failure and stale failures stay silent", async () => {
  const errors = [];
  const failure = new TypeError("renderer shader missing");
  let reject;
  const host = createRendererHost({ container: container(), onUnavailable: (...args) => errors.push(args),
    loadModule: async () => ({ mount: () => ({ applyState() {}, destroy() {}, ready: Promise.reject(failure) }) }),
  });
  assert.equal(await host.bind(binding()), false);
  assert.equal(errors[0][1], failure);
  assert.equal(errors[0][2], "visual.renderer.ready");
  const late = createRendererHost({ container: container(), onUnavailable: (...args) => errors.push(args),
    loadModule: () => new Promise((_, fail) => { reject = fail; }),
  });
  const opening = late.bind(binding());
  late.clear();
  reject(failure);
  assert.equal(await opening, false);
  assert.equal(errors.length, 1);
});


test("reply review restores captured state, including inherited fields, without repeating actions", async () => {
  let current = { angle: 0, mood: "idle" }, actions = 0;
  const host = createRendererHost({ container: container(), loadModule: async () => ({ mount: () => ({
    applyState: state => { current = { ...current, ...state }; },
    snapshotState: () => current,
    perform: () => actions++,
    cancel() {},
    destroy() {},
  }) }) });
  await host.bind(binding());
  const reducer = createChatPresentationReducer({ initialMessage: "hello" });
  const identity = { generationId: "g", generationNumber: 1, operationId: "op" };
  reducer.reduce({ ...identity, type: "lifecycle", status: "ready", revision: 1 });
  reducer.reduce({ ...identity, type: "chat.started" });
  reducer.reduce({ ...identity, type: "chat.completed", reply: { segments: [
    { text: "first", control: { ...control(), state: { angle: 10, mood: "smile" } } },
    { text: "inherit" },
    { text: "last", control: { ...control(), state: { mood: "sad" } } },
  ] } });
  host.begin("op");
  const segments = reducer.current().segments;
  for (const [index, segment] of segments.entries()) {
    await host.play(segment.control, "op", index, segment);
    reducer.setTypingSegment(segment, index);
  }
  reducer.finishTyping();
  assert.equal(actions, 2);
  assert.deepEqual(current, { angle: 10, mood: "sad" });
  for (const [index, mood] of [[0, "smile"], [2, "sad"], [1, "smile"], [0, "smile"]]) {
    const result = reducer.reviewReplyAt(index, segments[index].text);
    assert.equal(result.applied, true);
    assert.equal(await host.review(result.state.replyHistorySegments[index]), true);
    assert.deepEqual(current, { angle: 10, mood });
    assert.equal(actions, 2);
  }
  // New live output resumes the latest live state rather than the reviewed older face.
  host.begin("next");
  await host.play(undefined, "next", 0, {});
  assert.deepEqual(current, { angle: 10, mood: "sad" });
  await host.bind(binding("b"));
  assert.equal(await host.review(segments[0]), false);
  assert.equal(actions, 2);
  host.destroy();
});

test("rapid history changes and new replies abort stale restoration", async () => {
  const entered = deferred(), pending = deferred();
  let current = { angle: 0 }, hold = false, interrupted;
  const host = createRendererHost({ container: container(), loadModule: async () => ({ mount: () => ({
    async applyState(state, context) {
      if (hold && state.angle === 10) { interrupted = context.signal; entered.resolve(); await pending.promise; }
      if (!context.signal.aborted) current = { ...state };
    },
    snapshotState: () => current,
    cancel() {}, destroy() {},
  }) }) });
  await host.bind(binding());
  const first = {}, last = {};
  host.begin("live");
  await host.play({ ...control(), state: { angle: 10 } }, "live", 0, first);
  await host.play({ ...control(), state: { angle: 20 } }, "live", 1, last);
  hold = true;
  const old = host.review(first);
  await entered.promise;
  assert.equal(await host.review(last), true);
  pending.resolve();
  assert.equal(await old, false);
  assert.equal(interrupted.aborted, true);
  assert.deepEqual(current, { angle: 20 });
  host.begin("new");
  await host.play({ ...control(), state: { angle: 30 } }, "new", 0, {});
  assert.deepEqual(current, { angle: 30 });
  host.destroy();
});

test("history review never replays raw control for a renderer without state snapshots", async () => {
  let calls = 0;
  const host = createRendererHost({ container: container(), loadModule: async () => ({ mount: () => ({
    applyState() { calls++; }, destroy() {},
  }) }) });
  await host.bind(binding());
  const segment = {};
  host.begin("live");
  await host.play(control(), "live", 0, segment);
  assert.equal(await host.review(segment), false);
  assert.equal(calls, 1);
  host.destroy();
});

test("snapshot failures do not interrupt live actions", async () => {
  let actions = 0;
  const errors = [];
  const host = createRendererHost({ container: container(), onError: code => errors.push(code),
    loadModule: async () => ({ mount: () => ({
      applyState() {},
      snapshotState() { throw new Error("snapshot failed"); },
      perform() { actions++; },
      destroy() {},
    }) }) });
  await host.bind(binding());
  host.begin("live");
  const segment = {};
  assert.equal(await host.play(control(), "live", 0, segment), true);
  assert.equal(actions, 1);
  assert.deepEqual(errors, ["VISUAL_STATE_SNAPSHOT_FAILED"]);
  assert.equal(await host.review(segment), false);
  host.destroy();
});
