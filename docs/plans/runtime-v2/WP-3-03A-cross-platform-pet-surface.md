---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-08
---

# WP-3-03A 跨平台桌宠动态表面实施计划

1. 冻结 schema v3、逻辑 surface snapshot、动态包络和锚点换算，以纯前端/Rust 测试锁定。
2. 合并布局、alpha、缩放、菜单和表情过渡为单一 revision transaction，前端只在原生成功后提交画面。
3. 保留并加固 Windows region，新增 macOS 光标路由和 Linux GTK/GDK input shape。
4. 扩展 Harness profile 与 Windows 门，补 macOS、X11/XWayland、native Wayland 分列验收和诊断证据。
5. 自动门通过后写验证 record 并进入 stabilizing；负责人实机验收前不标记 accepted。

每阶段失败均保留上一版有效 surface。整体回退恢复固定窗口实现，不触碰用户数据和角色资源。
