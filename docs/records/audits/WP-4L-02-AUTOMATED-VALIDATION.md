---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-12
---

# WP-4L-02 人类可读运行日志与 Prompt Trace 自动验证记录

## 候选与环境

2026-08-12，在 Windows x64、分支 `refactor/tauri-runtime-v2` 上验证实现候选
`cb7066b5c1f3a77d94ff86da5c70cc69f8f4007a`。固定 base 为
`80764fa55d9dbb69e44f4bd5f634093f44d79010`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只保存已经执行的自动证据，
不填写负责人验收，也不把候选标记为 `accepted`。

候选按 [normative Spec](../../specs/runtime-v2/WP-4L-02-human-readable-runtime-log-agent-trace.md) 将
`sakura-runtime.log` 改为 Rust 单写者的人类可读中文文本，并把旧 JSONL 整组原样归档；新增默认开启、
仅本机保存的 `sakura-agent-trace.log`。Trace 按真实 Provider payload 顺序记录静态 section 大小、历史、
当前输入、动态 context、Memory、工具定义、模型原始回复和 usage；operation 在 staging 终态后成块提交。
普通正文保留，凭据、URL userinfo 和二进制正文始终移除，任何写入失败都不改变产品终态。

## Harness 自动门

- `runtime\python.exe -m harness check WP-4L-02`：当前任务、accepted 依赖、固定 base、allowlist、全局保护、
  activation 关闭、测试删除和 task 修订检查全部通过；越界与保护文件均为 0。
- `runtime\python.exe -m harness verify WP-4L-02 --report
  temp\harness\WP-4L-02-implementation.json`：绑定上述候选 SHA，退出码 3，机器状态
  `manual_pending`；17/17 唯一自动 case 通过，failed、pending 和 blocked 均为 0，唯一未完成项为
  normative Spec 指向的负责人验收。
- required profiles 经 Harness 去重后覆盖 `docs` 2 项、`runtime-v2-shell` 6 项、`python-full` 3 项、
  `journey-observability` 3 项和 `journey-agent-trace` 3 项。

## 关键结果与扩大回归

- Python 全量：unit 660 passed/6 skipped，integration 58 passed/2 skipped，Legacy Qt UI 24 passed。
  Qt-free 当前产品拓扑测试确认 Agent Trace 设置读取不会导入 PySide6，Core 能正常 ready 并完成真实聊天。
- Runtime v2 前端全量 148 passed；Agent Trace 前端 journey 3 passed，确认只有一个本地私密开关、保存仅发送
  布尔草稿，并在 Core generation 重启后原位重绑。
- Agent Trace Python journey 14 passed：逐项比对最终 Provider payload 与 trace prompt，覆盖尾部 system、
  尾部 user、合并 system、tool message、兼容重发、结构化/文本/非法回复、有效结果变化、两空行、长文本、
  staging 恢复、并发块、轮转保留，以及正文保留与凭据/二进制零命中。
- Observability journey 的 Python 10、Rust 9、前端 5 项全部通过；旧 JSONL 断言已迁移到文本日志契约，
  Core stderr bridge、WebView 受控诊断、脱敏、背压和故障隔离保持有效。
- 额外运行 `smoke` 3/3 与 `core-host` 4/4 profiles：Harness 12/31 项、Core protocol 20 项，Core unit 149、
  integration 34、Provider/模型 25、Memory 17 项通过。
- `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check` 通过；完整 Rust 使用
  `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1` 得到
  298 passed、24 ignored、0 failed。默认并行全量曾触发既有角色外观测试的临时目录同名竞态；该用例单独
  运行与串行全量均通过，未在本 WP 越界修改其测试夹具。
- `git diff --check`、`docs` 和独立 `journey-agent-trace` 均通过；日志写入失败、开关关闭、旧 JSONL 归档、
  32 MiB/跨日轮转、30 天/512 MiB 保留均有定向覆盖。

## 当前结论与人工边界

自动门通过后，WP-4L-02 进入 `stabilizing`。负责人验收应在真实应用中确认：运行日志呈现旧控制台样式；
一次包含历史、Memory、工具调用和最终回复的对话按真实 payload 顺序形成可读 request/reply 文档；关闭
开关后不再创建新 trace；本机日志中普通对话可见，但实际 API Key、Authorization、Cookie、密码、token、
URL userinfo 与二进制正文零命中；日志故障不影响聊天、工具确认、Core health 或退出。

上述人工检查完成并由负责人明确声明前，不得把 WP-4L-02 标记为 `accepted`。本记录不声称远端
Windows/macOS/Linux CI 或未实际执行的设备步骤已经通过。

## 可读性缺陷复验

2026-08-12，负责人真实日志检查发现普通日志成功轮询刷屏和 Agent Trace 过长，WP-4L-02 退回
`active`。修复候选 `4d0bef57142db1742e91443330f64e30fbdfc81a` 完成后重新执行固定自动门：

- `harness check WP-4L-02` 通过，越界、保护文件、测试删除和未审 task 修订均为 0。
- `harness verify WP-4L-02 --report temp\harness\WP-4L-02-readability.json` 为 17/17 自动 case 通过、
  0 failed、0 blocked，机器状态 `manual_pending`。
- Agent Trace journey 15 passed；23 条短历史和 18 个工具的固定合成 request 为 169 行，逐项保留历史
  role、正文、顺序、工具名称与成本，未以删除动态正文换取行数。它与 840 行缺陷现场的正文长度不同，
  不作为同 payload 的精确压缩率。
- Rust WP-4L-02 journey 7 passed，覆盖通用成功命令强制 debug、失败保持 warning 和毫秒浮点尾数清理；
  完整 Rust 299 passed/24 ignored。
- Python 全量 unit 661 passed/6 skipped、integration 58 passed/2 skipped、Qt UI 24 passed；`docs`、
  `smoke`、`core-host`、`runtime-v2-shell`、`journey-observability` 和 `journey-agent-trace` 均通过。

WP-4L-02 仅恢复 `stabilizing`，负责人需要重新检查真实默认 info 日志与一次完整工具对话 Trace；本次自动
复验不沿用缺陷前的人工判断，也不声称本 Work Package 已 `accepted`。
