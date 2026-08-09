---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-09
---

# WP-H-02A Harness 短超时输出测试确定化自动验证记录

## 候选与根因

2026-08-09，在分支 `refactor/tauri-runtime-v2`、固定 base
`817dc9b1909b5f145c95f3e8a37b7d8bcb776af5` 上验证 WP-H-02A。task 初始提交为
`c8311bf7bc6b2818655b674a8fd00196f24291cf`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准，本记录不填写人工验收。

失败测试原先用新 Python 进程执行 `print('中文', flush=True)`，同时把 case timeout 固定为 20 ms，并断言
超时报告必含该首行。本机 40 次无 timeout 探针测得 Python 首行进程总耗时为 28.616–34.187 ms，P50
30.116 ms、P95 32.449 ms；另 50 次真实 20 ms 探针全部在产生 stdout 前超时。因而失败源是测试对
解释器启动速度的错误假设，不是 Runner 丢失了已经产生的 UTF-8 bytes。

## 实现边界

- 生产 `harness/runner.py` 无需修改；manifest 中的 20 ms 仍原样传入 `subprocess.run`，没有启动宽限、
  自动重试、sleep 或超时后继续执行。
- 原竞态测试拆为三条独立证据：真实 Python 进程正常 UTF-8 输出；真实 20 ms timeout 只断言
  `timed_out=true` 与 `exit_code=null`；注入 `TimeoutExpired` 的 str/bytes 输出均精确进入报告。
- timeout、失败状态、UTF-8 `errors="replace"`、pipe 排空、JSON report、临时根和 fail-fast 契约均未改变。
  产品代码、suite manifest、Runtime、依赖、用户数据和人工验收语义均未修改。

## 自动结果

- `runtime\python.exe -m harness check WP-H-02A`：当前任务、依赖、固定 base、allowlist、全局保护、
  activation 关闭、测试删除和 task 修订检查全部通过。
- `runtime\python.exe -m pytest tests\unit\test_harness_runner.py -q`：12 passed，0.90 秒。
- 完整 `smoke` 连续运行 10 次，每次均为 3/3 passed；报告依次为
  `20260809T055444.405026Z-smoke.json`、`20260809T055514.432582Z-smoke.json`、
  `20260809T055543.991626Z-smoke.json`、`20260809T055613.351796Z-smoke.json`、
  `20260809T055644.119625Z-smoke.json`、`20260809T055712.948852Z-smoke.json`、
  `20260809T055741.714506Z-smoke.json`、`20260809T055810.181204Z-smoke.json`、
  `20260809T055839.425791Z-smoke.json`、`20260809T055911.533652Z-smoke.json`，均位于
  `temp/harness/`。
- `runtime\python.exe -m harness run unit`：618 passed、6 skipped，报告
  `temp/harness/20260809T060008.173279Z-unit.json`。
- `runtime\python.exe -m harness run docs`：2/2 passed，报告
  `temp/harness/20260809T055923.860565Z-docs.json`。

最终 `verify` 结果在实现与本记录提交后的同一候选上继续追加；自动门全绿也只允许表述为等待负责人
验收，不把 WP-H-02A 或暂停的 WP-4-01A 标记为 `accepted`。
