---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 项目负责人验收声明

## 日期与结论

2026-08-03，项目负责人在当前开发会话中明确声明：

> 我确认 WP-3V-01 人工验收通过，批准标记 accepted 并进入 WP-4-01。

该声明关闭冻结任务契约中的三项人工门，并授权将 WP-3V-01 标记为 `accepted`、按冻结契约激活
WP-4-01。Work Package 状态只维护在
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个状态真相源。

## 人工验收边界

- 项目负责人直接启动当前 Runtime v2 EXE，使用既有开发 Provider 配置完成真实回复与取消，并确认
  Core 强杀后的新 generation 恢复、兼容历史追加、正常退出、共享锁立即重获和相关进程零残留。
- 项目负责人审查同一候选 SHA 的 Windows x64、macOS arm64 和 Linux x64 证据，以及脱敏 manifest、
  日志、CAP-004 架构验证边界和只回退验证设施的独立回退范围。
- Legacy Qt 只保留为迁移参考与隔离数据 oracle，不是可见 UI、最终产品入口或用户回退路径；本次验收
  不改变 Phase 7 完成迁移后删除 Legacy Qt 的既定方向。

## 候选与自动证据

验收候选为 `dabcd7733548c0aa2953f02578e5e3f79a6200fc`。同一 SHA 的远端证据为：

- [Runtime v2 platform foundation run 30760748752](https://github.com/Rvosy/Sakura/actions/runs/30760748752)：
  Windows x64、macOS arm64 和 Linux x64 全部成功。
- [Test run 30760748801](https://github.com/Rvosy/Sakura/actions/runs/30760748801)：Harness、docs、Unit 和
  UI 检查全部成功。

本地真实 Windows 组合进程门、5/5 required Harness profiles、Rust 测试、release build 和格式检查均已
通过；最终隔离导入修复与验证提交分别为 `339a1caa` 和 `dabcd773`。详细缺陷与验证事实见
[`WP-3V-01-CI-ISOLATED-IMPORT-DEFECT.md`](WP-3V-01-CI-ISOLATED-IMPORT-DEFECT.md) 和
[`WP-3V-01-CI-STABILIZATION-VALIDATION.md`](WP-3V-01-CI-STABILIZATION-VALIDATION.md)。历史自动报告中的
人工项 `pending` 是负责人声明前的事实，不倒改原报告。

## 能力结论与后续处理

本次验收证明真实 Sakura Assistant 领域链可以由 Runtime v2 的无 Qt Core、Rust Gateway 和 Tauri Shell
承载，因此授权把 CAP-004 推进为 `architecture-validated`。该结论不是完整产品等价验收，CAP-004
仍须在 Phase 7 达到 `parity-accepted`，也不预先通过 Memory 或其后的业务 Work Package。

WP-4-01 必须先冻结真实 Memory 消费者、协议字段、故障矩阵、三平台环境、人工步骤和独立回退，再按
Harness activation 激活；不得只凭 Phase 4 暂定总表直接修改产品代码。
