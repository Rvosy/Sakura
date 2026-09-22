import assert from "node:assert/strict";
import test from "node:test";
import { createHostInteractionController, createHostVisualController } from "../chat/host-interaction.js";

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test("desktop facts follow session and generation without publishing stale session responses", async () => {
  let generation = "g1", idle = true;
  const session = deferred(), calls = [];
  const controller = createHostInteractionController({
    generationId: () => generation, isReady: () => true, isIdle: () => idle,
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "host_interaction_current") return generation === "g1" ? session.promise : { sessionId: "s2" };
      return { accepted: true };
    },
  });
  const old = controller.update();
  generation = "g2";
  controller.invalidate();
  session.resolve({ sessionId: "s1" });
  await old;
  assert.equal(calls.some(([name]) => name === "host_interaction_state"), false);
  await controller.update();
  assert.deepEqual(calls.at(-1), ["host_interaction_state", { payload: {
    generationId: "g2", sessionId: "s2", idle: true, activityRevision: 1,
  } }]);
  const before = calls.length;
  await controller.update();
  assert.equal(calls.length, before);
  idle = false;
  await controller.update();
  assert.equal(calls.at(-1)[1].payload.idle, false);
  controller.dispose();
  await controller.update();
  assert.equal(calls.length, before + 1);
});

test("rejected desktop facts reacquire the role session before publishing activity", async () => {
  const calls = [];
  let session = "old", accepted = false;
  const controller = createHostInteractionController({ generationId: () => "g", isReady: () => true, isIdle: () => true,
    invoke: async (name, args) => {
      calls.push([name, args]);
      return name === "host_interaction_current" ? { sessionId: session } : { accepted };
    },
  });
  await controller.update();
  session = "new"; accepted = true;
  await controller.update();
  assert.equal(calls.filter(([name]) => name === "host_interaction_current").length, 2);
  assert.equal(calls.at(-1)[1].payload.sessionId, "new");
  assert.equal(calls.at(-1)[1].payload.activityRevision, 1);
  controller.dispose();
});

test("same-generation session publication restores revoked UI facts without user activity", async () => {
  let generation = "g1", remoteIdle = false;
  const calls = [];
  const controller = createHostInteractionController({
    generationId: () => generation, isReady: () => true, isIdle: () => true,
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "host_interaction_current") return { sessionId: "same-session" };
      remoteIdle = args.payload.idle;
      return { accepted: true };
    },
  });
  const publication = revision => ({ type: "lifecycle", generationId: generation, revision });
  await controller.handleLifecycle(publication(1));
  assert.equal(remoteIdle, true);
  const first = calls.length;
  await controller.update();
  assert.equal(calls.length, first, "unchanged timer updates remain deduplicated");

  // Applying model settings replaces the Core session and revokes ChatHost UI facts,
  // while the current character, generation, screen-session ID and idle state stay put.
  remoteIdle = false;
  await controller.handleLifecycle(publication(2));
  assert.equal(remoteIdle, true);
  assert.equal(calls.length, first + 2);
  assert.equal(calls.at(-1)[1].payload.sessionId, "same-session");
  await controller.handleLifecycle(publication(2));
  await controller.handleLifecycle(publication(1));
  await controller.update();
  assert.equal(calls.length, first + 2, "replayed lifecycle events do not republish");

  generation = "g2";
  await controller.handleLifecycle({ type: "lifecycle", generationId: "g1", revision: 3 });
  assert.equal(calls.length, first + 2, "old generations cannot trigger publication");
  await controller.handleLifecycle(publication(1));
  assert.equal(calls.at(-1)[1].payload.generationId, "g2");
  controller.dispose();
});

test("a lifecycle publication during an in-flight facts update survives its late acknowledgement", async () => {
  const entered = deferred(), acknowledgement = deferred(), calls = [];
  let remoteIdle = false, stateCalls = 0;
  const controller = createHostInteractionController({
    generationId: () => "g", isReady: () => true, isIdle: () => true,
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "host_interaction_current") return { sessionId: "same-session" };
      remoteIdle = args.payload.idle;
      if (++stateCalls === 1) {
        entered.resolve();
        return acknowledgement.promise;
      }
      return { accepted: true };
    },
  });
  const publication = revision => ({ type: "lifecycle", generationId: "g", revision });
  const oldUpdate = controller.handleLifecycle(publication(1));
  await entered.promise;
  // The new Core session clears UI facts while the first acknowledgement is in flight.
  remoteIdle = false;
  await controller.handleLifecycle(publication(2));
  assert.equal(stateCalls, 1);
  acknowledgement.resolve({ accepted: true });
  await oldUpdate;
  assert.equal(remoteIdle, false);

  // The next scheduled update must read the new session and republish its facts.
  await controller.update();
  assert.equal(calls.filter(([name]) => name === "host_interaction_current").length, 2);
  assert.equal(stateCalls, 2);
  assert.equal(remoteIdle, true);
  const published = calls.filter(([name]) => name === "host_interaction_state");
  assert.ok(published[1][1].payload.activityRevision > published[0][1].payload.activityRevision);
  await controller.update();
  assert.equal(stateCalls, 2, "only the new acknowledgement may establish deduplication");
  controller.dispose();
});

function visualFixture({ claim = Promise.resolve({ accepted: true }), play = Promise.resolve(true) } = {}) {
  const target = { characterId: "character", bindingId: "binding", resourceId: "resource" };
  const calls = [], rendered = [];
  const playEntered = deferred();
  let operation = null, idle = true, generation = "generation";
  let controller;
  const renderer = {
    current: () => target,
    begin(id) { operation = id; rendered.push(["begin", id]); },
    play(...args) { rendered.push(["play", ...args]); playEntered.resolve(); return play; },
    cancelOperation(id, reason) {
      if (operation !== id) return false;
      operation = null; rendered.push(["cancel", id, reason]); return true;
    },
  };
  controller = createHostVisualController({ renderer, generationId: () => generation,
    characterId: () => target.characterId, isIdle: () => idle && !controller.busy(),
    invoke: async (name, args) => { calls.push([name, args]); return name === "host_visual_claim" ? claim : { accepted: true }; },
    onError: error => assert.fail(error),
  });
  const event = (type = "host.visual.apply", requestId = "visual") => ({ type, requestId, generationId: "generation", target,
    control: { version: 1, bindingId: target.bindingId, resourceId: target.resourceId, state: { pose: "smile" } } });
  return { controller, event, calls, rendered, renderer, playEntered: playEntered.promise,
    setIdle: value => { idle = value; }, setGeneration: value => { generation = value; } };
}

test("cancellation while claim is pending prevents any late rendering", async () => {
  const claim = deferred();
  const env = visualFixture({ claim: claim.promise });
  const applying = env.controller.receive(env.event());
  await env.controller.receive(env.event("host.visual.cancel"));
  claim.resolve({ accepted: true });
  await applying;
  assert.deepEqual(env.rendered, []);
  assert.equal(env.calls.at(-1)[1].payload.status, "failed");
  assert.equal(env.controller.busy(), false);
});

test("displayed plugin controls release busy but remain owned until cancelled", async () => {
  const play = deferred();
  const env = visualFixture({ play: play.promise });
  const applying = env.controller.receive(env.event());
  await env.playEntered;
  assert.equal(env.controller.busy(), true);
  play.resolve(true);
  await applying;
  assert.equal(env.controller.busy(), false);
  assert.equal(env.calls.at(-1)[1].payload.status, "displayed");
  await env.controller.receive(env.event("host.visual.cancel"));
  assert.equal(env.rendered.at(-1)[0], "cancel");
});

test("a cancelled plugin cannot stop a later chat or publish a displayed result after cancellation", async () => {
  const play = deferred();
  const env = visualFixture({ play: play.promise });
  const applying = env.controller.receive(env.event());
  await env.playEntered;
  env.renderer.begin("new-chat");
  await env.controller.receive(env.event("host.visual.cancel"));
  play.resolve(true);
  await applying;
  assert.equal(env.rendered.some(([name]) => name === "cancel"), false);
  assert.equal(env.calls.at(-1)[1].payload.status, "failed");
});

test("claim cannot play into a changed generation or newly busy interaction", async () => {
  for (const change of [env => env.setGeneration("next"), env => env.setIdle(false)]) {
    const claim = deferred();
    const env = visualFixture({ claim: claim.promise });
    const applying = env.controller.receive(env.event());
    change(env);
    claim.resolve({ accepted: true });
    await applying;
    assert.deepEqual(env.rendered, []);
    assert.equal(env.calls.at(-1)[1].payload.status, "failed");
  }
});
