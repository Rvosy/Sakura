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

基线实现从 Tauri 主窗口取得 HWND，在 setup/UI 线程创建 `Compositor`、`DesktopWindowTarget`、填满
客户区的 `HostBackdropBrush` sprite visual 和低透明 Sakura tint visual，并持有全部 Composition 对象
直到 Shell 退出。默认关闭，不执行截图、窗口显隐循环或捕获排除。

为排除主题底色造成的观感干扰，曾加入 A/B/C 调试块。实机确认 CSS `backdrop-filter` 只能模糊
WebView 内角色，不能采样桌面程序，因此最终候选已移除调试块与输入框 CSS blur，改由位于主窗口
正后方的原生辅助 HWND 提供桌面背景。该辅助 HWND 能完成 WinRT/DispatcherQueue/Composition 初始化，
也能与输入框矩形同步，但实机只绘制黑色区域；移动 Sakura 后还会短暂留下黑色残影。因此候选失败，
进程已关闭，辅助 HWND、线程、布局同步、透明实验样式和 Gaussian 依赖均已从工作树撤回。

## A/B 实机结果

| 场景 | 观察结果 |
|---|---|
| 正常接入 `DesktopWindowTarget` | 项目负责人现场确认角色、气泡与输入框仍由 WebView 正常绘制，气泡透明区域能透出窗口后方内容 |
| CSS Gaussian 候选 | A/B/C 实机对照证明只模糊 WebView 内的角色立绘，桌面应用文字不受影响 |
| 辅助 HWND Gaussian 候选 | 日志报告 active，窗口尺寸和位置正确，但只输出黑色区域，移动后出现黑色残影；未通过视觉 Gate，代码已撤回 |
| 强制原生初始化失败 | 同一二进制继续启动，角色、气泡、输入框、WebView 交互表面完整恢复 |
| 进程与资源 | 两次候选均有正常 Core Host 和 WebView2 子进程；均通过应用关闭请求退出，没有强杀 |

早期局部截图保留在被忽略的本机 `temp/`，但该截图不足以判断窗口完整层级，且当时存在默认 target
与独立 PoC target 实例混淆。后续以可执行文件路径核对实例，并以项目负责人现场观察作为视觉 Gate
证据。截图不是发布资产，也不进入 Git。

## 结论边界

当前证据证明 `DesktopWindowTarget` 直接接入当前 Tauri 顶级 HWND 时，WebView 角色与控件仍可见，
透明样式也能生效；但 CSS backdrop 只采样 WebView 内部像素，辅助 HWND 上的 HostBackdrop/Gaussian
候选又只输出黑色并残影。因此当前 Tauri/WebView2 HWND 结构没有通过“输入框实时桌面高斯”技术 Gate。
后续若继续，需要新的宿主边界，例如 WebView2 Composition Controller/root visual；不能把本轮候选
描述为可用，也不能回退到连续截图。

本记录不是项目负责人验收声明，Work Package 状态仍以
[总计划](../../plans/runtime-v2/work-packages.md) 为唯一真相源。
