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
WebView 内角色，不能采样桌面程序。首轮辅助 HWND 黑块实验漏设
`DWMWA_USE_HOSTBACKDROPBRUSH=TRUE`，不能作为 HostBackdrop 失败证据；辅助 HWND 方案随后删除。

补齐 DWM 属性后，纯 Win32 Gate 先后通过 HostBackdrop 实时采样与 Gaussian Gate。相同链路随后直接
接入 Tauri/Wry 顶级 HWND：`HostBackdropBrush -> Border clamp -> GaussianBlur`，WebView 继续绘制
角色、文字和控件。全窗口 visual 曾造成角色透明边缘轮廓和拖动残影，因此最终改为两个圆角 region，
只覆盖气泡与输入框。拖动完成若局部 surface 几何未变化，则复用既有 region，避免冗余
`SetWindowPos + SetWindowRgn` 触发右侧闪影。

鲜艳半透明粉色诊断进一步暴露了左侧约 37 px 漏带。启动日志先记录
`active=[0,0,900,994] bubble=[154,614,592,147] offset=[154,614]`；角色 alpha mask 加载后，最终 HWND
收缩为 `active=[37,0,797,994]`，WebView 已按新原点左移，而原生 region 未重算。把 region 更新接入
角色命中激活/收敛事务后，日志变为 `offset=[117,614]`，与最终 DOM 气泡左边一致。

## A/B 实机结果

| 场景 | 观察结果 |
|---|---|
| 正常接入 `DesktopWindowTarget` | 项目负责人现场确认角色、气泡与输入框仍由 WebView 正常绘制，气泡透明区域能透出窗口后方内容 |
| CSS Gaussian 候选 | A/B/C 实机对照证明只模糊 WebView 内的角色立绘，桌面应用文字不受影响 |
| 纯 Win32 HostBackdrop/Gaussian Gate | 设置必需 DWM 属性后可实时采样桌面并完成 Gaussian blur |
| Tauri/Wry 顶级 HWND | WebView 内容保持在上层，气泡和输入框 region 能实时模糊桌面，无需辅助 HWND 或 Composition Controller |
| region 与拖动 | 最终 surface origin 从 0 变为 37 后原生 offset 同步从 154 变为 117；保持拖动与松手截图覆盖一致，无右侧闪影 |
| 强制原生初始化失败 | 同一二进制继续启动，角色、气泡、输入框、WebView 交互表面完整恢复 |
| 进程与资源 | 两次候选均有正常 Core Host 和 WebView2 子进程；均通过应用关闭请求退出，没有强杀 |

最终候选由 `target/debug/sakura-runtime-v2-shell.exe`、`SAKURA_WINDOWS_GLASS_POC=1` 启动并自动
截取窗口。覆盖同步截图为本机 `temp/glass-final-origin-sync.png`，SHA-256
`CC54DE61E13966EAA7FC88DB835B29ED0810C0B54C985BCCDCE2DD8946A26D21`；拖动保持和松手截图分别为
`temp/glass-drag-held.png` 与 `temp/glass-drag-after.png`。截图是被忽略的本机审查证据，不进入 Git。

## 结论边界

当前证据证明 `DesktopWindowTarget` 可直接接入当前 Tauri 顶级 HWND，WebView 角色与控件仍可见，
气泡和输入框能采样并高斯模糊桌面程序；不需要辅助 HWND、截图或 Composition Controller。鲜粉诊断
确认最终左边界覆盖完整，自动拖动保持/松手截图未复现右侧闪影。100% DPI 的实现观察已完成；规范中
其余人工 Gate（包括 150% DPI、动态背景、截图与强制失败的负责人复验）仍需项目负责人确认。

同日 `runtime\python.exe -m harness verify WP-3-03B` 生成
`temp/harness/20260812T202114.331061Z-WP-3-03B.json`：8/8 自动 case 通过、0 failed、0 blocked，返回
`manual_pending`。该结果支持候选进入 `stabilizing`，不构成人工 `accepted`。

以上内容记录负责人声明前的自动验证事实；当时尚未形成人工验收结论。Work Package 状态始终以
[总计划](../../plans/runtime-v2/work-packages.md) 为唯一真相源。

## 2026-08-13 项目负责人验收

2026-08-13，项目负责人在当前开发会话中明确声明：

> 现在WP-3-03B 我验收通过可以accept了

该声明关闭 WP-3-03B 的人工 Gate，并授权把该 Work Package 标记为 `accepted`。负责人接受的候选为
`1be1106e7c9bff5a4d6c814ccc654e238cf0f07e`；其 Windows 实机证据和边界见本文前述记录。

状态切换前在 macOS 上使用仓库运行时再次执行
`runtime/bin/python3.12 -m harness verify WP-3-03B`，报告为
`temp/harness/20260813T023554.019857Z-WP-3-03B.json`：8/8 自动 case 通过、0 failed、0 blocked，
返回 `manual_pending`。该返回值是负责人声明写入前的正确历史结果，不倒改为自动 accepted。

本记录只保存负责人明确给出的验收结论，不补写未提供的设备、逐项操作或额外测试结果。Work Package
当前状态仍只维护在[总计划](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个状态真相源；
本次验收不预先接受恢复为 `stabilizing` 的 WP-4-01B 或任何后续工作包。
