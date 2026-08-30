---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
status_source: self
updated: 2026-08-23
---

# WP-3-03C Windows 输入栏实时高斯玻璃产品化计划

## 实施阶段

1. 冻结 task v2、ADR、规范、计划与初始审计记录；暂停 WP-4-04 候选但不回滚其代码或证据。
2. 扩展 UI 配置、Appearance v3 和 capability manifest，完成偏好/平台有效模式分离。
3. 将 PoC 后端收敛为默认初始化、仅输入栏、运行时可切换且失败安全的 Windows backend。
4. 按旧版层级复刻高斯输入栏 CSS，移除气泡覆盖和诊断样式。
5. 完成 Rust、前端和兼容测试，运行 required profiles，并把实际结果追加到审计记录。
6. 自动门全绿后把候选推进 `stabilizing`；等待项目负责人完成 Windows 实机视觉验收。

## 回退

运行时回退是选择“纯色块”；原生初始化失败也走同一路径且不重写偏好。代码回退依次移除前端模式接线、
恢复 Appearance v2、移除产品化 backend 接线；不得退回截图、辅助 HWND 或全窗口 visual。

WP-4-04 的实现、测试和证据在本包期间冻结。只有 WP-3-03C 被项目负责人 accepted 并明确批准后，才可
把 WP-4-04 的固定 `base_ref` 单向前移到其原 base 的后代提交并恢复执行。

## 风险控制

- 不修改 `data/**`、`characters/**`、Legacy Qt、插件实现、`third_party/**` 或 `tools/mcp/**`。
- 不增加强度、透明度或 tint 设置。
- 已存在的 `desktop/frontend/app.js` 与 `index.html` 换行痕迹不得冒充本包功能变更。
- 自动截图可用于观察证据，但不得成为产品背景输入；人工验收结论只能由项目负责人填写。
