# WP-1P-06：三平台最小 Shell + Core lifecycle 总门

> 状态：accepted（CI platform foundation）
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

## 6. Accepted 记录（2026-07-24）

激活提交：`abfe0fe`。实现提交：`d331919`。CI/生命周期修正提交：`35f5a30`、
`f71cc68`、`ee3a39e`、`077dab8`、`b88e744`。本 accepted 记录由
`docs(runtime): 接受 Phase 1P CI platform foundation` 独立提交。

最新实现 HEAD `b88e744918c0d84548fbdd43df8b17a0e00a4797` 的证据：

- push platform run `30068988807`：Windows x64、macOS arm64、Linux x64 全绿；
- pull_request platform run `30068990391`：三个原生 target 全绿；
- Unit/UI run `30068990399`：Unit 与 UI 全绿；
- PR #147 保持 Draft，没有合并或转为 Ready。

每个 platform job 都先构建原生 Tauri Shell、校验固定 Runtime archive 和 explicit development /
packaged golden RuntimeLayout，再运行 Core lifecycle 合同及真实 Shell acceptance。真实 Shell
由 Rust composition root 获取共享锁，通过正式 RuntimeLocator 与 ManagedProcessTreeBackend 启动
bundled Python 根进程，完成 `hello -> initialize/readiness -> health -> Snapshot -> protocol
shutdown`；验收同时覆盖第二入口锁冲突、Tauri 强杀后的 OS 保险回收、锁立即重获和完整恢复轮。

资源证据由 backend 的 `tree_empty`、显式 pipe/fd/handle release、Core PID 退出探针、隔离临时目录
删除断言以及 acceptance 前后 `data/`/`runtime/` 路径、类型、长度和逐文件内容 SHA-256 清单固定。
本机 Windows Rust/Cargo 1.97.0 复验为 100 passed、14 ignored，真实 Shell 三轮验收通过，保护
清单 SHA-256 为 `e76a9e8db385df5364679b07194071cada2b78d3da96f30f0b7dca53a099bc1d`；
`abfe0fe..b88e744` 的 tracked diff 也不包含 `data/` 或 `runtime/`。所有测试资源只位于 runner
workspace staged Runtime 或受前缀约束的系统临时目录，完成后目录不存在。

CI 暴露并修复了 Windows 并行 lifecycle 调度、macOS 缺少 GNU `timeout`、Linux runner 中断的
dpkg 状态、POSIX signal 143 与 Windows exit code 93 差异、Windows embeddable Python isolated
`_pth` 导入路径，以及“只编译 Shell 后运行 Core 单元测试不足以证明真实 Shell 组合生命周期”
六类问题。没有用 skip/xfail、删除测试或降低断言关闭失败。

本 WP 只证明当前最小 Core 的公共生命周期能够承载未来后代，不声称 Assistant、插件、MCP、
TTS 或浏览器产品链已经完成排水，也没有修改 Supervisor、generation、restart budget、IPC
Envelope、Snapshot schema 或用户可见产品语义。P0/P1 为 0。

device validation deferred：macOS 透明命中、拖动、中文/日文 IME、Retina、Spaces、多屏；Linux
X11 透明命中、拖动、焦点、IME、多屏；Linux Wayland 透明、命中、拖动、焦点、IME、窗口身份
和 compositor 行为。以上全部保留在 WP-7-02 发布前真实设备硬门禁；CI/Xvfb 不计作实机通过。

独立回退：依次 revert accepted 记录、`b88e744`、`077dab8`、`ee3a39e`、`f71cc68`、
`35f5a30`、`d331919`、`abfe0fe`，即可恢复 WP-1P-05 accepted；不回退 WP-1P-01 至 05，不删除
普通 POSIX lock file、真实 Runtime/data 或用户资源。
