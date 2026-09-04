---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-1P-04：Windows/macOS/Linux 受控进程树 backends

> 工作包进度见 `docs/plans/runtime-v2/work-packages.md`，不作为开发许可。
> 日期：2026-07-24
> 前置：WP-1P-03 accepted，提交 `9d079a4d`
> 规范来源：ADR-0001、ADR-0004、`WP-1P-01-platform-contract.md`

## 1. 结果边界

本 Work Package 将已经验收的 Windows `ManagedProcessTree` 迁移到可注入的
`ManagedProcessTreeBackend`，并为 macOS/Linux 建立同一公共生命周期契约的正式 POSIX
backend。公共层继续只观察 root pid、root wait、整树 terminate、整树 verified exited
和 release；Win32 handle、POSIX fd、session id、process group id、signal 和 guardian
控制协议都留在平台实现。

进程树 backend 负责操作系统资源管理；Supervisor、IPC 和领域能力继续由各自所有者维护。
跨模块调整需保持下述生命周期和故障契约。macOS/Linux 编译结果不能替代窗口、IME、多屏或 compositor 实机证据。

## 2. 验证环境

真实后代进程和故障夹具使用隔离临时根，测试结束回收全部后代和临时资源。保留用户未跟踪文件、真实配置、角色
及 Runtime，不能为清理测试而删除或改写无关数据。

## 3. 公共生命周期冻结

调用顺序保持：

```text
backend.spawn
  -> 可选 pipes 交给 IPC owner
  -> protocol shutdown / root wait
  -> 必要时 terminate_tree
  -> wait_tree_exited
  -> close/drain pipes
  -> release_exited
  -> 才允许下一 generation
```

root exit 不等于 tree exit。`wait_root`、`terminate_tree` 和 `wait_tree_exited` 必须可重复；
`release_exited` 只有在整树 verified exited 后成功。Drop 只作为最终保险，不代替显式
清理、等待和验证。任何平台无法建立监管边界时 spawn 必须 fail closed。

## 4. 平台实现

### Windows

保留每 generation 独立 Job Object、suspended spawn、resume 前 assignment、
kill-on-close、匿名 pipe ownership、assignment/resume 失败回收和 Job accounting 验证。
适配层只把既有错误、wait 和 pipe DTO 转成平台契约；不得以形式统一重写 Win32 时序。

### macOS/Linux

POSIX guardian 在 Core 执行前先通过 `setsid` 建立独立 session/process group，再在该组内
启动 Core，消除“Core 已执行但尚未接入监管”的逃逸窗口。Tauri 持有 guardian 控制管道；
正常 release 显式关闭，Tauri crash/kill 导致 EOF，guardian 必须对整个 group 执行有界
TERM/KILL 回收。Core root 退出时 guardian 仍负责清理遗留组成员并报告 root status。

Tauri 侧通过 PGID 执行整组终止和 verified-exited 轮询；guardian/control/status fd 与三条
stdio pipe 均采用明确的 close-on-exec 和单一 owner。Linux 可以把 parent-death signal 作为
额外保险，但不能代替 guardian EOF 和 group 回收。PGID 必须由仍受控的 guardian identity
锚定到 root 启动成功，不能根据不受信任 PID 文本重建。

## 5. 故障矩阵

| 场景 | 必须结果 |
|---|---|
| 普通/piped spawn | root 在监管组内执行；parent 只保留其拥有的 pipe 端 |
| program/cwd/pipe 建立失败 | 无已执行 Core、guardian、fd 或临时目录残留 |
| Job assignment/resume 或 POSIX setsid/guardian 接入失败 | fail closed，并回收已创建对象 |
| root 正常退出 | wait 可重复返回同一 status；整树另行验证 |
| root 先退出且一层/多层后代仍存活 | guardian/backend 回收完整 group，不把 root exit 当成功证据 |
| 后代忽略 shutdown/TERM | deadline 后升级 KILL，整树 verified exited |
| deadline 超时 | 有界返回；不得无限 wait |
| PID/PGID identity 变化或复用风险 | 不接管不属于当前 generation 的树，稳定 fatal |
| 重复 terminate/wait/verify/release | 幂等或稳定 invalid-state，不泄漏、不误杀 |
| Tauri 正常退出 | 显式排水后 release，全部 handle/fd/pipe/process/group 清零 |
| Tauri crash/kill | Job kill-on-close 或 guardian EOF 保险回收 |

## 6. 验证责任和退出条件

- Windows `windows-2025` x64：既有 Job Object 全回归、生命周期故障 fixture、真实 bundled Python、
  assignment/resume rollback、handle/pipe 零残留。
- macOS `macos-15` arm64：真实 session/process group、root/child/grandchild、忽略 TERM、
  guardian EOF、fd/pipe 和临时目录零残留。
- Linux `ubuntu-24.04` x64：与 macOS 同矩阵，并记录 parent-death 保险（如启用）不是唯一机制。
- 三平台均运行普通/piped spawn、root-first-exit、强制整组回收、重复 API、旧 generation
  barrier、Rust Supervisor fixture 和 staged bundled Python 根进程。
- 相关 Rust 测试、`python -m pytest tests/unit`、`python -m pytest tests/ui`、Debug/Release
  build、`cargo fmt --check`、`cargo test --locked`、`py_compile` 和 `git diff --check`
  按本机可执行范围通过；macOS/Linux 由最新 HEAD 原生 CI 证明。
- 普通 Unit/UI 和 platform foundation 三平台最新 HEAD 全绿，P0/P1 为 0。

满足以上证据后才能把本文和总计划登记为 accepted，再激活 WP-1P-05。

## 7. Accepted 记录（2026-07-24）

实现提交：`1aa02e5`（`feat(runtime): 实现三平台受控进程树后端`）。

最新 Draft PR #147 HEAD：`1aa02e591335d7ebc43d50b2b3533f60d8edbf1b`。

最新 `runtime-v2-platform-foundation.yml` 原生证据：

- push run `30057738510`：Windows x64、macOS arm64、Linux x64 全部通过；
- pull_request run `30057739993`：Windows x64、macOS arm64、Linux x64 全部通过；
- 同一 HEAD 的 Unit/UI run `30057739984`：Unit 与 UI 全部通过。

证据覆盖 RuntimeLocator staging、三平台编译、cargo fmt、platform/shared-instance 契约、
Windows Job Object 回归和 macOS/Linux POSIX backend 的真实子进程组测试（root-first-exit、
多层后代、忽略 TERM、guardian EOF、超时升级、重复 API、身份边界和资源回收）。Workflow
使用真实 runner；没有用 skip/xfail、旧 run 或 Windows 结果替代其他平台。通过结果只证明
进程树 backend 的 CI platform foundation，不替代 WP-1P-06 的完整 Shell + Core 生命周期门。

审查确认没有修改 `data/`、`runtime/`、角色、插件、`.superpowers/` 或产品 IPC/Supervisor
语义，P0/P1 为 0。独立回退为依次 revert `1aa02e5`、`8bffe3e`（保留 WP-1P-01/02/03）。

## 8. 独立回退

整体 revert WP-1P-04 的实现、修正和 accepted 提交：删除 POSIX guardian/backend 与新增
fixture，恢复 Windows `ManagedProcessTree` 的直接调用和 WP-1P-03 accepted 启动点。回退
不得删除用户 Runtime/data、普通 POSIX lock file 或 `.superpowers/`，也不回退
WP-1P-01/02/03。
