---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-1C-04：bundled Core 生命周期

> Work Package 状态、启动点和唯一 active/stabilizing 项只见
> `docs/plans/runtime-v2/work-packages.md`。
> 开始日期：2026-07-24
> 前置证据：WP-1C-03 accepted；提交 `2ad0014` 的三平台 platform foundation 与 Unit/UI 全绿。

## 1. 结果与边界

本 Work Package 只冻结 bundled Python Core 的三平台端到端 lifecycle：Windows x64、macOS
arm64 和 Linux x64 的 Tauri Shell 经 `RuntimeLocator` 解析受控开发、测试和发布布局，启动真实
Core Host，并完成 `hello`、`initialize`、readiness、Snapshot、health 和 protocol shutdown。

bundled Python 必须来自各目标平台已固定、可校验的发布 Runtime 工件；发布布局只能使用包内
Python/sidecar，不得回退系统 Python、扫描 `PATH` 或由公共逻辑假设 `.exe`。`RuntimeLocator`
必须返回可执行文件、资源根、Core module、工作目录和架构的结构化结果；Windows、macOS bundle
和 Linux 包布局均由各自 fixture/golden layout 约束。

本规范只引用总表的 Work Package 状态，不复制或维护任何状态字段。

## 2. 精确允许目录

只允许修改下列路径；新增 fixture 必须位于列出的 WP 专用目录。

- `app/core_host/`
- `desktop/src-tauri/src/core_host_runtime.rs`
- `desktop/src-tauri/src/runtime_locator.rs`
- `desktop/src-tauri/src/managed_process_tree.rs`
- `desktop/src-tauri/src/platform/`
- `desktop/tests/`
- `tests/unit/test_core_host_*.py`
- `tests/integration/test_core_host_lifecycle.py`
- `tests/fixtures/runtime_v2/wp_1c_04/`
- `.github/workflows/runtime-v2-platform-foundation.yml`
- `docs/specs/runtime-v2/WP-1C-04-bundled-core-lifecycle.md`
- `docs/adr/0001-runtime-v2-process-supervision.md`
- `docs/adr/0002-runtime-v2-ipc.md`
- `docs/adr/0004-runtime-v2-cross-platform-foundation.md`
- `docs/plans/runtime-v2/work-packages.md`

## 3. 精确禁止目录和能力

禁止修改 `app/agent/`、`app/assistant/`、`app/core/`、`plugins/`、`app/plugins/`、`data/`、
`runtime/`、`characters/`、`desktop/src/`、`desktop/src-tauri/src/router/`、`desktop/src-tauri/src/operations/`
及非本 WP fixture/test 路径。不得变更角色、Core 配置、历史或用户数据 schema。

明确禁止 Assistant Adapter、聊天、Router、Operation、chat cancel/Gateway、业务优先级、resource
token、通用协议平台、Memory、Tools、MCP、插件、TTS、浏览器、截图和主动互动。不得放宽
timeout、安全失败、三平台矩阵或 required checks。

## 4. 生命周期与故障范围

- Core 在协议协商后通过 `initialize` 开始最小初始化；readiness、只读 Snapshot 与 health 不承载
  Assistant 领域语义。
- 覆盖正常 shutdown、Core crash、忽略 shutdown 后的强制整树回收、以及共享应用锁的立即重获。
- 每个 generation 的 stdin/stdout/stderr pipe、reader/writer/init thread、native handle/fd、进程树和
  临时资源在正常、初始化失败、crash 和强杀后均须归零；旧 generation 不得影响新 generation。
- 继续沿用 WP-1C-03 的 hello 3 秒、initialize 接受 5 秒、readiness watchdog 30 秒、shutdown 3 秒和
  完整树停止 5 秒；失败安全关闭，不以延长或跳过门禁规避问题。
- `data/`、`runtime/`、角色、配置和历史在真实应用验收前后必须零非预期变化，以递归清单与内容摘要
  比较证明。

## 5. 验收计划

自动测试：在 Windows x64、macOS arm64、Linux x64 同一提交运行 platform foundation，验证
RuntimeLocator、bundled Python 来源/布局、共享锁、ManagedProcessTree、`hello -> initialize ->
readiness -> Snapshot -> health -> shutdown`、强制回收与 generation 资源清理；普通 Unit/UI checks
也必须通过。

故障测试：分别注入 Core crash、初始化失败或超时、忽略 shutdown、遗留后代、pipe/reader/writer
清理、锁竞争与重获，证明不留下 root/后代、thread、handle/fd 或临时目录残留，且不修改用户数据。

真实应用验收：分别在 Windows x64、macOS arm64、Linux x64 的真实 Tauri Shell 使用各自 bundled
Python 运行 lifecycle 冒烟；记录运行时来源、架构、`RuntimeLocator` 结果、生命周期事件、强杀保险
和前后数据清单。CI/Xvfb 不代替 macOS、Linux X11/Wayland 的发布前窗口体验证据。

## 6. 独立回退

仅回退本 Work Package 的提交：

```powershell
git revert --no-edit <WP-1C-04-commit-SHA>
```

回退后恢复 WP-1P-06 已验证的开发 `RuntimeLocator`/Fake Core 生命周期路径；保留 WP-1C-03
协议安全、三平台进程树与共享锁门禁，不删除或改写 `data/`、`runtime/`、角色、配置和历史。

## 7. 实现与 Windows 预验收记录（2026-07-24）

当前实现把 `RuntimeLayout` 冻结为 target、architecture、mode、Runtime root、Python executable、
resource root、Core entry、Core module、working directory 和 source ID。`CoreHostRuntime` 仅消费
该结构，并在 spawn 前验证所有路径为 absolute/canonical、Python 与资源位于 Runtime root、Core
entry 位于资源根且与 module 一致。启动参数固定使用 `-I -B -X utf8` 和受控 bootstrap；生产代码
没有系统 Python fallback、`PATH` 扫描、隐式 cwd、`target/debug` 推断或公共 `.exe` 假设。

共享 `lifecycle-golden.json` 同时由 Rust locator/runtime 与 Python integration/Shell 验收读取，冻结
三 target 的 architecture/packaged layout、协议版本/capability、既有 deadline 和
`hello -> initialize -> readiness -> Snapshot -> health -> shutdown` 顺序。CI 从现有三份固定 manifest
下载并校验 archive 后，同时 staging development 和 packaged Runtime；packaged 资源只复制无 Qt
`app/core_host`，运行前后递归内容摘要必须相同。

Windows x64 本机预验收使用 Python 3.12.8 AMD64 固定 archive，size `11094114`、SHA-256
`8d3f33be9eb810f23c102f08475af2854e50484b8e4e06275e937be61ce3d2fb`。packaged lifecycle/fault
matrix 为 1 passed，覆盖连续两代 ready lifecycle、failed readiness、Core crash、忽略 shutdown 与
强制整树回收；40 个 packaged 文件、`22450769` bytes，前后摘要均为
`fcdc6dfa6eb426aac2b71d9c1c037140a3d15e8695b51b4534432e8b85b86121`。

真实 Debug Tauri Shell 完成 normal+lock conflict、Shell crash、lock reacquire 三轮；每轮由正式
development `RuntimeLocator` 启动 bundled Python，完成 hello/initialize/ready Snapshot/两次 health/
protocol shutdown，crash 后 Core Job 整树退出，下一轮立即重获共享锁。最终精确匹配 Shell/Core
进程 0、验收临时目录 0。保护资源 before/after 逐项相同：`characters/` 16 文件、343514157 bytes、
SHA-256 `612d93d89a4f32a5033bafae7852200fe3161beaf8035ce41338a98a542d15f5`；`data/` 13 文件、
4182744 bytes、SHA-256 `c5a4019f0af056d6b317524f2345f68c46b911ef9b74f92f562b9f667663a5fa`；
`runtime/` 47501 文件、2028445327 bytes、SHA-256
`03cb4c7039ebaf4a7ab5e4232d1395455c5aafeb03024fe040b503b279304a78`。

本机自动门禁：相关 Python 47 passed；archive/workflow 3 passed；最终 Python integration/workflow
16 passed；Rust CoreHost 19 passed、1 个 packaged 显式验收项按默认 ignored；显式 packaged 项
1 passed；完整 Rust 112 passed、15 个 fixture/显式验收项 ignored；`cargo fmt --check`、Debug locked
build 和三份 Python 脚本 `py_compile` 通过。本机完整 Unit 的 WP 相关测试全绿；其余既有测试因本机
残留 `F:\Projects\Sakura`/无权限 `D:\` 产生 6 failed、12 setup errors，不涉及本 WP 修改路径，最终
权威结果以同一提交的 GitHub Unit/UI jobs 为准。

首个实现提交 `97f5216` 的三平台 packaged lifecycle 都通过，但 macOS/Linux 只读摘要准确捕获到
test-only fault fixture launcher 在解包标准库写入 bytecode；修正提交 `7d4067f` 让 test launcher 与
产品启动统一使用 `-I -B -X utf8`，没有放宽 timeout、摘要或断言。修正后同一 HEAD 的 push
platform run `30091500680`、pull_request platform run `30091504687` 和 Unit/UI run
`30091504697` 全绿；PR #147 保持 Draft，merge state CLEAN，P0/P1 为 0。

push run 三平台固定 archive 与 packaged 资源摘要分别为：Windows x64 `11094114` bytes /
`8d3f33be9eb810f23c102f08475af2854e50484b8e4e06275e937be61ce3d2fb` /
`7b331f6cc8688d448ead7663992808edbbadff4deffec921215e656894d64d73`；macOS arm64
`15676873` bytes / `5dfd4d81ad8ea0407e6153ed998a5fba332275c60ece81c6db2b58e443de60b9` /
`a0ad3a0c83b378ef38eb766735c0de718a88de9d87b3b736d7b5096189d43e9f`；Linux x64
`67062562` bytes / `c8032747c8e44ce0164236fa70a6b767a43ef778dc51b99bd18f25984f8cba3b` /
`fe60308e4dd1c5859a3c731e81f0c69967f74eda0e5d5739a38435d7136b88a4`。各平台摘要均为运行前后
同值，development Shell 的 runtime 摘要也分别保持 Windows
`c8526139bea34915e03dac13ada959f96ad95be7275c2e112ccfbd199222646b`、macOS
`a770207330fff18eff6a23d470bdf9e967e615228bf8e480a802c6d8de208d2c`、Linux
`0bb7c231f1123330359ba3cbadd32ead603f71b803f502c15700fe56c06eea6d`；CI checkout 的
`characters/`、`data/` 均为零文件且前后摘要一致。

据此总表可进入 `stabilizing`。最终 `accepted` 仍要求本次状态/ADR/总表文档 HEAD 自身的三平台、
Unit、UI 全绿并完成最终 diff、残留资源、PR 和回退审查；WP-3-01 继续保持 planned。

Stabilizing 文档 HEAD `18a3cab` 的 push platform run `30091910794`、pull_request platform run
`30091915123` 和 Unit/UI run `30091915140` 再次全绿。最终审查确认 WP 白名单外 tracked diff 为 0，
`characters/`、`data/`、`runtime/` tracked diff 为 0，精确匹配 Shell/Core 进程、验收目录和 staging
目录残留均为 0；PR #147 保持 Draft、merge state CLEAN、8/8 checks 成功，P0/P1 为 0。

据此 WP-1C-04 验收闭环：三平台 development/packaged locator、固定 bundled Python identity/
architecture/layout、正常与故障 lifecycle、连续 generation、pipe/reader/writer/init thread、handle/fd、
完整进程树、临时目录、共享锁重获和保护资源摘要均有同提交自动证据。后续破坏性 lifecycle 修改必须
暂停功能开发并更新 ADR/golden；WP-3-01 仍为 planned，本次不激活。
