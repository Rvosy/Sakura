import assert from "node:assert/strict";
import test from "node:test";

import {
  disabledRequiredPluginProviders,
  enabledPluginDependents,
  presentPluginComponent,
  presentPluginReason,
  presentPluginStatus,
  projectPluginActivity,
  pluginMetadata,
  pluginIconName,
  pluginResourceContributions,
  filterPluginCatalog,
} from "../settings/plugin-presentation.js";

test("component overview combines enabled plugin resources across surfaces and keeps published installation state", () => {
  const section = (surface, value) => ({
    sectionId: "bundle", surface, values: { bundle: value },
    fields: [{ key: "bundle", label: "组件", type: "resource", value: { ready: false } }, { key: "status", type: "status" }],
  });
  const installed = { ready: true, applicability: "required", taskState: "idle" };
  const running = { ready: false, applicability: "required", taskState: "running", progress: 42 };
  const plugins = [
    { pluginId: "third-party", name: "A", enabled: true, sections: [section("plugin", installed), { ...section(null, running), sectionId: "second" }] },
    { pluginId: "legacy", name: "B", enabled: true, settings: [section("about", running)] },
    { pluginId: "disabled", name: "C", enabled: false, sections: [section("plugin", installed)] },
    { pluginId: "no-resource", name: "D", enabled: true, sections: [{ fields: [{ key: "state", type: "status" }] }] },
  ];
  const resources = pluginResourceContributions(plugins);
  assert.deepEqual(resources.map(({ plugin, section, value }) => [plugin.pluginId, section.sectionId, value]), [
    ["third-party", "bundle", installed], ["third-party", "second", running], ["legacy", "bundle", running],
  ]);
  assert.equal(pluginResourceContributions([{ ...plugins[0], enabled: false }]).length, 0);
});

test("plugin icons use the local catalogue and fall back without interpreting supplied markup", () => {
  assert.equal(pluginIconName({ presentation: { category: "connectivity", icon: "smartphone" } }), "smartphone");
  for (const icon of [undefined, "future-icon", "../brain.svg", '<svg onload="alert(1)">']) {
    assert.equal(pluginIconName({ presentation: { category: "memory", icon } }), "brain");
  }
  assert.equal(pluginIconName({ presentation: { kind: "infrastructure", category: "voice" } }), "layers");
  assert.equal(pluginIconName({ plugin_id: "sakura_mem0" }), "puzzle");
});

test("catalog filters combine declared metadata, install source and actual activity", () => {
  const plugins = [
    { id: "one", name: "Voice", author: "作者", source: "user", state: "active", presentation: { kind: "provider", category: "voice" } },
    { id: "two", name: "Hub", source: "bundled", state: "failed", presentation: { kind: "infrastructure", category: "voice" } },
    { id: "three", name: "Old plugin", source: "user", state: "disabled" },
  ];
  assert.deepEqual(filterPluginCatalog(plugins, { query: "作者", category: "voice", source: "user", kind: "provider" }).map((plugin) => plugin.id), ["one"]);
  assert.deepEqual(filterPluginCatalog(plugins, { status: "problem" }).map((plugin) => plugin.id), ["two"]);
  assert.deepEqual(filterPluginCatalog(plugins, { status: "disabled" }).map((plugin) => plugin.id), ["three"]);
  assert.deepEqual(filterPluginCatalog(plugins, { category: "model" }), []);
  assert.deepEqual(pluginMetadata({ id: "sakura.tts", provides: ["sakura.tts"], required: true }), { kind: "extension", category: "other" });
  assert.deepEqual(pluginMetadata({ presentation: { kind: "unknown", category: "unknown" } }), { kind: "extension", category: "other" });
});



test("plugin dependency projections cascade enablement and find affected consumers", () => {
  const hub = {
    id: "hub-install", plugin_id: "sakura.tts", name: "Sakura TTS Hub",
    enabled: false, supported: true, provides: ["sakura.tts"], requires: [],
  };
  const provider = {
    id: "provider-install", plugin_id: "sakura.tts.provider", name: "TTS Provider",
    enabled: false, supported: true, provides: ["sakura.voice"], requires: ["sakura.tts"],
  };
  const consumer = {
    id: "consumer-install", plugin_id: "sakura.voice.consumer", name: "Voice Consumer",
    enabled: true, supported: true, provides: [], requires: ["sakura.voice"],
  };
  const plugins = [consumer, provider, hub];

  assert.deepEqual(
    disabledRequiredPluginProviders(consumer, plugins, {
      "consumer-install": true, "provider-install": false, "hub-install": false,
    }).map((plugin) => plugin.plugin_id),
    ["sakura.tts", "sakura.tts.provider"],
  );
  assert.deepEqual(
    enabledPluginDependents(hub, plugins, {
      "consumer-install": true, "provider-install": true, "hub-install": true,
    }).map((plugin) => plugin.plugin_id),
    ["sakura.voice.consumer", "sakura.tts.provider"],
  );
  assert.equal(
    presentPluginComponent("sakura.tts", plugins),
    "Sakura TTS Hub（sakura.tts）",
  );
});

test("unknown plugin failures stay readable and retain the original code", () => {
  assert.deepEqual(presentPluginStatus({
    state: "failed",
    reasonCode: "SOMETHING_NEW",
  }), {
    label: "启动失败",
    message: "这个插件暂时无法使用。",
    diagnostic: "诊断代码：SOMETHING_NEW",
  });
});



test("plugin activity keeps warning and failure stable", () => {
  const warning = projectPluginActivity({
    state: "active",
    settings: [{
      fields: [{ key: "status", type: "status" }],
      values: { status: { state: "warning", label: "功能受限", message: "安全说明" } },
    }],
  });
  assert.deepEqual(warning, {
    state: "warning",
    label: "功能受限",
    message: "安全说明",
    hasRunningResource: false,
    isTransient: false,
  });
  assert.equal(projectPluginActivity({ state: "failed", sections: [] }).state, "failed");
  assert.equal(projectPluginActivity({ state: "failed", sections: [] }).isTransient, false);

  assert.deepEqual(projectPluginActivity({
    state: "failed",
    reason_code: "PLUGIN_APPLICATION_NOT_READY",
    sections: [],
  }), {
    state: "working",
    label: "正在启动",
    message: "插件 Worker 正在初始化，请稍候。",
    hasRunningResource: false,
    isTransient: true,
  });

  const error = structuredClone(warning);
  error.state = "error";
  error.label = "运行失败";
  assert.equal(projectPluginActivity({
    state: "active",
    sections: [{ fields: [{ key: "status", type: "status" }], values: { status: error } }],
  }).state, "error");

  const errorWithWorkingDetail = projectPluginActivity({
    state: "active",
    sections: [{
      fields: [
        { key: "primary", type: "status" },
        { key: "detail", type: "status" },
      ],
      values: {
        primary: { state: "error", label: "运行失败", message: "安全说明" },
        detail: { state: "working", label: "旧任务", message: "" },
      },
    }],
  });
  assert.equal(errorWithWorkingDetail.state, "error");
  assert.equal(errorWithWorkingDetail.isTransient, false);
});
