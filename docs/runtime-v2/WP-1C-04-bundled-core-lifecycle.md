# WP-1C-04：bundled Core 生命周期

> Work Package 状态、启动点和唯一 active/stabilizing 项只见
> `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`。
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
- `docs/runtime-v2/WP-1C-04-bundled-core-lifecycle.md`
- `docs/adr/0001-runtime-v2-process-supervision.md`
- `docs/adr/0002-runtime-v2-ipc.md`
- `docs/adr/0004-runtime-v2-cross-platform-foundation.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`

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
