---
kind: plan
status: active
audience: maintainer
source_of_truth: ../../plans/runtime-v2/work-packages.md
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03B Windows Composition 实时玻璃 PoC 计划

## 实施范围

1. 新增 Windows 专用 glass backend，封装开关、初始化、对象持有和稳定错误分类。
2. 从 Tauri 主窗口取得 HWND，在 UI 线程初始化 Compositor、DesktopWindowTarget 与 host backdrop visual。
3. 用窗口客户区 visual 完成最小层级验证，并在 WebView 增加仅 PoC 模式使用的透明样式钩子。
4. 增加纯逻辑测试、Windows 编译检查和实机观察说明。
5. 运行 Harness 自动门；自动通过后进入 `stabilizing`，等待项目负责人人工验证技术 Gate。

## 退出条件

- 显式开关可稳定启用，关闭时不改变既有路径。
- 初始化失败不影响 Shell/Core 生命周期。
- 自动 profile 全绿，且不存在 allowlist 外变更。
- 项目负责人完成规范中的实机观察前，不得声明 PoC accepted 或架构已工程验证。

## 回退

关闭 `SAKURA_WINDOWS_GLASS_POC` 即恢复原产品表现。代码级回退按以下顺序执行：移除前端 PoC 样式钩子，
移除 setup 接线，再移除 Windows backend 和依赖 feature；不得用截图实现替代。

## 风险控制

- 不修改 `data/**`、`characters/**`、`third_party/**`、Python Core 或 Legacy Qt。
- 不启用捕获排除，不安装新 crate，只扩展仓库已固定的 `windows` crate feature。
- Windows Composition 对象由 Shell 生命周期持有，避免 setup 返回后释放。
- 如果 WebView2 始终遮挡原生 visual，记录失败并停止，不扩大为窗口架构重写。
