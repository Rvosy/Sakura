---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-22
---

# WP-4-06 截图与受控图像资源本机自动验证记录

## 候选与证据边界

2026-08-22 在 macOS arm64、分支 `refactor/tauri-runtime-v2` 上复核产品候选 `7fc7cc8`。截图实现最初以
`cfba1fd` 保存，当前候选包含之后已经合入的 Runtime v2、Plugin Kernel v3 和窗口表面修复。

本记录只保存本机自动证据。验证全部使用仓库测试 fixture、系统临时日志路径和测试 app root；没有启动
真实截图，没有读取、写入、移动或清理仓库 `data/`。因此本记录不证明 macOS 屏幕录制权限、真实 Retina/
多屏框选、Windows 混合 DPI、Linux X11 或 Wayland portal 行为。

## 已执行结果

- `journey-screen-capture`：3/3 通过，报告
  `temp/harness/20260822T084303.562494Z-journey-screen-capture.json`；Python 8 passed、Rust 4 passed、
  WebView 55 passed，覆盖 generation 私有 JPEG containment、单次消费、DPI 换算、框选、替换、释放和
  opaque attachment ID。
- `runtime-v2-shell`：6/6 通过，报告
  `temp/harness/20260822T084444.528091Z-runtime-v2-shell.json`；完整前端 211 passed，Rust 外观、角色表现、
  产品壳、窗口几何和命中分组共 84 passed。
- `journey-plugins`：3/3 通过，报告
  `temp/harness/20260822T083949.124078Z-journey-plugins.json`；Python 110 passed、Rust 1 passed、前端
  17 passed。
- Core Host/Memory 的首次 profile 运行暴露两处集成测试仍断言旧 `embeddingStatus` 字段；同步为 schema v2
  `embeddingResource` 精确形状后，Memory 定向纵向测试 20/20 通过。该失败与修正事实保留，不把旧报告
  冒充最终全绿报告。
- 完整 Runtime v2 前端 211/211、Plugin Kernel 与 Mem0 Python 46/46、Rust `plugin_settings` 5/5、
  `cargo fmt --check`、Python `compileall` 和 `git diff --check` 通过。
- `docs`：2/2 通过，报告 `temp/harness/20260822T084140.819676Z-docs.json`。

## 尚未完成的门禁

- 当前分支尚未推送，未取得同一产品候选 SHA 的 Windows x64、macOS arm64、Linux x64 CI 结果。
- 未执行 macOS 屏幕录制权限拒绝、授权后恢复、Retina 和真实多屏框选；未捕获任何用户桌面内容。
- 未执行 Windows 100%/150% 混合 DPI、Linux X11、Wayland portal 选择/取消和显示器变化实机清单。
- 未执行项目负责人 owner acceptance。

所以 WP-4-06 继续保持 `active`。当前自动结果支持提交候选和触发三平台 CI，但不能单独改为
`stabilizing` 或 `accepted`；真实平台证据也不得由脚本存在、编译成功或第三方 capture backend 的实现推断
代填。
