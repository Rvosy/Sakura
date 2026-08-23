---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# Core 明确失败与手动恢复

Runtime v2 在 Core 启动失败、连接中断或进程崩溃后停止自动恢复。主窗口保留，但新 Core generation 只由
用户明确点击“重试连接”或设置应用发出的显式 Restart 创建。

## Supervisor

Supervisor 状态精确为：

```text
stopped -> spawning -> running -> stopping -> stopped
                       |             |
                       +-- failure --+-> failed
failed -- Retry --> spawning
running -- Restart --> stopping --> spawning
```

不存在 `restarting` 状态、restart token、backoff、restart budget 或 retryable 分类。重复 Start/Stop/Restart/
Retry 意图由同一个串行 Supervisor 幂等归约，但不会创建隐藏的重试计划。

## 失败顺序

启动、握手、初始化、连接或进程失败时必须：

1. 保存该 generation 的首个 `FailureReason`；后续清理错误只能进入诊断日志。
2. 立即取消 generation，拒绝其后续 request/event/callback/resource。
3. 完整停止并验证 Core 进程树、管道和 generation 私有资源。
4. 清理成功后进入 `failed`；清理未完成时保持阻塞，不得创建新 generation。

旧 generation 的迟到回流不得覆盖 failure、当前 generation、Snapshot 或 UI。应用退出优先级最高；退出开始
后任何 Retry/Restart 都不能再 spawn。

## 公开快照

Shell 只发布五态 Supervisor、当前 generation 身份和：

```json
{
  "failure": {
    "code": "unexpected_exit",
    "message": "Core 进程意外退出。"
  }
}
```

正常状态下 `failure` 为 `null`。`message` 必须来自固定安全映射，不包含异常文本、命令行、路径、凭据或
用户数据。快照不包含自动重启计数或 pending 标志。

## 用户体验

- Core 失败会终止当前活动回复一次，并显示安全失败原因与“重试连接”。
- 当前 WebView、未发送草稿、已完成回复、历史导航和窗口几何保持。
- 不显示“正在自动重连”，也不自动倒计时。
- `Retry` 只在 Supervisor 为 `failed` 且旧进程树已清理时生效，并创建不同的新 generation。
- 配置确实要求 Core 重启时仍使用显式 `Restart`；它不是失败重试。

## 验证

覆盖启动、Restart、崩溃清理、首因保留、手动 Retry、重复意图、旧 generation 回流、清理阻塞和应用
退出。WP-3V-01 的失败场景必须观察 `failed`，再显式 Retry，并验证新 generation。

取舍见 [`ADR-0030`](../../adr/0030-core-explicit-failure-and-manual-retry.md)。
