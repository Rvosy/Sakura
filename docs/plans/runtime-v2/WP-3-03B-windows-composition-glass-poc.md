---
kind: plan
status: accepted
audience: maintainer
source_of_truth: ../../plans/runtime-v2/work-packages.md
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03B Windows Composition 实时玻璃 PoC 计划

## 实施范围

1. 先用独立纯 Win32 验证器设置 `DWMWA_USE_HOSTBACKDROPBRUSH=TRUE`，初始化 DispatcherQueue、
   Compositor、DesktopWindowTarget 与 host backdrop visual，不启动 Tauri/WebView2/Core。
2. Gate 1 实机确认实时桌面采样后，在同一验证器加入 Gaussian effect graph 完成 Gate 2。
3. Gate 1/2 通过后，在 Tauri/Wry 顶级 HWND 复用 visual graph，并把两个原生 visual 分别裁剪到气泡
   和输入框；不堆叠辅助 HWND，也不在已通过时扩大为 Composition Controller 重写。
4. 保留 Windows 专用 glass backend 的开关、失败降级和稳定错误分类，但不把缺少必要 DWM 属性的
   首轮黑块记录作为 HostBackdrop 失败证据。
5. 在角色 alpha mask 改变最终 `activeBounds` 后同步重算原生 region 的 surface-local 坐标；同一局部
   几何的拖动完成不重复提交 HWND/region，避免松手闪影。
6. 增加纯逻辑测试、Windows 编译检查和实机观察说明。
7. 运行 Harness 自动门；自动通过后进入 `stabilizing`，等待项目负责人人工验证技术 Gate。

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
- 不启用捕获排除，不引入锁文件之外的新 crate；仅扩展已固定的 `windows` feature，并显式复用其
  已锁定的 `windows-numerics` 类型依赖。
- Windows Composition 对象由 Shell 生命周期持有，避免 setup 返回后释放。
- 鲜艳粉色 tint 只用于验证 region 覆盖边界，最终产品配色需在负责人验收后另行收敛。
