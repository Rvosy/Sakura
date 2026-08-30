---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 CI 稳定化项目负责人批准

## 日期与批准声明

2026-08-03，项目负责人审查 PR #147 首轮三平台 CI 失败的三个已定位根因后明确声明：

> 批准按上述三个根因实施 WP-3V-01 CI 修复，并为 stabilizing 状态建立新的负责人治理锚点。

该声明授权新增 `harness/activations/WP-3V-01/0003.json`，以当前 `stabilizing` 状态源、任务契约和
冻结治理文档建立新的负责人基线。当前 Work Package 状态仍只维护在
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个状态真相源。

## 批准范围

- 修正 macOS/POSIX 进程表解析，允许 `comm` 字段包含空格，并补充回归覆盖。
- 将 WP-3V-01 的 Legacy 参考 oracle 收敛为无 Qt、无可见 UI 的 headless 数据兼容与共享锁 oracle；
  不为三平台 CI 安装 Legacy Qt UI 依赖。
- 保留 Harness 对冻结治理文件和退出码 `3` 的既有语义；以新增 `0003` 锚点完成负责人审查，不放宽
  workflow、任务契约、自动验收或人工验收门禁。

## 非目标与后续

本次批准不授权修改 Legacy Qt 产品入口、前置生产实现、Provider 时序、共享锁协议或用户数据，也不
表示 WP-3V-01 已通过三平台 CI 或人工验收。修复候选仍须完成 required profiles、真实 Windows 组合门、
Rust/格式门，并在同一候选 SHA 上重新取得 Windows x64、macOS arm64 和 Linux x64 证据。
