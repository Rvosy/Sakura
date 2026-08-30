---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 Router 事件顺序缺陷记录

## 发生了什么

2026-08-03，Windows x64 真实组合验收
`desktop/tests/windows_wp_3v_01_assistant_architecture_acceptance.ps1` 在第一轮真实聊天中收到
`chat.started`，但未收到对应的 `chat.completed`，最终报告
`WP_3V_01_CHAT_TERMINAL_TIMEOUT:chat.completed`。本轮本地 Provider 已收到一次请求并完成响应，验收
进程树在失败清理后残留为零。

验收器的启动时序、main WebView 事件作用域、Tauri JSON 包装和公开事件 `type` 字段问题已由
`d47cd414` 独立修正。修正后的诊断只观察到公开 `chat.started`，证明原始 marker 超时已被收敛为
生产事件链缺陷。

## 根因与归属

使用相同脱敏数据集、Provider 配置和响应直接驱动 bundled Python Core 时，Core 严格按
`chat.started`、`chat.completed`、`chat.send` response 的顺序输出，排除 Python Assistant、Provider
和历史写入故障。

Rust `CoreHostRouter` 将普通事件和关键终态放入两个独立队列；消费者每次固定先读取关键队列。
当本地 Provider 足够快、两个事件在同一次轮询前均已入队时，Router 会先交付
`chat.completed`。Gateway 按既有契约拒绝“终态早于 started”，随后只能发布 `chat.started`，造成
活动回复永远没有终态。该缺陷归属 WP-2-01 的并发 Router 顺序与有界事件队列，不归属
WP-3V-01 验收设施。

## 治理结论

项目负责人已批准执行冻结回退条款：WP-3V-01 退回 `planned`，只重新打开 WP-2-01 进入
`stabilizing`。当前状态仍只以
[Work Package 总表](../../plans/runtime-v2/work-packages.md) 为准。

修复不得通过延迟本地 Provider、放宽 Gateway 顺序校验、扩大无界队列或修改聊天协议来规避复现。
必须以 Rust 回归测试证明同一快速事件批次保持 wire order，同时保留终态的有界容量保证；修复后
重新运行 WP-3V-01 真实 Windows 组合验收。

## 修复与自动验证

稳定化实现提交 `fab46beb` 保留普通/关键两个有界队列，为同一 reader 读取的事件分配单调序号，
并在消费端按两个队头的序号合并交付。队头缓存继续计入各自原有容量，因此关键事件预留仍严格为
8，不因排序修复扩成 9；协议 envelope、Gateway 校验和 Provider 时序均未改变。

TDD 先证明旧实现把 `chat.completed` 提前，再证明修复后 `chat.started`、`chat.completed` 保持 wire
order；第二条回归测试证明缓存中的关键队头仍占用命名容量。最终本地证据：

- Router 定向：10 passed。
- 完整 Rust 单线程：239 passed、24 ignored、0 failed。首次默认并行运行的 3 个失败均来自共享
  Windows mutex 测试互相污染；精确回收该轮遗留的仓库 Shell/Core/WebView 子树后，稳定的
  `--test-threads=1` 完整运行全绿。
- WP-3V-01 真实 Windows 组合验收：`status=passed`、`provider_requests=4`、`core_kills=1`、
  `cancel_terminals=1`、`generation_rehydrated=true`、`process_residue=0`、`sensitive_evidence=0`；
  manifest 只有 `data/chat_history/fixture.jsonl` 发生预声明变化，临时根已删除，Legacy oracle 回读兼容。
- `harness verify WP-2-01`：四个 required profiles 全部通过，20 项自动检查通过、0 失败；仅负责人
  人工复核项保持 pending。

这些证据只形成 WP-2-01 的重新稳定化候选，不由 Agent 标记 `accepted`。负责人重新验收
WP-2-01 后，才能为 WP-3V-01 建立新的 activation 并恢复组合验证。
