---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# Sakura Product Harness

## Purpose

Product Harness 将 Sakura 已有的 Python、Rust、frontend 和平台检查组织为稳定的产品能力 profile，并为
本地开发和 CI 生成一致的机器可读报告。它验证产品结果，不管理开发者或 Agent 的实现过程。

## Invariants

- 公共 CLI 只提供 `list` 和 `run <profile>`；默认运行 `smoke`，可用 `--report` 指定报告路径。
- `suites.json` 使用 schema v1。每个 profile 按声明顺序引用至少一个已存在且不重复的 case；每个 case
  具有唯一 ID、非空 argv、正 timeout 和字符串环境变量，并且至少被一个 profile 引用。
- Runner 顺序执行所选 profile 的全部 case。单个失败或 timeout 不跳过同 profile 的后续 case。
- `{python}` 和 `{repo}` 分别解析为当前解释器和仓库绝对路径；命令使用 argv 启动，不经过 shell。
- 每次运行使用唯一仓库内临时根，并默认覆盖 `TMPDIR`、`TMP`、`TEMP`；case 环境可以显式覆盖。
- timeout 是硬 deadline：超时必须失败并终止子进程，不重试、不增加隐藏宽限期、不伪造未产生的输出。
- JSON report 保持 schema v1，使用 UTF-8、UTC 和原子替换；保存 case 的命令、耗时、退出码、timeout、
  stdout/stderr，并且不枚举环境变量或凭据。
- 自动状态只使用 `passed`、`failed`。退出码为 `0`（全部通过）、`1`（至少一个 case 失败）、`2`（调用或
  manifest 错误）。人工或设备验收不改变自动结果。
- profile 围绕 Sakura 产品能力和真实 journey 组织。Harness 不读取 Work Package、Git changed-set、
  task contract、allowed paths、activation 或审批状态。

## Compatibility

现有 `smoke`、`docs`、`unit`、`core-host`、`python-full`、Runtime v2 Shell/Window profiles，以及 Tools、
MCP、Plugins、Observability、Agent Trace journeys 保持可调用。平台专用 profile 在不支持的平台由调用方
明确不执行，不产生伪造的通过结果。

## Verification

- `tests/unit/test_harness_runner.py`
- `python -m harness run harness`
- `python -m harness run smoke`

## Related Decisions

- [ADR-0021：Harness 只验证产品结果](../adr/0021-product-harness-outcome-verification.md)
