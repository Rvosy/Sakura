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

## FastEmbed/ONNX 启动性能补充

同日继续在相同 Windows x64 debug Runtime v2 上验证 FastEmbed/ONNX 候选。首次真实 EXE 仍约 41 秒才
就绪；新增的 mem0 依赖检查点确认 ONNX 模型加载仅约 0.10 秒。独立 import 探针把主要等待定位到本地
Qdrant 不使用的 `grpcio` 原生扩展（约 32.2 秒）和遥测关闭时仍被 mem0 无条件导入的 PostHog SDK
（约 10.7 秒）。

候选随后只加载 Qdrant 同步客户端，为固定本地 Qdrant 的未使用远程路径提供 import-only gRPC 占位符，
并在 `MEM0_TELEMETRY=False` 时使用轻量 PostHog 占位模块。最新一次真实 EXE 时间线如下：

- mem0 及其依赖 import 约 1.59 秒；固定 ONNX 模型加载约 0.11 秒；LLM client 创建约 12.19 秒。
- 从启动到 `mem0_ready` 约 13.92 秒，完整实机脚本约 14.76 秒，exit code 0；已低于旧版约 20 秒的目标。
- 独立本地 Qdrant 探针完成 collection 创建、向量写入、相似度查询和关闭；结果命中预期 point，且
  `AsyncQdrantClient` 与真实 `grpcio` 均未加载。
- `tests/unit/test_memory_store_resources.py` 为 25 passed，覆盖 PostHog 占位、同步 Qdrant facade、本地
  Qdrant 查询/关闭、导入钩子恢复及诊断脱敏。

快速接话不接入 Runtime v2，本候选未迁移或加载其 BGE 模型、分类头和调用链。

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

## FastEmbed 启动优化后的自动回归

在加入同步 Qdrant facade、gRPC/PostHog 占位和依赖级导入检查点后，继续对同一候选运行：

- Memory 定向 pytest：39 passed，覆盖 Memory 资源、Core Memory 边界和 WP-4-01 集成契约。
- `runtime\python.exe -m harness run docs`：2/2 passed，报告
  `temp/harness/20260809T045301.652418Z-docs.json`。
- `runtime\python.exe -m harness run core-host`：4/4 passed；Core Host unit 121 passed、真实进程
  integration 34 passed、Provider/模型 25 passed、Memory 17 passed，报告
  `temp/harness/20260809T045237.763222Z-core-host.json`。
- `runtime\python.exe -m harness run runtime-v2-shell`：6/6 passed；前端 132 passed，Rust 各目标均通过，
  报告 `temp/harness/20260809T045248.883094Z-runtime-v2-shell.json`。
- `runtime\python.exe -m harness run smoke` 仍为 2/3 passed，报告
  `temp/harness/20260809T045333.010645Z-smoke.json`。唯一失败仍是既有
  `test_timeout_and_utf8_output_are_actionable`：20 ms 超时先于本机 Python 子进程的 UTF-8 首行输出；未修改
  Harness 或测试绕过该门禁。
- `runtime\python.exe -m harness verify WP-4-01A` 返回 exit code 1 / `failed`，报告
  `temp/harness/20260809T045509.198942Z-WP-4-01A.json`；docs 2/2 passed，smoke 在同一既有用例失败，
  后续 required profiles 依首失败策略标为 blocked。独立执行的 core-host 4/4 和 runtime-v2-shell 6/6
  结果如上；本候选仍不能声明自动门全绿或 Work Package 完成。

提交 `2acb5b610997ae6f193b35643327df573c7f0046` 后再次运行相同 `verify`，波动的 20 ms Harness 用例本次
通过，docs、smoke、core-host、runtime-v2-shell 共 15/15 自动 case 全部通过。机器报告
`temp/harness/20260809T045623.032004Z-WP-4-01A.json` 状态为 `manual_pending`，自动失败和 blocked 均为
0；人工验收仍为 `pending`。这只证明自动门通过，不把 Work Package 状态改为 `accepted`，最终状态继续
只以 [`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

## SOCKS ImportError 修复补充验证

同日继续复现完整 debug EXE 继承 SOCKS `ALL_PROXY` 的路径。根因是 OpenAI/HTTPX 在构造 LLM client
时需要 `socksio`，而 Runtime 当时未安装该依赖；该条件可稳定触发 `ImportError`，不是 FastEmbed/ONNX
故障或低概率导入竞争。候选在 `requirements.txt` 固定 `socksio==1.0.0`，并在 Memory 子进程创建
Qdrant/ONNX 前预加载 `socksio` 与 `openai`。未知 `ImportError` 只在同一 Memory 子进程内等待 100 ms
后恢复一次，不清理共享 `sys.modules`，不创建第二个 Memory 子进程。快速接话仍未接入 Runtime v2，
本次也没有引入 PyTorch、SentenceTransformer 或 Transformers。

依赖与定向验证结果：

- 当前 `runtime\python.exe` 已安装 `socksio 1.0.0`；`runtime\python.exe -m pip check` 返回
  `No broken requirements found.`。
- requirements dry-run exit code 0；对 PyTorch、Torch、SentenceTransformer 和 Transformers 的解析
  命中为 0。
- `tests/unit/test_memory_store_resources.py` 为 31 passed；与 Core Memory 边界合并的定向回归为
  45 passed。新增覆盖依赖存在、依赖缺失脱敏、首次 `ImportError` 后成功、连续两次 `ImportError`
  后降级，以及非 `ImportError` 不重试。
- 强制 SOCKS 轮次的四个依赖事件只包含固定事件名、阶段、结果、PID/耗时等有界元数据；对代理 scheme、
  代理环境变量名、Windows 路径、Traceback 和异常正文特征的实机日志扫描命中均为 0。

使用 `cargo build --manifest-path .\desktop\src-tauri\Cargo.toml --locked` 重新构建同一 debug EXE 后，
完成以下真实启动：

- 普通继承环境两轮从 `shell_started` 到 `mem0_ready` 分别为 14.542 秒和 9.522 秒；两轮均依次写入
  `llm_dependency_socksio_started/completed` 与 `llm_dependency_openai_started/completed`，无固定失败
  事件。用户在第二轮真实界面反馈“现在可以了”；该反馈仅记录可见故障已消失，不代替正式人工验收。
- 强制只保留一个不可连接的本地 SOCKS `ALL_PROXY`，并移除更高优先级 HTTP/HTTPS 代理变量以确保
  HTTPX 进入 SOCKS 分支。该轮 14.146 秒进入 `mem0_ready`，无依赖失败、无 `llm_create` 重试、无
  Memory 降级，满足正常启动不超过 20 秒的目标。
- 普通轮次和 SOCKS 轮次均从应用内“退出”菜单结束；最终复扫 Shell、Core Host 和 Memory 子进程残留
  为 0。

本补充候选的独立 Harness 结果：

- `runtime\python.exe -m harness run core-host`：4/4 passed；Core Host unit 121 passed、真实进程
  integration 34 passed、Provider/模型 25 passed、Memory 17 passed。报告
  `temp/harness/20260809T054110.704644Z-core-host.json`。
- `runtime\python.exe -m harness run runtime-v2-shell`：6/6 passed；前端 132 passed，Rust 角色外观
  9 passed、角色表现 8 passed、产品 Shell 8 passed、窗口几何 23 passed、窗口交互 23 passed。报告
  `temp/harness/20260809T054036.699767Z-runtime-v2-shell.json`。
- `runtime\python.exe -m harness run smoke`：2/3 passed，报告
  `temp/harness/20260809T054021.446603Z-smoke.json`。唯一失败仍是既有
  `test_timeout_and_utf8_output_are_actionable`：20 ms 超时先于本机 Python 子进程的 UTF-8 首行输出；
  未修改 Harness 或测试规避该波动。

追加记录后，`runtime\python.exe -m harness run docs` 为 2/2 passed，报告
`temp/harness/20260809T054247.062130Z-docs.json`。随后连续两次执行
`runtime\python.exe -m harness verify WP-4-01A`，均返回 exit code 1 / `failed`：

- 首次报告 `temp/harness/20260809T054301.446838Z-WP-4-01A.json`；
- 透明重跑报告 `temp/harness/20260809T054317.868280Z-WP-4-01A.json`。

两份报告均为 docs 2/2 passed，`smoke` 在同一个 20 ms Harness 自测上失败，后续 core-host 与
runtime-v2-shell 共 12 个 case 依首失败策略标为 blocked。它们在本次 verify 前的独立运行分别为 4/4
和 6/6 passed，且完整 EXE 的普通与强制 SOCKS 实机启动均已进入 `mem0_ready`。本候选因此不能声明
自动门全绿或 Work Package 完成；人工验收状态也未修改，最终状态继续只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

## Harness 纠正验收后的恢复预检

2026-08-09，项目负责人验收 WP-H-02A 后，唯一状态源已把 WP-4-01A 恢复为 `active`。随后在干净
工作树和 HEAD `3c984f187ee6e5b8f1549bf96fdf21055f2e66fd` 上执行
`runtime\python.exe -m harness check WP-4-01A`：当前 WP、WP-4-01 与 WP-H-02A 两项依赖、固定 base
祖先关系、全局保护、activation 关闭、测试删除和 task 工作树修订均通过；唯一失败为 allowlist。

WP-4-01A 的不可移动 base `051ac908497ec361292431b31ec8a712be83893e` 早于后来插入并已验收的
WP-H-02A，因此 changed-set 仍包含后者的六个专属文件：自动验证 record、spec 索引、spec、Harness
README、task 和 Harness runner 单元测试。它们不是 Memory 候选修改，也不应加入 Memory task
allowlist；本记录没有移动 base、扩大 allowlist、修改 Harness 或隐藏这组已提交前置依赖变化。

为保留机器证据，继续执行 `runtime\python.exe -m harness verify WP-4-01A`，得到 exit code 1 /
`failed`，报告 `temp/harness/20260809T060935.598465Z-WP-4-01A.json`。由于 scope 预检失败，docs、smoke、
core-host 和 runtime-v2-shell 共 15 个唯一 case 全部为 `blocked`，没有执行任何产品测试；这不是 Memory
测试失败，也不能表述为自动门通过。WP-4-01A 保持 `active`，等待以独立治理纠正恢复固定 base 任务在
已验收插入依赖之后的安全续跑语义。
