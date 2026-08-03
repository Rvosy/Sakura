import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import {
  createMemoryController,
  normalizeMemoryRecord,
  validateMemorySnapshot,
} from "../settings/memory-runtime.js";

function fakeDocument() {
  const elements = new Map();
  for (const id of ["memoryCurationProvider", "memoryCurationModel", "memoryTriggerTurns"]) {
    elements.set(id, {
      value: "",
      min: "",
      max: "",
      textContent: "",
      children: [],
      append(child) { this.children.push(child); },
      addEventListener() {},
    });
  }
  return {
    getElementById: (id) => elements.get(id),
    createElement: () => ({ value: "", textContent: "" }),
  };
}

const snapshot = () => ({
  schemaVersion: 1,
  windowGeneration: 3,
  coreGenerationId: "generation-3",
  status: "ready",
  message: "",
  curation: {
    enabled: true,
    triggerTurns: 8,
    backfillLimit: 200,
    available: true,
  },
  curationModelSlot: { profileId: "fixture", model: "curator" },
  providerChoices: [{ id: "fixture", alias: "Fixture", models: ["curator"] }],
  embedding: {
    model: "sentence-transformers/all-MiniLM-L6-v2",
    dimensions: 384,
    installed: true,
    task: null,
  },
});

test("Memory snapshot is generation scoped and fixes the embedding model contract", () => {
  assert.equal(validateMemorySnapshot(snapshot()).coreGenerationId, "generation-3");
  assert.throws(() => validateMemorySnapshot({ ...snapshot(), coreGenerationId: "" }));
  assert.throws(() => validateMemorySnapshot({
    ...snapshot(),
    embedding: { ...snapshot().embedding, dimensions: 768 },
  }));
});

test("Memory records retain Chinese and Japanese input while projecting timestamps", () => {
  const record = normalizeMemoryRecord({
    id: "memory-1",
    content: "中文草稿と日本語の下書き",
    layer: "semantic",
    category: "preference",
    source: "explicit",
    importance: 0.8,
    confidence: 0.9,
    scope: "sakura",
    createdAt: "2026-08-03T10:00:00+08:00",
    updatedAt: "2026-08-03T10:01:00+08:00",
    lastAccessedAt: "",
    score: 0.7,
  });
  assert.equal(record.content, "中文草稿と日本語の下書き");
  assert.equal(record.updated_at, "2026-08-03T10:01:00+08:00");
  assert.throws(() => normalizeMemoryRecord({ ...record, scope: "" }));
});

test("Runtime Memory frontend receives only opaque identities and never asks for a data path", () => {
  const source = fs.readFileSync(new URL("../settings/memory-runtime.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /hostCall\s*\(/);
  assert.doesNotMatch(source, /data[\\/]memory|qdrant|mem0_history|api_key/i);
  assert.match(source, /windowGeneration/);
  assert.match(source, /coreGenerationId/);
});

test("model progress is generation scoped and exposes cancel only through the opaque handle", async () => {
  let listener = null;
  const calls = [];
  const updates = [];
  const invoke = async (name, payload) => {
    calls.push([name, payload]);
    if (name === "settings_memory_model_download") {
      return {
        accepted: true,
        taskId: "task-1",
        taskHandle: "opaque-handle",
        status: "starting",
      };
    }
    if (name === "settings_memory_model_cancel") return { accepted: true, taskId: "task-1" };
    throw new Error(name);
  };
  const controller = createMemoryController({
    document: fakeDocument(),
    invoke,
    listen: async (_name, callback) => { listener = callback; return () => {}; },
    applySnapshot: () => {},
    onDirty: () => {},
    onError: () => {},
    onModelEvent: (task) => updates.push(task),
  });
  await controller.initialize(snapshot());
  await controller.downloadModel();
  listener({ payload: {
    type: "memory.model.progress",
    generationId: "stale-generation",
    windowGeneration: 3,
    taskId: "task-1",
    stage: "downloading",
    progress: 50,
  } });
  assert.equal(updates.length, 0);
  listener({ payload: {
    type: "memory.model.progress",
    generationId: "generation-3",
    windowGeneration: 3,
    taskId: "task-1",
    stage: "downloading",
    progress: 50,
  } });
  assert.equal(controller.embedding().task.progress, 50);
  assert.equal(await controller.cancelModel(), true);
  assert.equal(calls.at(-1)[1].taskHandle, "opaque-handle");
  controller.dispose();
});
