---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03B Windows Composition 实时玻璃 PoC 记录

## 2026-08-13 最小直接接入实验

环境为 Windows 11 x64、Tauri 2.11.3、WebView2 151.0.4129.78、`windows` crate 0.61.3。
候选从独立 `temp/glass-poc-target` 构建，避免覆盖当时正在运行的默认 debug 产物。开关为
`SAKURA_WINDOWS_GLASS_POC=1`，失败对照额外设置
`SAKURA_WINDOWS_GLASS_POC_FORCE_FAILURE=1`。

实现从 Tauri 主窗口取得 HWND，在 setup/UI 线程创建 `Compositor`、`DesktopWindowTarget`、填满客户区
的 `HostBackdropBrush` sprite visual 和低透明 Sakura tint visual，并持有全部 Composition 对象直到
Shell 退出。默认关闭，不执行截图、窗口显隐循环或捕获排除。

## A/B 实机结果

| 场景 | 观察结果 |
|---|---|
| 正常接入 `DesktopWindowTarget` | Shell、Core 和 WebView 日志均报告 ready，但主窗口客户区不再绘制角色、气泡或输入框；预期 tint 也不可辨认 |
| 强制原生初始化失败 | 同一二进制继续启动，角色、气泡、输入框、WebView 交互表面完整恢复 |
| 进程与资源 | 两次候选均有正常 Core Host 和 WebView2 子进程；均通过应用关闭请求退出，没有强杀 |

屏幕证据保留在被忽略的本机文件 `temp/glass-poc-foreground.png` 与
`temp/glass-poc-forced-fallback.png`。它们不是发布资产，也不进入 Git。

## 结论边界

本轮证明的是：**把 `Windows.UI.Composition.DesktopWindowTarget` 直接挂到当前 Tauri 顶级 HWND 的
最小接法没有通过 WebView2 层级兼容 Gate**。失败与 Core/角色资源无关，因为强制失败对照立即恢复了
相同 WebView 内容。

本轮没有证明 Windows Composition host backdrop 整体不可行。后续若继续，必须先重新设计宿主边界，
例如评估独立原生子窗口或 WebView2 composition controller/root visual target；这属于新的架构切面，
不得在本 WP 内偷偷扩大。按 ADR-0015，本轮不继续实现 Gaussian effect graph，也不退回连续截图。

本记录不是项目负责人验收声明，Work Package 状态仍以
[总计划](../../plans/runtime-v2/work-packages.md) 为唯一真相源。
