# WP-1P-03：Windows/POSIX 共享应用锁 backends

> 状态：Accepted
> 日期：2026-07-23
> 前置：WP-1P-02 accepted，提交 `d7248da3`
> 规范来源：ADR-0003、ADR-0004、`WP-1P-01-platform-contract.md`

## 1. 结果边界

本 Work Package 让 Tauri/Rust 与 legacy Python/Qt 在每个正式平台竞争同一个共享应用锁，并由桌面生命周期根持有 lease。Windows 保留已经验收的 named mutex；macOS/Linux 新增同路径、同打开规则、同 `flock` 语义的 Rust/Python backend。Tauri composition root 只通过 `InstanceLockBackend` 获取 lease，不直接选择平台实现。

本 WP 同时修正迁移后仍从 Tauri `main.py` 导入 legacy Qt 符号的测试。legacy UI 生命周期继续留在 `legacy_qt_main.py`，不回填到新的 Tauri 入口。

以下不属于 WP-1P-03：

- Core Supervisor、IPC、Snapshot、进程树、窗口交互或产品能力修改。
- 把 Core、插件、MCP、TTS 或浏览器子进程变成锁竞争者。
- 删除历史 `data/sakura.lock`、Qdrant lock 或任何用户数据。
- 以 PID、锁文件内容、文件存在或 stale 猜测代替 OS lock。
- 完整应用退出排水的 macOS/Linux 产品级证明；该证据在 WP-1P-06 的真实 Shell + Core lifecycle 总门完成。

## 2. 稳定 identity 与平台 object

语义 identity 固定为 `sakura.desktop.shared-user-data.v1`，不按入口、版本、安装目录、角色、Core generation 或 CPU 架构分叉。

| 平台 | 权威 object |
|---|---|
| Windows | `Local\SakuraDesktop.SharedUserData.v1` named mutex |
| macOS | 下述 canonical lock path 上的 `flock(LOCK_EX | LOCK_NB)` |
| Linux | 下述 canonical lock path 上的 `flock(LOCK_EX | LOCK_NB)` |

`InstanceLockBackend::acquire` 只接受冻结 identity。其他 identity 返回 `platform.instance_lock.invalid_input`，不能隐式建立第二套锁。

## 3. POSIX 路径冻结

路径解析是纯函数；读取环境变量时不触碰 `data/`、日志、配置或 Core。被选中的环境变量必须是绝对路径，相对路径立即 fatal，不继续尝试下一优先级。

| 平台 | 优先级 | 最终路径 |
|---|---|---|
| macOS | `TMPDIR` | `$TMPDIR/sakura/sakura.desktop.shared-user-data.v1.lock` |
| macOS | `HOME` fallback | `$HOME/Library/Caches/sakura/sakura.desktop.shared-user-data.v1.lock` |
| Linux | `XDG_RUNTIME_DIR` | `$XDG_RUNTIME_DIR/sakura/sakura.desktop.shared-user-data.v1.lock` |
| Linux | `XDG_STATE_HOME` fallback | `$XDG_STATE_HOME/sakura/sakura.desktop.shared-user-data.v1.lock` |
| Linux | `HOME` fallback | `$HOME/.local/state/sakura/sakura.desktop.shared-user-data.v1.lock` |

未设置、空字符串只表示该候选不可用。所有候选均不可用时 fatal。路径不位于仓库、安装目录或共享 `data/`，Rust 与 Python golden tests 必须对相同环境映射得到逐字一致的结果。

## 4. POSIX 打开与安全规则

获取顺序固定如下：

1. 递归创建最终 `sakura` 锁目录，然后 canonicalize。
2. 要求 canonical 目录是 directory 且由当前 effective UID 所有，再将 mode 收紧为 `0700`。
3. 以 read/write、create、`O_CLOEXEC`、`O_NOFOLLOW` 和初始 mode `0600` 打开固定文件名。
4. 对已打开 fd 执行 `fstat`；只接受 regular file、当前 effective UID 所有、`st_nlink == 1`。
5. 用 `fchmod` 将文件 mode 收紧为 `0600`。
6. 对 fd 执行非阻塞 exclusive `flock`，并让 lease/file descriptor 覆盖完整桌面生命周期。

锁文件不写 PID 或状态文本。普通文件在进程退出后可以保留；是否已有实例只取决于当前 fd 上的 advisory lock。符号链接、硬链接、错误 owner/type、打开/检查/改权限失败均 fatal，不能删除目标、强制接管或继续启动。

只有 `flock` 本身返回 `EACCES`/`EAGAIN`/`EWOULDBLOCK` 才映射为 `already_running`。路径解析、目录创建、`open`、`fstat` 或 `fchmod` 的同名 errno 仍属于 fatal，防止权限错误被误报成普通冲突。

## 5. 生命周期与结果

```text
桌面入口
  -> acquire shared instance lease
     -> acquired: 继续 Shell/Core 生命周期，根对象持有 lease
     -> already_running: 显示稳定冲突提示，不启动 Core，不写共享 data
     -> fatal: 显示平台诊断，非零退出，不降级成第二写入者
```

Windows guard 的 Drop 释放 mutex 并关闭 handle；POSIX guard 的 Drop 解锁，随后关闭 fd。正常释放后另一个入口应立即获取。进程 crash/kill 时由 OS 回收 handle/fd 和 held lock，不依赖删除普通锁文件。

Tauri 必须在 `tauri::Builder`、Core、日志、配置、migration 和任何共享数据动作之前获取 `InstanceLockBackend` lease。legacy Qt 保持相同锁前零写入顺序。冲突提示仍为：

- 标题：`Sakura 已在运行`
- 正文：`另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。`

## 6. 错误映射

| 情况 | 稳定结果 |
|---|---|
| identity 漂移 | `platform.instance_lock.invalid_input` / `Never` |
| 已有 holder | `already_running`，不是 `PlatformError` |
| 权限/owner 拒绝 | `platform.instance_lock.permission_denied` / `AfterUserAction` |
| 必需环境根缺失或平台不支持 | `platform.instance_lock.unsupported_environment` |
| 其他 OS/API 失败 | `platform.instance_lock.native_failure`，保留脱敏 `win32`/`errno` native code |

错误分类只决定诊断语义，不授权自动重试、提权、删锁或绕过互斥。

## 7. 验证责任与 Accepted 证据

本地 Windows 回归已经证明：

- 原 7 个 Unit 失败和 12 个 legacy UI 失败已关闭；Unit 为 965 passed / 6 skipped，UI 为 379 passed，`test_pet_window.py` 为 272 passed。
- Windows shared lock Rust tests 4 passed，shared identity 1 passed，Debug Shell build 成功。
- legacy 测试改为导入 `legacy_qt_main.py`；新的 Tauri `main.py` 未恢复 Qt 生命周期。

提交 `71c3039c` 的三平台原生 CI run `30025831299` 已证明：

- Windows named mutex 回归无变化。
- macOS arm64 与 Linux x64 的 Rust/Python 路径 golden、双向冲突、正常释放、强杀释放和普通文件残留均通过。
- 锁目录/文件的 type、owner、mode、单硬链接和 no-follow 约束通过；权限/API 错误 fatal。
- 平台 foundation workflow 三个 job 全绿；macOS arm64 用时 1m57s、Linux x64 2m38s、Windows x64 3m23s。

同一 SHA 的 Test run `30025831268` 也已全绿：Unit 2m12s、UI 4m08s。push event 的三平台 run `30025828101` 独立重复成功。实现过程先后暴露并修正 Unix 泛型函数项生命周期推断以及测试探针 `PYTHONPATH`/stdout pipe 问题；没有用 skip/xfail 绕过失败。

diff 审查确认没有 `data/`、`runtime/`、产品能力或用户资源变化，P0/P1 为 0，故 WP-1P-03 登记 `Accepted`。这里接受的是生产 Rust/Python backend、Tauri composition-root 接线和原生子进程互斥契约；WP-1P-06 继续负责三平台真实 Shell + Core、legacy Qt 回退入口和全部后代/写入任务完整排水后才释放锁的产品级生命周期总证据。

## 8. 独立回退

整体 revert WP-1P-03 提交：恢复 Tauri 对既有 Windows guard 的直接调用、Python Windows-only named mutex 和测试入口修正前状态；删除 POSIX fixture 与本规范。回退不删除任何普通 lock file、历史 `data/sakura.lock`、Qdrant lock、用户 `data/` 或 Runtime 内容，也不回退 WP-1P-01/02。
