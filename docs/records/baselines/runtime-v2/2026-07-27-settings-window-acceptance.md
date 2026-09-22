---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# 2026-07-27 设置窗口实现与验收记录

以下保留对应日期的实现、验证和失败记录。当前契约见[WP-3U-01-same-app-settings-window](../../../specs/runtime-v2/WP-3U-01-same-app-settings-window.md)。

## 激活与实现记录（2026-07-27）

- 前置 WP-3-03 已由项目负责人验收并标记 `accepted`，本 WP 随即激活。
- 单一规范源冻结为方案 1：`desktop/frontend/settings/**` 是 canonical source；legacy
  `tools/settings-tauri` 直接从该目录构建，不保留第二份完整资产。
- Rust 已拥有原生产品菜单、封闭 menu item allowlist、唯一 `settings` 窗口、重复打开聚焦、
  CloseRequested/放弃确认和 5 秒有界主应用退出协调。
- Windows 原生命中区域在立绘解码后按 `object-fit: contain` 的真实外框与 PNG alpha 蒙版收紧；
  图片外侧留白及 PNG 画布内部全透明像素均不再拦截鼠标，气泡、输入框、按钮与实际可见立绘仍保持
  可交互和可右键。
- capability manifest 当前不开放任何可写 section；未迁移页面稳定禁用，前端不读取或保存用户配置。
- 本地 frontend 与 Rust 全量自动测试通过，进入 `stabilizing`；Windows 真实菜单位置、100%/150% DPI、
  最小化/聚焦、中文 IME、重复打开和退出仍需形成候选验收证据后才能 `accepted`。

本 WP 只迁移窗口宿主、菜单入口、设置前端外壳和能力门控。角色/外观配置的真实读取与保存属于
WP-3U-02；其他设置功能按 `docs/specs/runtime-v2/settings-incremental-migration.md` 的 feature 级顺序跟随
对应能力 WP，不能集中恢复旧 HostRpc 或无后端开放页面。


## 接受记录（2026-07-27）

> 本节保留当时的验证事实、风险接受与回退方案，不是当前开发步骤。

- 自动测试：实现与稳定化提交依次通过 frontend 全量测试（最终 70 passed）、Runtime Rust 全量测试
  （最终 195 passed、23 ignored）、Harness smoke（2/2）、locked debug build、`cargo fmt --check`、
  `git diff --check`；legacy settings 宿主 `cargo check --locked` 通过，canonical frontend freshness 和
  capability/secret 边界测试通过。最终候选 `7a58bdc0a` 的 Runtime v2 platform foundation run
  `30236383550` 在 Windows x64、macOS arm64、Linux x64 全绿，三平台均执行 frontend 测试和 native
  Tauri build；Test run `30236383605` 的 Unit/UI jobs 全绿。
- Windows 手动验收：项目负责人已确认真实右键菜单位置和操作、设置窗口创建/最小化/重新聚焦、
  中文 IME、未保存关闭确认、快速重复打开保持单实例、关闭设置不退出桌宠或 Core，以及主程序正常
  退出和资源清理全部通过。
- DPI 设备证据：本轮明确未执行 100%/150% DPI 人工验收，不记录为通过，也没有观察到可复现产品
  缺陷。项目负责人按 G-008 明确接受这项非失败型设备证据风险；真实 Tauri WebView/菜单复验点已
  登记到 WP-7-02。若后续发现可复现且可归因于本候选实现的 DPI 缺陷，必须重新打开 WP-3U-01。
- 数据与安全：本 WP 未开放任何可写设置 section，未读取或保存角色、Provider、TTS、Memory、MCP、
  插件、凭据或其他用户配置；设置 WebView 没有直接写入 `data/**`。未发现 P0/P1、退出条件缺陷、
  凭据泄露或第二生命周期根。
- 已知风险：除上述 Windows DPI 设备证据外，macOS/Linux 真实菜单、焦点、IME 和 compositor 设备
  体验仍按既定范围留在 WP-7-02；公共 Rust/frontend 构建证据已经由同一候选三平台 CI 通过。
- 回退：依次 revert 本 WP 的 Windows 稳定化修复和 `6058bac06`，移除 Runtime v2 product menu、
  settings window、capability shell 与 canonical frontend 接线，恢复 WP-3-03 固定产品 UI；保留旧
  独立设置工具和 legacy Qt 入口，不删除、恢复或改写用户配置。
