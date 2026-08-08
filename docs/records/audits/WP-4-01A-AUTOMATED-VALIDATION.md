---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-09
---

# WP-4-01A Memory 启动恢复自动验证记录

## 候选与范围

2026-08-09，在分支 `refactor/tauri-runtime-v2`、固定 base
`051ac908497ec361292431b31ec8a712be83893e` 上验证 WP-4-01A 工作树候选。Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准；本记录不填写人工验收，也不把任务
标记为 `accepted`。

候选修复三个真实产品故障：Memory 不再等首次打开设置页才预热；首次快照的 generation、transport 和
deadline 瞬时错误不再让三种初始化文案来回切换；加载中关闭设置后立即再次打开不会因旧原生窗口正在
销毁而丢弃请求。mem0、SentenceTransformer/PyTorch、Qdrant、LLM client 与 SQLite 全部位于当前 Core
generation 私有 Memory 子进程，Core Router 只保留有界代理和业务状态。

每次 Shell 启动覆盖 `data/logs/memory-initialization.jsonl`。诊断事件把 Qdrant、LLM client 和 SQLite
分别记录为 `qdrant_create`、`llm_create`、`sqlite_create` 的 started/completed/failed，不复制记忆正文、
query、路径、配置值、API key 或异常原文。单纯生成 mem0 配置不再提前创建 Qdrant 目录；目录副作用与
真正的 `qdrant_create` 阶段对齐。

## Windows 实机结果

在 Windows x64 本地 debug Runtime v2、固定 embedding 模型已安装的环境中连续完成三次真实初始化：

- 三次从启动到 `mem0_ready` 分别约为 48.4 秒、48.7 秒和 48.6 秒；三次都依次完成 embedding、Qdrant、
  LLM client 与 SQLite，并保持 Core snapshot、设置读取和关闭响应。
- 保存记忆整理模型触发一次 Core generation 重建；新 generation 完成第二次初始化并恢复原设置窗口。
- 第三次初始化期间关闭设置并立即再次打开，新窗口使用新的单调 generation 正常创建，未卡死设置入口。
- 调试实例正常退出；检查时无 Shell、Core 或 Memory 子进程残留。临时 probe 记忆已删除，
  `memory_curation.trigger_turns` 恢复为 `3`，整理模型恢复为
  `[满血A]gemini-3.1-flash-lite-preview`。

实机结束时日志为 49,449 bytes；后续自动 Core 生命周期测试只追加了安全生命周期事件，最终复扫为
400 行、79,179 bytes，仍低于 1 MiB。`mem0_ready`、`qdrant_create_completed`、
`llm_create_completed`、`sqlite_create_completed` 各 3 次。对 `query`/`content` 字段、API key/Bearer
形态、Windows 用户路径、Traceback/异常原文的扫描命中均为 0。本次没有 Memory 初始化失败；自动 Core
重启窗口中的失败事件均为预期的固定 `core_unavailable` 类别。

## 自动结果

- `runtime\python.exe -m harness check WP-4-01A`：当前任务、依赖、固定 base、allowlist、全局保护路径、
  activation 关闭、测试删除和 task 修订检查全部通过。
- Memory 定向 pytest（含未协商能力不得打开 Qdrant 存储）：31 passed；`py_compile` 和
  `git diff --check` 通过。
- `runtime\python.exe -m harness run core-host`：4/4 case 通过；Core Host unit 120 passed，真实进程
  integration 34 passed，Provider/模型 25 passed，Memory 16 passed。
- `runtime\python.exe -m harness run runtime-v2-shell`：6/6 case 通过；前端 132 passed；Rust 角色外观
  9 passed、角色表现 8 passed、产品 Shell 8 passed、窗口几何 23 passed、窗口交互 23 passed。
- `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1`：270 passed、
  24 ignored fixture、0 failed；`cargo fmt --check` 通过。配置投影修正后，完整 Rust 测试不再向源 fixture
  留下空 Qdrant 目录。
- `runtime\python.exe -m harness run python-full`：integration 46 passed/2 skipped，Legacy Qt 参考回归
  24 passed；unit 为 600 passed/6 skipped/1 failed。唯一失败是既有
  `test_timeout_and_utf8_output_are_actionable`：测试把 Python 启动与 UTF-8 首行输出固定在 20 ms 内，本机
  本次进程在约 32 ms 才超时，报告未捕获到先行输出。未修改 Harness 或测试放宽门禁。

## 自动门状态

Memory/Core、前端、Rust 与文档相关实现门已分别通过，Windows 实机复现也已消失；但上述 Harness
20 ms 用例使 `python-full` 不能全绿。最终执行
`runtime\python.exe -m harness verify WP-4-01A` 返回 exit code 1 / `failed`，报告为
`temp/harness/20260808T195238.731379Z-WP-4-01A.json`：docs 2/2 passed，required `smoke` 在
`harness-self-test` 同一用例失败，后续 core-host 与 runtime-v2-shell 因首失败策略标为 blocked；两者已在
本次 verify 前独立运行并分别为 4/4、6/6 通过。因而本记录不能表述为“自动门通过”或“Work Package
完成”，更不能代替项目负责人的人工验收。
