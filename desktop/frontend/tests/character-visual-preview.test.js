import assert from "node:assert/strict";
import test from "node:test";

import {
  createCharacterVisualPreviewSessionController,
  restoreCommittedCharacterVisual,
} from "../pet/character-visual-preview.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function publication(characterId, revision, windowGeneration = 1) {
  return { schemaVersion: 1, characterId, revision, windowGeneration };
}

test("late B preview cannot overwrite C portrait, theme, or greeting", async () => {
  let coreGenerationId = "generation-a";
  const controller = createCharacterVisualPreviewSessionController({
    currentCoreGenerationId: () => coreGenerationId,
  });
  const bLoaded = deferred();
  const applied = [];

  const runPreview = async (next, loaded = Promise.resolve()) => {
    const token = controller.begin(next);
    if (!token) return;
    await loaded;
    if (!controller.isCurrent(token)) return;
    applied.push(next.characterId);
  };

  const b = runPreview(publication("B", 1), bLoaded.promise);
  await runPreview(publication("C", 2));
  bLoaded.resolve();
  await b;

  assert.equal(coreGenerationId, "generation-a");
  assert.deepEqual(applied, ["C"]);
});

test("Core generation rebind invalidates a preview continuation at every async boundary", async () => {
  let coreGenerationId = "generation-a";
  let blocked = false;
  const controller = createCharacterVisualPreviewSessionController({
    currentCoreGenerationId: () => coreGenerationId,
    blocked: () => blocked,
  });
  const resourceLoaded = deferred();
  const token = controller.begin(publication("B", 1));
  assert.ok(token);

  const continuation = (async () => {
    await resourceLoaded.promise;
    return controller.isCurrent(token);
  })();

  blocked = true;
  controller.invalidate();
  coreGenerationId = "generation-b";
  blocked = false;
  resourceLoaded.resolve();

  assert.equal(await continuation, false);
  assert.equal(controller.isCurrent(token), false);
});

test("a clean select-back preview supersedes the staged role before close waits", async () => {
  const controller = createCharacterVisualPreviewSessionController({
    currentCoreGenerationId: () => "generation-a",
  });
  const stagedLoaded = deferred();
  const restoredLoaded = deferred();
  const applied = [];

  const preview = async (next, loaded) => {
    const token = controller.begin(next);
    await loaded.promise;
    if (controller.isCurrent(token)) applied.push(next.characterId);
  };

  const staged = preview(publication("B", 1), stagedLoaded);
  const restored = preview(publication("A", 2), restoredLoaded);
  stagedLoaded.resolve();
  await staged;
  assert.deepEqual(applied, []);

  let closed = false;
  const close = restored.then(() => { closed = true; });
  await Promise.resolve();
  assert.equal(closed, false);
  restoredLoaded.resolve();
  await close;
  assert.deepEqual(applied, ["A"]);
  assert.equal(closed, true);
});

test("select-back restores the latest committed portrait after an in-flight role event", async () => {
  let committedState = { portrait: "calm" };
  let renderedPortrait = "calm";
  const visualEffect = deferred();
  const restored = [];

  const restore = (async () => {
    await visualEffect.promise;
    return restoreCommittedCharacterVisual({
      currentState: () => committedState,
      resetRenderedPortrait: () => { renderedPortrait = null; },
      render: async (state) => {
        assert.equal(renderedPortrait, null);
        renderedPortrait = state.portrait;
        restored.push(state.portrait);
        return { applied: true, key: state.portrait };
      },
    });
  })();

  committedState = { portrait: "speaking" };
  renderedPortrait = "speaking";
  visualEffect.resolve();

  assert.deepEqual(await restore, { applied: true, key: "speaking" });
  assert.equal(renderedPortrait, "speaking");
  assert.deepEqual(restored, ["speaking"]);
});
