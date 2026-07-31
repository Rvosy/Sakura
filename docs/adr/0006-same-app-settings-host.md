---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# ADR-0006：设置窗口属于同一 Tauri App 和同一生命周期根

> 状态：Accepted
> 决策日期：2026-07-27
> 适用范围：Runtime v2 桌宠菜单、设置窗口、设置前端资产和应用退出协调

## 背景

legacy Qt 通过独立 `sakura-settings` Tauri App 和 PySide6 wrapper 启动设置窗口。该结构依赖
stdin/stdout HostRpc、外部进程检测、Qt `QProcess`/`QThread` 适配以及单独的退出语义。Runtime v2
已经由 ADR-0001 确立单一桌面生命周期根，继续把设置作为独立产品进程会重新引入第二窗口宿主、
第二进程状态和跨进程退出协调。

同时，Runtime v2 和 legacy Qt 在过渡期都需要设置界面。如果两个宿主分别手工维护完整
`index.html`、JavaScript 和样式，页面行为与安全门控会持续漂移。

## 候选方案

### 方案 A：同一 Sakura Tauri App 内的单例设置窗口

由主 Tauri App 同时拥有 `pet`、最多一个 `settings` WebViewWindow 和同一受监管 Python Core。
Rust 统一处理菜单、窗口、dirty close 和应用退出；两个宿主消费一份 canonical settings frontend。

### 方案 B：继续启动独立 `sakura-settings` App

复用旧设置进程的 stdin/stdout HostRpc 和进程级 `AppState`。该方案保留第二个产品进程、独立退出和
跨进程状态同步。

### 方案 C：继续由 PySide6 wrapper 托管设置进程

复用 `app/ui/tauri_settings.py` 中的 `QProcess`、`QObject`、`QThread`、`QTimer` 和
Signal/Slot。该方案把 Qt 生命周期重新带入 Runtime v2 产品入口。

canonical frontend 的位置另比较了两个可行布局：直接以 `desktop/frontend/settings/**` 为规范源，
或抽取独立共享目录。实施选择前者，legacy 设置工具只机械消费同一资源。

## 决策

采用方案 A：

- 一个 Sakura Tauri App 同时拥有 `pet`、最多一个 `settings` 窗口和同一 Python Core。
- Rust 是菜单 allowlist、窗口创建/聚焦/销毁、未保存拦截和应用退出协调的唯一所有者。
- 关闭 `settings` 不退出 App 或关闭 Core；App 退出统一协调 settings、Core 和全部窗口。
- Core 崩溃不销毁设置窗口；设置窗口根据能力清单显示可用、只读或不可用状态。
- Runtime v2 不启动独立 `sakura-settings` 子进程，也不复用其 HostRpc 作为产品边界。
- `desktop/frontend/settings/**` 是设置前端的 canonical source；legacy 独立设置宿主可以保留，
  但必须机械消费同一资源，不能维护第二份完整资产。
- legacy 独立设置工具和 Qt 回退入口的最终移除不属于本决策；在回退期内它们继续存在。

设置窗口 command、capability manifest、关闭状态机和验收条件继续由
[`WP-3U-01` spec](../specs/runtime-v2/WP-3U-01-same-app-settings-window.md) 约束。

## 后果

收益：

- 设置、桌宠和 Core 共用一个桌面生命周期根，不需要第二套进程监管或退出协议。
- 重复打开、最小化恢复、dirty close 和主应用退出可以由一个 Rust 状态机协调。
- Runtime v2 与 legacy 设置宿主共享前端规范源，降低资产和交互漂移。

代价：

- 主 Tauri App 必须承担 secondary window、WebView 故障、未保存确认和 Core generation 变化的协调。
- legacy 设置工具需要机械暂存或构建适配，不能再独立修改完整前端资产。
- 同 App 宿主不意味着全部设置已经迁移；未完成 feature 仍必须失败安全门控。

## 状态与后续变更

WP-3U-01 已完成单例设置窗口、canonical frontend 和退出协调验收，因此本决策为 `Accepted`。
后续桌宠菜单从原生表现改为 WebView 自绘没有改变 Rust 的能力、动作和生命周期所有权，不构成对本
ADR 的取代。

执行状态和验收证据只见
[`work-packages.md`](../plans/runtime-v2/work-packages.md) 与 records。若未来重新采用独立设置产品
进程或改变窗口生命周期根，应创建新 ADR 并 `supersedes: ADR-0006`。
