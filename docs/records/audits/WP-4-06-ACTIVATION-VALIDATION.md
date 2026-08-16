---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-17
---

# WP-4-06 激活验证

WP-4-05 已 accepted；范围冻结基线为 `b609ab83611ea59e60522de56182787db3427c08`。WP-4-06 现在成为
唯一 active Work Package，但在可执行 Spec、必要 ADR、实施计划和验证设计冻结前，只允许开展调查与文档
工作，不声称截图产品实现已经开始。

范围对应 CAP-015：手动截图、generation 私有受控图像资源、相关设置和平台权限。冻结必须覆盖 legacy
正常/错误路径、Python/Rust/WebView/平台 backend 所有权、command/event/Operation/resource token、
资源失效与清理、权限拒绝和取消、多屏/DPI、macOS 屏幕录制权限、Linux X11 与 Wayland portal，以及
Windows/macOS/Linux 自动与真实应用验收。自动观察、主动互动、提醒和调度仍属于 WP-4-07。
