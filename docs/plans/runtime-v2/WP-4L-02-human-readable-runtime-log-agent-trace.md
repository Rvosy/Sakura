---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-12
---

# WP-4L-02 人类可读运行日志与 Prompt Trace 实施计划

## 1. 目标、基线与边界

以项目负责人接受的 WP-4-03 最终候选
`80764fa55d9dbb69e44f4bd5f634093f44d79010` 为固定 base，实现
[`normative Spec`](../../specs/runtime-v2/WP-4L-02-human-readable-runtime-log-agent-trace.md) 与
[`ADR-0013`](../../adr/0013-human-readable-runtime-log-and-private-agent-trace.md)。任务契约为
`harness/tasks/WP-4L-02.json`，不创建 activation。

不修改仓库中的 `data/**`、`characters/**`、`third_party/**` 或 `tools/mcp/**`，不增加查看、清除、导出、
上传或回放能力。

## 2. 分阶段实施

### A. Runtime 文本日志

- 保留 Rust queue、bridge、相关 ID 和故障隔离，把持久编码从 JSONL 改为固定中文单行文本。
- 在 writer 打开新格式前识别并整组原样归档旧活动/轮转 JSONL；随后只允许纯文本轮转。
- 审计现有事件等级，把 polling 与普通成功降为 debug/trace；为故障和关键生命周期冻结中文消息与属性。

退出条件：Rust 测试覆盖示例格式、控制字符、归档、轮转、并发顺序、背压和写失败；同名文件零混写。

### B. Trace recorder 与隐私基础

- 新增 `AgentTraceSettings`、`AgentTraceRecorder`、operation staging、启动恢复、提交锁、日期/32 MiB 轮转、
  30 天/512 MiB retention、100 列自由文本和 1 MiB 截断。
- 建立递归凭据过滤、URL userinfo 清理和二进制 metadata/hash 投影；staging 写入前执行同一过滤。
- 在 Core/Legacy bootstrap 创建 recorder；无外层 operation 时创建本地 operation，有外层 interaction ID 时
  复用并由真实终态完成成块提交。

退出条件：单元测试覆盖普通正文保留、credential/binary 零命中、并发块、崩溃恢复和全部文件故障隔离。

### C. 最终 payload provenance

- 为待发送消息增加内部 provenance，标记 history、user_input、assistant_tool_call、tool_result 和动态
  runtime context；工具 helper 与 reply-repair 构造点必须显式标记。
- Prompt recipe 暴露静态 section 计数，ContextSnapshot 保留 selected/dropped 顺序；Memory recall fragment
  扩展实际 id、score 和 source metadata。
- `api_client` 在所有 compatibility fallback 完成且真正发送每个 payload 前同步剥离 provenance、构建
  request 文档；system merge 和尾部 user fallback 以实际 placement 表示。

退出条件：mock Provider 对最终 payload 与 trace 逐条相等，Provider payload 深度扫描不到内部字段。

### D. Reply 与有效结果追踪

- 扩展 `ChatCompletionTurn` 保存 usage、原始 message/content、placement 和调用 trace handle；收到 Provider
  message 后、任何业务解析前写 reply 文档。
- 合法 JSON 嵌套展开；普通文本、非法结构、native/pseudo tool calls 分别投影；reply repair 使用下一
  model_call 和 `purpose: reply_repair`。
- 在 AgentRuntime 解析、tone 清洗、语言修复和安全兜底结束时，仅对实际变化的原 reply 增补 effective
  result/change list，不为正常回复复制 JSON。

退出条件：segments、visual observation、普通文本、非法 JSON、repair、tone 和 fallback 展示规则全覆盖。

### E. 设置纵向链与 Journey

- Python 配置服务加入默认 true 的 `agent_trace.enabled`；Runtime v2 Rust 设置 feature 提供严格 load/save
  DTO、原子 YAML 写入和 Core generation 重启；前端仅显示开关与隐私说明。
- 注册并完成 `journey-agent-trace` 的 Python/Rust/frontend 三条定向 case，更新 userdoc、devdoc、索引与
  `CHANGELOG.md`。
- 运行 task required profiles：`docs`、`runtime-v2-shell`、`python-full`、`journey-observability`、
  `journey-agent-trace`；另行运行 Harness 去重禁止同时登记的 `smoke`、`core-host`，以及完整 Rust 回归、
  fmt/check。

退出条件：`harness verify WP-4L-02` 自动门全绿时只进入 `manual_pending`/`stabilizing`，等待项目负责人
验收，不由 Agent 填写 accepted。

### F. 真实运行日志可定位性稳定化

- 冻结 Chat、Memory、Context、API、Tool、Screen、Reply、TTS 的用户可观察事件目录；未注册的内部成功
  事件维持 debug/trace。
- Python 在最终 payload 与原始 Provider reply 边界发出安全业务元数据；Rust writer 统一固定中文、关联 ID、
  事件专属字段顺序和有界单行投影。
- 复用现有 interaction/Agent Trace 身份，不另建平行 trace；用 `op/trace/call` 将普通日志与私密 Trace
  关联。旧 TTS/截图调用点通过兼容映射逐步接入，不在本阶段迁移全部模块。

退出条件：默认 info 下普通对话为少量连续里程碑，工具、截图和 TTS 只按实际发生追加；错误行能够指出
stage/code/status/retryable，且不存在轮询刷屏、正文泄漏或泛化的“Core 运行事件”。

## 3. 故障矩阵

覆盖旧/损坏/超大 JSONL、活动文件空或已是文本、归档重名、日志目录不存在/只读、open/write/flush/
rename/chmod 失败、队列拥塞、进程中途退出、staging 半行/损坏/重复恢复、同 operation 超过 32 MiB、跨日
提交、多个 operation 并发、retention 删除失败、设置 YAML 缺失/损坏/只读、Core generation 重启失败、
Provider 兼容参数剥离和 runtime role 回退。所有失败不得改变产品终态或删除未成功提交的 staging。

## 4. 回退

先关闭新 trace 开关并正常退出，确认 writer、staging handle 和 Core generation 归零；按本 WP 产品提交
逆序 revert，恢复 ADR-0012 JSONL Runtime 编码和无 Agent Trace 行为。回退不删除、截断或改写任何
`sakura-runtime-jsonl-archive-*`、`sakura-agent-trace*`、staging、聊天、Memory 或配置；遗留 trace 只能由
用户自行保管或删除。
