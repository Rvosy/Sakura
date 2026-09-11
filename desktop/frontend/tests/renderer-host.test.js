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

test("history browsing keeps controls as data and never invokes renderer code", () => {
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
