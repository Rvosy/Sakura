import assert from "node:assert/strict";
import test from "node:test";

import {
  CHARACTER_SWITCH_TIMEOUT_MS,
  applyCharacterSwitch,
  commitCharacterSelection,
  countCharacterScopedCollectionDrafts,
  hasCharacterScopedDrafts,
  pendingCharacterSelection,
  setCharacterSwitchLock,
  waitForCharacterSwitch,
} from "../settings/character-switch-runtime.js";

test("character switch timeout covers the full Core shutdown and readiness budget", () => {
  assert.equal(CHARACTER_SWITCH_TIMEOUT_MS, 60_000);
});

const receipt = Object.freeze({
  restartState: "requested",
  previousCoreGenerationId: "generation-a",
  targetCharacterId: "character-b",
});

function lifecycle({
  generationId = "generation-b",
  generationNumber = 2,
  readiness = "ready",
  characterId = "character-b",
} = {}) {
  return {
    supervisor: { generationId, generationNumber },
    snapshot: { generationId, readiness },
    characterPresentation: { generationId, characterId },
  };
}

test("character-specific appearance, voice, and Memory drafts block switching", () => {
  assert.equal(hasCharacterScopedDrafts(), false);
  assert.equal(hasCharacterScopedDrafts({ appearanceDirty: true }), true);
  assert.equal(hasCharacterScopedDrafts({ voiceDirty: true }), true);
  assert.equal(hasCharacterScopedDrafts({ memorySettingsDirty: true }), true);
  assert.equal(hasCharacterScopedDrafts({ memoryDraft: { content: "draft" } }), true);
  assert.equal(hasCharacterScopedDrafts({ memoryEditorDraftCount: 1 }), true);
});

test("only Memory collection editors count as character-scoped drafts", () => {
  const states = [
    { surface: "memory", editor: { values: { content: "draft" } } },
    { surface: "memory", editor: null },
    { surface: "plugins", editor: { values: { name: "global draft" } } },
  ];
  assert.equal(countCharacterScopedCollectionDrafts(states), 1);
});

test("character choices remain local drafts until apply commits only the final target", async () => {
  let selectedCharacterId = "character-a";
  const selectedByBackend = [];

  selectedCharacterId = "character-b";
  assert.equal(pendingCharacterSelection({
    committedCharacterId: "character-a",
    selectedCharacterId,
  }), "character-b");
  selectedCharacterId = "character-c";
  assert.equal(selectedByBackend.length, 0);

  const applied = [];
  await commitCharacterSelection({
    committedCharacterId: "character-a",
    selectedCharacterId,
    readLifecycle: async () => ({ supervisor: { generationNumber: 7 } }),
    async selectCharacter(characterId) {
      selectedByBackend.push(characterId);
      return { targetCharacterId: characterId };
    },
    async applyChange(nextReceipt, previousLifecycle) {
      applied.push({ nextReceipt, previousLifecycle });
      return "switched";
    },
  });

  assert.deepEqual(selectedByBackend, ["character-c"]);
  assert.equal(applied.length, 1);
  assert.equal(applied[0].previousLifecycle.supervisor.generationNumber, 7);
});

test("selecting the committed character again clears the draft and performs no commit", async () => {
  assert.equal(pendingCharacterSelection({
    committedCharacterId: "character-a",
    selectedCharacterId: "character-a",
  }), null);

  let lifecycleReads = 0;
  let backendWrites = 0;
  const result = await commitCharacterSelection({
    committedCharacterId: "character-a",
    selectedCharacterId: "character-a",
    readLifecycle: async () => { lifecycleReads += 1; },
    selectCharacter: async () => { backendWrites += 1; },
    applyChange: async () => {},
  });

  assert.equal(result, null);
  assert.equal(lifecycleReads, 0);
  assert.equal(backendWrites, 0);
});

test("switching locks character pages and aggregate submit without locking global pages", () => {
  const rolePage = { inert: false, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; } };
  const globalPage = { inert: false };
  const save = { disabled: false };
  setCharacterSwitchLock({ pages: [rolePage], submitControls: [save] }, true);
  assert.equal(rolePage.inert, true);
  assert.equal(rolePage.attributes["aria-busy"], "true");
  assert.equal(save.disabled, true);
  assert.equal(globalPage.inert, false);

  setCharacterSwitchLock({ pages: [rolePage], submitControls: [save] }, false);
  assert.equal(rolePage.inert, false);
  assert.equal(save.disabled, false);
});

test("switch completion requires a newer consistent ready generation and target presentation", async () => {
  const publications = [
    lifecycle({ generationId: "generation-a", generationNumber: 1, characterId: "character-a" }),
    lifecycle({ characterId: "character-a" }),
    lifecycle(),
  ];
  const result = await waitForCharacterSwitch({
    receipt,
    previousGenerationNumber: 1,
    readLifecycle: async () => publications.shift(),
    delay: async () => {},
  });
  assert.equal(result.characterPresentation.characterId, "character-b");
  assert.equal(publications.length, 0);
});

test("switch coordinator clears old role state before waiting and always leaves switching", async () => {
  const sequence = [];
  await applyCharacterSwitch({
    receipt,
    previousLifecycle: { supervisor: { generationNumber: 1 } },
    applyCommittedSnapshot() { sequence.push("snapshot"); },
    clearCharacterState() { sequence.push("clear"); },
    async rebindSettings(next) {
      sequence.push(`rebind:${next.characterPresentation.characterId}`);
    },
    setSwitching(value) { sequence.push(`switching:${value}`); },
    readLifecycle: async () => lifecycle(),
    delay: async () => {},
  });
  assert.deepEqual(sequence, [
    "snapshot",
    "switching:true",
    "clear",
    "rebind:character-b",
    "switching:false",
  ]);
});

test("failed target initialization does not rebind or retry the committed mutation", async () => {
  let reads = 0;
  let rebound = false;
  const switching = [];
  await assert.rejects(
    applyCharacterSwitch({
      receipt,
      previousLifecycle: { supervisor: { generationNumber: 1 } },
      applyCommittedSnapshot() {},
      clearCharacterState() {},
      rebindSettings() { rebound = true; },
      setSwitching(value) { switching.push(value); },
      readLifecycle: async () => {
        reads += 1;
        return lifecycle({ readiness: "failed" });
      },
      delay: async () => {},
    }),
    /CHARACTER_SWITCH_INITIALIZATION_FAILED/,
  );
  assert.equal(reads, 1);
  assert.equal(rebound, false);
  assert.deepEqual(switching, [true, false]);
});
