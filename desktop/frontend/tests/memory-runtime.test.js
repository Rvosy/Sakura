import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import {
  createMemoryController,
  isMemoryGenerationTransitionError,
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

test("Memory model controls live on the model page while the Memory page keeps only curation turns", () => {
  const markup = fs.readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const styles = fs.readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");
  const modelPage = markup.match(/<section id="page-model"[\s\S]*?<\/section>/)?.[0] || "";
  const memoryPage = markup.match(/<section id="page-memory"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(modelPage, /memoryCurationProvider/);
  assert.match(modelPage, /memoryCurationModel/);
  assert.match(modelPage, /memoryModelResourceCard/);
  assert.doesNotMatch(memoryPage, /memoryCurationProvider|memoryCurationModel|memoryModelResourceCard/);
  assert.match(memoryPage, /memoryTriggerTurns/);
  assert.match(memoryPage, /admin-workbench/);
  assert.match(styles, /\.admin-workbench\s*\{[\s\S]*?grid-template-columns:\s*minmax\(260px, 0\.9fr\)\s+minmax\(340px, 1\.1fr\)/);
  assert.doesNotMatch(styles, /#page-memory\s*\{[\s\S]*?grid-template-rows:\s*auto auto auto/);
});

test("settings recovery keeps IME composition editable while disabling Memory actions", () => {
  const source = fs.readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  assert.match(source, /memoryState\.composing = true/);
  assert.match(source, /if \(!memoryState\.composing\) \{\s*fields\.memoryContent\.value/);
  assert.match(source, /const actionsDisabled = readOnly \|\| memoryState\.loading \|\| memoryState\.rebinding/);
  assert.doesNotMatch(source, /const readOnly = \["loading"/);
});

test("generation transition errors are recognized without treating ordinary validation errors as reconnects", () => {
  assert.equal(isMemoryGenerationTransitionError(new Error("SETTINGS_CORE_GENERATION_MISMATCH")), true);
  assert.equal(isMemoryGenerationTransitionError("GENERATION_INVALIDATED: Router closed"), true);
  assert.equal(isMemoryGenerationTransitionError(new Error("记忆内容不能为空")), false);
});

test("search rebinds once, preserves settings draft, and discards the stale generation", async () => {
  const document = fakeDocument();
  const calls = [];
  let getCount = 0;
  const nextSnapshot = {
    ...snapshot(),
    coreGenerationId: "generation-4",
    curation: { ...snapshot().curation, triggerTurns: 12 },
  };
  const controller = createMemoryController({
    document,
    invoke: async (name, payload) => {
      calls.push([name, payload]);
      if (name === "settings_memory_search" && payload.coreGenerationId === "generation-3") {
        throw new Error("GENERATION_INVALIDATED: Router closed");
      }
      if (name === "settings_memory_get") {
        getCount += 1;
        return nextSnapshot;
      }
      if (name === "settings_memory_search") return { status: "ready", memories: [] };
      throw new Error(name);
    },
    applySnapshot: () => {},
    onDirty: () => {},
    onError: () => {},
    wait: async () => {},
  });
  await controller.initialize(snapshot());
  document.getElementById("memoryTriggerTurns").value = "9";
  await controller.search({ query: "draft", limit: 10 });
  assert.equal(getCount, 1);
  assert.equal(calls.at(-1)[1].coreGenerationId, "generation-4");
  assert.equal(document.getElementById("memoryTriggerTurns").value, "9");
  assert.equal(controller.isDirty(), true);
});

test("writes are never replayed after an uncertain generation transition", async () => {
  let writes = 0;
  const controller = createMemoryController({
    document: fakeDocument(),
    invoke: async (name) => {
      if (name === "settings_memory_upsert") {
        writes += 1;
        throw new Error("GENERATION_INVALIDATED: Router closed");
      }
      if (name === "settings_memory_get") {
        return { ...snapshot(), coreGenerationId: "generation-4" };
      }
      throw new Error(name);
    },
    applySnapshot: () => {},
    onDirty: () => {},
    onError: () => {},
    wait: async () => {},
  });
  await controller.initialize(snapshot());
  await assert.rejects(
    controller.upsert({ content: "中文草稿", layer: "semantic" }),
    /安全刷新记忆列表/,
  );
  assert.equal(writes, 1);
});

test("a pre-dispatch identity mismatch queues one safe write on the fresh generation", async () => {
  const generations = [];
  const controller = createMemoryController({
    document: fakeDocument(),
    invoke: async (name, payload) => {
      if (name === "settings_memory_upsert") {
        generations.push(payload.coreGenerationId);
        if (payload.coreGenerationId === "generation-3") {
          throw new Error("SETTINGS_CORE_GENERATION_MISMATCH");
        }
        return { status: "ready", memory: { id: "memory-new" } };
      }
      if (name === "settings_memory_get") {
        return { ...snapshot(), coreGenerationId: "generation-4" };
      }
      throw new Error(name);
    },
    applySnapshot: () => {},
    onDirty: () => {},
    onError: () => {},
    wait: async () => {},
  });
  await controller.initialize(snapshot());
  const result = await controller.upsert({ content: "恢复后的草稿", layer: "semantic" });
  assert.equal(result.memory.id, "memory-new");
  assert.deepEqual(generations, ["generation-3", "generation-4"]);
});

test("provider restart refreshes Memory identity even if another reconnect already observed the new generation", async () => {
  let gets = 0;
  const controller = createMemoryController({
    document: fakeDocument(),
    invoke: async (name) => {
      if (name === "settings_memory_get") {
        gets += 1;
        return { ...snapshot(), coreGenerationId: "generation-4" };
      }
      throw new Error(name);
    },
    applySnapshot: () => {},
    onDirty: () => {},
    onError: () => {},
    wait: async () => {},
  });
  await controller.initialize({ ...snapshot(), coreGenerationId: "generation-4" });
  await controller.refreshCurrent();
  assert.equal(gets, 1);
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
