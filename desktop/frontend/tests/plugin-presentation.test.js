import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  presentPluginReason,
  presentPluginStatus,
  projectPluginActivity,
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

test("plugin activity projects active semantic status without relying on plugin ids", () => {
  const plugin = (state) => ({
    state: "active",
    sections: [{
      fields: [{ key: "health", type: "status", value: { state, label: state, message: "detail" } }],
      values: { health: { state, label: state, message: "detail" } },
    }],
  });

  assert.deepEqual(projectPluginActivity(plugin("working")), {
    state: "working",
    label: "working",
    message: "detail",
    hasRunningResource: false,
    isTransient: true,
  });
  assert.deepEqual(projectPluginActivity(plugin("ready")), {
    state: "ready",
    label: "ready",
    message: "detail",
    hasRunningResource: false,
    isTransient: false,
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

test("plugin activity recognizes running resources and tolerates missing status fields", () => {
  const running = projectPluginActivity({
    state: "active",
    sections: [{
      fields: [{ key: "model", type: "resource" }],
      values: { model: { taskState: "running" } },
    }],
  });
  assert.equal(running.state, "neutral");
  assert.equal(running.hasRunningResource, true);
  assert.equal(running.isTransient, true);

  assert.deepEqual(projectPluginActivity({ state: "active", sections: [] }), {
    state: "neutral",
    label: "",
    message: "",
    hasRunningResource: false,
    isTransient: false,
  });
});

test("plugin page keeps enablement quiet and reserves status copy for failures", () => {
  const markup = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

  assert.match(markup, /插件拥有与 Sakura 相同的本机权限，仅安装你信任的插件。/);
  assert.match(markup, /id="pluginInstallMenuButton"[^>]*aria-haspopup="menu"/);
  assert.match(markup, /从 ZIP 安装…/);
  assert.match(markup, /从文件夹安装…/);
  assert.match(settings, /安装、启用和设置插件/);
  assert.match(settings, /toggle\.setAttribute\("role", "switch"\)/);
  assert.match(settings, /function pluginHasExceptionalStatus\(plugin\)/);
  assert.match(settings, /status\.message \|\| status\.diagnostic/);
  assert.doesNotMatch(markup, /pluginStatusStrip|pluginStatusFilter/);
  assert.doesNotMatch(settings, /is-pending|\["运行状态"|\["启用状态"|metaRows\.push\(\["保存后"/);
  assert.doesNotMatch(settings, /active \/ ACTIVE/);
  assert.doesNotMatch(settings, /插件会在当前 Core 内局部启停/);
});

test("plugin install menu has keyboard, escape, and outside-dismiss behavior", () => {
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

  assert.match(settings, /pluginInstallMenuButton\.addEventListener\("keydown"/);
  assert.match(settings, /pluginInstallMenu\.addEventListener\("keydown"/);
  assert.match(settings, /event\.key === "Escape"/);
  assert.match(settings, /event\.key === "ArrowDown"/);
  assert.match(settings, /document\.addEventListener\("pointerdown"/);
  assert.match(settings, /document\.addEventListener\("focusin"/);
});
