---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-07
---

# WP-4-01 本地自动验证记录

## 候选与范围

2026-08-03，在分支 `refactor/tauri-runtime-v2`、激活基线 `2f4c5134` 上完成 WP-4-01 工作树候选的
本地自动验证。Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准；本记录不填写人工验收，也不把任务
标记为 `accepted`。

候选建立当前 bundled Python Core generation 唯一的无 Qt Memory owner、角色 scope 召回、完成回复后
整理、Memory 设置/CRUD、固定 embedding 模型导入与显式下载，并开放四个 Memory 设置 feature。模型
任务使用 Memory 域专用的 opaque identity、进度、取消和唯一终态，没有抽取 WP-4-02 的通用 Operation。

## 自动结果

- `runtime\python.exe -m harness check WP-4-01`：范围、依赖、受保护路径和 dependency policy 全部通过。
- `runtime\python.exe -m harness run core-host`：Core Host unit 117 passed；真实进程 integration 36 passed；
  WP-4-01 Memory 10 passed。
- `runtime\python.exe -m harness run runtime-v2-shell`：前端 109 passed；Provider/模型 25 passed；Memory
  10 passed；角色外观 8 passed；角色表现 8 passed；产品 Shell 7 passed；窗口几何 16 passed；窗口交互
  15 passed。
- `runtime\python.exe -m harness run docs`：文档结构、元数据、链接、状态真相源及 4 项结构测试通过。
- `runtime\python.exe -m harness run smoke`：Harness self-test 6 passed、Agent Development 39 passed、
  Core Host protocol 20 passed。
- `runtime\python.exe -m harness run python-full`：unit 589 passed/6 skipped；integration 45 passed/2
  skipped；Legacy Qt 仅作为迁移参考的 UI 回归 24 passed。
- `cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml`、`cargo fmt --check`、新增 Windows
  验收脚本 PowerShell parser、JavaScript `node --check`、Python `py_compile` 和 `git diff --check` 通过。
  `cargo test --locked -- --test-threads=1` 为 243 passed、24 ignored fixture、0 failed；Rust build 只有
  既有 `character_appearance.rs` 三项 dead-code warning。

模型任务定向门覆盖 generation/window 绑定、opaque cancel handle、单调进度、取消和重复 terminal；Python
边界覆盖取消与失败的唯一脱敏终态；下载使用 staging，失败删除 staging 并保留旧可读缓存。普通聊天在
embedding 缺失或 Memory 故障时继续真实 Provider 请求，不隐式联网或自动重发。

## 尚待负责人完成

Windows 可见 UI 验收入口为：

```powershell
.\desktop\tests\windows_wp_4_01_memory_acceptance.ps1
```

该脚本直接启动当前 debug Runtime v2 EXE，在系统临时目录中使用隔离应用根和 embedding cache；正常退出
后检查 `data/memory.json` 字节、允许写入路径、共享锁立即重获、相关进程残留和临时根清理。脚本不会
自动填写验收结论。

负责人仍须亲自完成中文/日文 IME CRUD、召回影响下一轮真实聊天、一次完成回复整理、错误 ZIP/下载失败
恢复、模型任务取消、Core 强杀与新 generation 恢复，并审查同一最终候选 SHA 的 Windows x64、macOS
arm64、Linux x64 workflow、脱敏 manifest/log 和 Memory-only 回退边界。远端三平台同 SHA 证据和人工
操作在本记录创建时尚未发生，因此不能据此标记 `accepted`。

## 2026-08-07 最终契约修正复验

独立审查确认两项缺口后，以提交 `cf164dc` 冻结唯一最终契约修订 `0006`，随后执行
`python3 -m harness preflight WP-4-01`，范围、依赖、受保护路径、dependency policy 和 activation 历史
全部通过。实现只拆分 Qt-free 资源边界、修正默认能力测试拓扑并增加当前产品拓扑门；未启动 WP-4-02，
未修改 IPC、Memory 数据格式、Runtime manifest、依赖锁、userdoc、CHANGELOG 或 CAP-008 状态。

修正候选的定向结果如下：

- `runtime/bin/python3 -m pytest` 运行 ResourceManager 兼容、纯资源导入、Memory 资源/Core 和两项真实进程
  集成测试：56 passed。当前产品拓扑测试在独立进程拒绝全部 `PySide6` import 后，同时完成 Chat、
  Provider Settings、Memory 设置/搜索和正常 shutdown。
- `cargo test ... core_host_runtime::tests:: -- --test-threads=1`：42 passed、1 个仅正式 packaged Runtime
  可运行的测试 ignored；历史生命周期测试均发送显式 predecessor hello payload。
- 默认 hello 能力集合定向测试：1 passed；Memory Gateway：4 passed；`cargo fmt --check` 通过。
- Appearance、Provider、Memory 前端定向回归：26 passed；各领域 dirty、draft 和 generation rebind
  回归保持通过。
- `runtime/bin/python3 -m harness run docs`：2/2 case 通过；
  `runtime/bin/python3 -m harness check WP-4-01`：通过且无范围外、禁止或受保护文件变更。

首次直接运行 `verify` 时，`python-full` 的两项既有 WP-3-06 测试把本机系统临时目录中的
`/var -> /private/var` 识别为不安全路径，报告为
`temp/harness/20260807T120342Z-WP-4-01.json`。未修改 WP-3-06 代码或契约；把 `TMPDIR` 指向仓库内隔离
临时根后，两项测试单独复跑为 2 passed。随后在相同隔离环境执行：

```text
TMPDIR=<repo>/temp/harness-runtime-tmp runtime/bin/python3 -m harness verify WP-4-01
```

结果为 exit code 3 / `manual_pending`，报告
`temp/harness/20260807T120524Z-WP-4-01.json`：docs、smoke、core-host、runtime-v2-shell、python-full 五个
required profile 全部通过，自动条目 8/8 passed，人工条目 3 项 pending。其中完整 Python unit 为
600 passed、1 skipped，integration 为 48 passed，Legacy Qt 参考回归为 24 passed。

该结果只能表述为“自动门通过，等待验收”。同一最终提交的远端 Test 与 Windows/macOS/Linux platform
workflow、Windows EXE 人工步骤和 packaged Runtime 依赖审查尚未在本次本地复验中发生；WP-4-01 继续
保持 `active`，CAP-008 不在本记录中更新为 `architecture-validated`、`platform-verified` 或
`parity-accepted`。
