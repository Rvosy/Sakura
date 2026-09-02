import assert from "node:assert/strict";
import test from "node:test";

import {
  disabledRequiredPluginProviders,
  enabledPluginDependents,
  presentPluginComponent,
  presentPluginReason,
  presentPluginStatus,
  projectPluginActivity,
} from "../settings/plugin-presentation.js";



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
