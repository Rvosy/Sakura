---
kind: plan
status: stabilizing
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-15
---

# WP-4-04 Python 插件能力等价实施计划

## 1. 目标与边界

实现 [`normative Spec`](../../specs/runtime-v2/WP-4-04-python-plugin-capability-parity.md) 与
[`ADR-0016`](../../adr/0016-runtime-v2-generation-private-plugin-worker.md)；路线图状态只读
[`work-packages.md`](work-packages.md)。

本 WP 不修改 `data/**`、`characters/**`、`plugins/**`、`third_party/**` 或 `tools/mcp/**`，不实现
renderer、Qt widget/tools tab、浏览器/移动桥接、TTS、截图、安装更新、签名或通用 worker 平台。

## 2. 分阶段实施

### A. 契约与 RED

- 复核 ADR、Spec、本文、索引和 `journey-plugins` baseline。
- 增加 Python/Rust/frontend RED，分别冻结 worker lifecycle/贡献调用、gateway/受控后代和设置
  generation 重绑定。

退出条件：相关文档检查通过，RED 证明 Runtime v2 尚未建立插件纵向链，并明确受影响模块、真实数据安全
边界和回退路径。

### B. 私有 worker 与 descriptor

- 从现有插件领域提取不导入 Qt 的 manifest/discovery/permission/descriptor 语义；Core 创建 generation
  私有 worker，通过有界私有 RPC 加载 fixture 插件并发布脱敏状态。
- 实现 pending/writer queue、initialize/call/event/settings/close deadline、generation/token identity、
  EOF/crash/污染处理和逆序 shutdown；worker/后代进入受控进程树。
- 严格禁止 Core 导入插件实现；失败插件不隐藏健康插件，required/unknown API/permission/duplicate 有稳定
  reason code。

退出条件：Core readiness/control 不受慢或坏插件阻塞；正常/故障/强杀/重建后 worker、pipe、thread、
handler、timer、句柄和后代归零。

### C. Tool、prompt/context 与 event

- 将插件工具 descriptor 注册到既有 ToolRegistry，复用 Operation、取消和 Action ID，调用前重验 identity；
  结果、异常、timeout 和 worker crash 收敛为有界 ToolResult。
- 以 opaque contribution ID 调用 prompt patch/context provider，保留 untrusted、预算、截断、防注入和
  敏感字段白名单。
- 从 Core 明确派发 app/message/tool 摘要事件；单 handler 超时/失败只隔离对应插件，旧 generation 迟到
  丢弃。

退出条件：成功、拒绝、取消、超时、插件 crash 与 Core restart 都有唯一终态；内置 Tools/MCP、聊天和
control 行为不回归。

### D. 设置、数据与可观测性

- 增加插件列表/状态 DTO、启停保存、声明式 field get/save/action；保存沿用 Python owner、revision、原子
  替换和受控 Core restart，required 插件不可禁用。
- 设置 WebView 只渲染获准 schema/value；旧 generation 状态/action 丢弃，保存失败保留草稿并显示稳定
  reason。不能迁移的贡献明确 `unavailable`。
- 接入 WP-4L-01 固定事件与脱敏；用隔离 assistant root 验证允许写集，真实 `data/**` 和 `plugins/**`
  零变化。

退出条件：设置重开/保存失败/restart/旧响应/窗口关闭通过 Python、Rust 和 frontend journey；日志 sentinel
与受保护路径 diff 为零。

### E. 候选与验收

- 运行相关产品 profiles：`docs`、`smoke`、`core-host`、`runtime-v2-shell`、`journey-tools` 和
  `journey-plugins`，并运行完整相关 Python/Rust/frontend 回归与三平台 Runtime v2 CI。
- 在 Windows 隔离 root 完成 fixture 插件加载、工具直接执行、context/event、禁用/重启、设置/action、Core
  crash/recovery 和退出零残留，扫描 DTO/日志与数据 diff。
- 将已发生的自动验证写入 record。自动验证和真实设备验收共同作为路线图状态评审证据；Product Harness
  报告只记录各 case 的 `passed`/`failed`，不代替负责人验收。

## 3. 故障矩阵

覆盖缺失/损坏/future manifest、损坏 overrides、未知 API/permission、required/disabled、ID/entry 不一致、
重复 tool/patch/provider/section、恶意 schema/value、巨大 prompt/context/result/event、initialize/call/
event/settings/close 超时、GIL hang、stdout pollution、半帧/EOF、worker/后代 crash、延期 Action ID 基础
设施不被助手激活、Operation 取消、Core crash/restart、旧 generation 迟到、设置保存冲突和 shutdown deadline。

## 4. 回退

先关闭插件设置 feature 和 contribution 注册，使当前 generation 失效并正常关闭 worker；再按 WP-4-04
产品提交逆序 revert。回退不得删除、恢复或改写 `plugins.yaml`、插件安装目录、插件私有数据、日志或其他
用户数据；超时后只回收当前 generation 明确拥有的 worker/后代。
