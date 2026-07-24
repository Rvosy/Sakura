# WP-1P-06：三平台最小 Shell + Core lifecycle 总门

> 状态：Active
> 日期：2026-07-24
> 前置：WP-1P-05 accepted，提交 `63a4106`
> 规范来源：ADR-0001、ADR-0003、ADR-0004、WP-1P-01/02/03/04/05

## 1. 结果边界

本 Work Package 把共享应用锁、RuntimeLocator、受控进程树、窗口 backend 和现有最小
Python Core Host 组合为同一套三平台生命周期总门。每个原生 runner 执行：

```text
shared lock -> explicit RuntimeLocator -> controlled Core tree -> hello
-> initialize/readiness -> health -> Snapshot -> protocol shutdown
-> full tree/pipes/fd/handles/temp cleanup -> release lock -> reacquire lock
```

只验证当前最小 Core 已有的公共 lifecycle 接口；不伪造插件、MCP、TTS、浏览器或 Assistant
后代已经完成产品级排水。

## 2. 允许目录与非目标

允许修改：`desktop/src-tauri/src/core_host_runtime.rs`、`main.rs` 的最小 debug/acceptance
接线、platform contracts/backend 测试、三平台 acceptance fixture、platform foundation
workflow、本文、ADR-0004 和 Work Package 总计划。不得修改真实 `data/`、`runtime/`、
角色、插件、`.superpowers/`、共享数据 schema、产品 Assistant/MCP/TTS 语义或发布 workflow。

## 3. 故障矩阵

必须覆盖 Runtime 缺失、共享锁冲突、RuntimeLocator fatal、spawn/pipe fatal、hello/initialize
超时、health/Snapshot 响应、正常 shutdown、Core crash、Core hang、忽略 shutdown、根退出但
后代存活、旧 generation barrier、app shutdown during spawn/initialize、Tauri 强杀、锁释放
后立即重新获取，以及 pipe/fd/handle/进程树/临时目录/隔离清单零残留。

Linux 安装依赖、下载、构建和测试均使用分钟级 timeout 与有界重试；concurrency 必须取消旧
run。diagnostics 记录 CI session/compositor 元数据，但不把 Xvfb 当真实设备验收。

## 4. 退出条件

- Windows x64、macOS arm64、Linux x64 最新 HEAD 原生编译和最小 lifecycle 全绿；
- shared lock、RuntimeLocator、正式 process/window/diagnostics backend 组合成功；
- 正常/异常/强杀路径资源零残留，旧 generation 未完全退出前不得创建新 generation；
- Unit/UI 全绿，P0/P1 为 0，真实 `data/` 与 `runtime/` 清单前后不变；
- accepted 记录明确 CI 已验证项和 macOS/X11/Wayland device validation deferred；
- ADR-0004 仅更新为 `Technically Validated for CI platform foundation`，不得写完整产品
  `Accepted`。

## 5. 独立回退

整体回退本 WP 的 activation、lifecycle harness、workflow、accepted 记录和 ADR 更新即可
恢复 WP-1P-05 accepted；不得回退 WP-1P-01 至 WP-1P-05 的独立 backend 或删除用户数据。
