import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  presentPluginReason,
  presentPluginStatus,
} from "../settings/plugin-presentation.js";

test("plugin status uses plain language without diagnostic codes for routine states", () => {
  assert.deepEqual(presentPluginStatus({ state: "active", reasonCode: "ACTIVE" }), {
    label: "运行正常",
    message: "",
    diagnostic: "",
  });
  assert.deepEqual(presentPluginStatus({ state: "disabled", reasonCode: "PLUGIN_DISABLED" }), {
    label: "已停用",
    message: "",
    diagnostic: "",
  });
});

test("plugin status explains known failures and keeps diagnostics", () => {
  assert.deepEqual(presentPluginStatus({
    state: "failed",
    reasonCode: "API_VERSION_UNSUPPORTED",
  }), {
    label: "版本不兼容",
    message: "这个插件版本与当前 Sakura 不兼容，无法使用。",
    diagnostic: "诊断代码：API_VERSION_UNSUPPORTED",
  });
  assert.deepEqual(presentPluginStatus({
    state: "failed",
    reasonCode: "MISSING_SERVICE",
  }), {
    label: "缺少所需组件",
    message: "缺少运行所需的组件，暂时无法使用。",
    diagnostic: "诊断代码：MISSING_SERVICE",
  });
  assert.equal(presentPluginStatus({
    state: "failed",
    reasonCode: "SERVICE_CONFLICT",
  }).label, "与其他插件冲突");
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

test("plugin settings reasons use the same presentation rules", () => {
  assert.equal(presentPluginReason("READY"), null);
  assert.equal(
    presentPluginReason("SETTINGS_LOAD_FAILED").diagnostic,
    "诊断代码：SETTINGS_LOAD_FAILED",
  );
});

test("plugin page uses the plain-language presentation instead of raw runtime states", () => {
  const markup = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

  assert.match(markup, /插件可访问你的文件和网络，只安装你信任的来源。/);
  assert.match(markup, /从 ZIP 安装/);
  assert.match(settings, /安装、启用和设置插件/);
  assert.match(settings, /\["运行状态", status\.label\]/);
  assert.doesNotMatch(settings, /active \/ ACTIVE/);
  assert.doesNotMatch(settings, /插件会在当前 Core 内局部启停/);
});
