---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-18
---

# WP-4-06 截图与受控图像资源实施计划

## 实施顺序

1. 冻结 Legacy Qt 的框选、混合 DPI 裁剪、多模态消息、视觉摘要和错误路径，并拒绝旧 Tauri 裸路径 DTO。
2. 建立 generation 私有截图 registry、Core 单次读取校验、`assistant.screen-capture-v1` 协议和聊天附件消费。
3. 建立每显示器覆盖层和跨平台捕获；覆盖权限拒绝、portal 取消、显示器变化、负坐标与 DPI。
4. 输入栏接入 `+`、下方单项截图菜单、附件状态、替换/释放和发送失败保留；同步原生交互区域。
5. 补齐 Python/Rust/WebView 回归、fault injection、Harness journey 和 Windows/macOS/Linux 实机验收脚本。

## 候选与回退

自动门全绿后进入 stabilizing，不代填真实屏幕权限与多屏设备证据。独立回退先关闭
`assistant.screen-capture-v1` 和 `+` 菜单，关闭所有覆盖层、释放 Core 内存附件并删除 generation 临时目录；
聊天、历史和其他 capability 保持不变，不删除任何用户持久数据。

