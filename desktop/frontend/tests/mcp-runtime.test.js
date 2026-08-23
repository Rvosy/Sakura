import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createMcpController,
  validateMcpSnapshot,
} from "../settings/mcp-runtime.js";

function snapshot(coreGenerationId = "generation-a", overrides = {}) {
  return {
    schemaVersion: 1,
    desktop: { supported: true, label: "macOS MCP", experimentalText: "实验性功能" },
    desktopEnabled: false,
    configState: "valid",
    reasonCode: "READY",
    servers: [{
      serverId: "macos",
      transport: "stdio",
      enabled: false,
      state: "disabled",
      reasonCode: "SERVER_DISABLED",
      toolCount: 0,
    }],
    windowGeneration: 7,
    coreGenerationId,
    ...overrides,
  };
}

function fixture() {
  const listeners = {};
  const group = { hidden: true };
  const row = {
    hidden: false,
    querySelector() { return null; },
  };
  const toggle = {
    checked: false,
    disabled: false,
    addEventListener(name, listener) { listeners[name] = listener; },
    fire(name) { listeners[name]?.(); },
    closest(selector) {
      return selector === ".settings-group" ? group : selector === ".setting-row" ? row : null;
    },
  };
  const status = { textContent: "", dataset: {} };
  const servers = { textContent: "", replaceChildren(...children) { this.children = children; } };
  return {
    toggle,
    group,
    status,
    servers,
    document: {
      getElementById(id) {
        return { desktopMcp: toggle, mcpStatusStrip: status, mcpServerStatus: servers }[id];
      },
      createElement() { return { className: "", textContent: "" }; },
    },
  };
}

test("unsupported platforms hide the entire desktop MCP settings group", () => {
  const { toggle, group, document } = fixture();
  const controller = createMcpController({
    document,
    invoke: async () => { throw new Error("unexpected call"); },
    onDirty: () => {},
  });

  controller.initialize(snapshot("generation-a", {
    desktop: { supported: false, label: "Desktop MCP", experimentalText: "实验性功能" },
  }));
  assert.equal(group.hidden, true);
  assert.equal(toggle.disabled, true);

  controller.initialize(snapshot());
  assert.equal(group.hidden, false);
  assert.equal(toggle.disabled, false);
});

test("WP-4-03 MCP snapshots are exact, bounded, and contain no private configuration", () => {
  assert.equal(validateMcpSnapshot(snapshot()).coreGenerationId, "generation-a");
  assert.throws(() => validateMcpSnapshot({ ...snapshot(), headers: { Authorization: "private" } }));
  assert.throws(() => validateMcpSnapshot(snapshot("", {})));
  assert.throws(() => validateMcpSnapshot(snapshot("generation-a", {
    servers: [{ ...snapshot().servers[0], command: "private" }],
  })));
});

test("WP-4-03 MCP save hot-applies in the bound generation", async () => {
  const { toggle, document } = fixture();
  const calls = [];
  let restarted = false;
  const controller = createMcpController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_mcp_save") {
        restarted = true;
        return { changePlan: "applied" };
      }
      if (command === "settings_mcp_get" && restarted) {
        return snapshot("generation-a", { desktopEnabled: true });
      }
      throw new Error("unexpected call");
    },
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  toggle.checked = true;
  toggle.fire("change");
  assert.equal(controller.isDirty(), true);

  await controller.save();

  assert.deepEqual(calls[0], ["settings_mcp_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    settings: { desktopEnabled: true },
  }]);
  assert.deepEqual(calls[1], ["settings_mcp_get", undefined]);
  assert.equal(controller.isDirty(), false);
});

test("WP-4-03 opens desktop MCP and wires only the dedicated settings boundary", () => {
  const index = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  const native = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
  const manifest = readFileSync(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");

  assert.match(index, /id="mcpStatusStrip"/);
  assert.match(index, /id="mcpServerStatus"/);
  assert.match(index, /id="desktopMcpGroup"[^>]*hidden/);
  assert.match(settings, /invoke\("settings_mcp_get"\)/);
  assert.match(settings, /runtimeMcpController\.save\(\)/);
  assert.match(native, /async fn settings_mcp_get/);
  assert.match(native, /async fn settings_mcp_save/);
  assert.match(manifest, /"tools\.desktop_mcp"\.to_string\(\)/);
  assert.doesNotMatch(settings, /command.*desktopMcp|headers.*desktopMcp|env.*desktopMcp/);
});
