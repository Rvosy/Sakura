---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# WP-H-02A：Harness 短超时输出测试确定化纠正

> timeout 与输出捕获的当前契约已并入 [`Product Harness`](../../../specs/product-harness.md)；本文只保留
> WP-H-02A 的历史纠正背景。历史状态见 [`work-packages.md`](../../../plans/runtime-v2/work-packages.md)。

## 范围与根因

本纠正包只修复 Harness 自测把“Python 子进程必须在 20 ms 内启动并打印首行”误当作 Runner 输出契约
的问题。Windows 上新 Python 解释器的初始化时间可以超过 20 ms；如果 deadline 先到，子进程尚未产生
任何 stdout，Runner 不可能也不应伪造预期文本。该竞态会让 `smoke` 和所有依赖它的 `verify` 偶发失败，
但不表示 UTF-8 解码或超时后的 pipe 排空丢失了已经产生的字节。

## 行为契约

- manifest 中的 `timeout_seconds` 保持原值。deadline 到达后 case 必须标记 `timed_out=true`、失败并终止
  子进程；不得自动重试、增加隐藏启动宽限期或让进程为补写日志继续执行。
- 报告保存标准库在终止并排空 pipe 后返回的 stdout/stderr；已经产生的 bytes 必须按 UTF-8
  `errors="replace"` 解码。deadline 前没有产生的输出不得被推断、补写或当作 Runner 缺陷。
- 真实 20 ms 回归只验证短 deadline 能稳定产生 timeout 结果，不对尚未完成启动的子进程要求首行输出。
  UTF-8 timeout 输出的保留与 bytes 解码使用注入的 `TimeoutExpired` 确定性覆盖，并另保留正常真实子进程
  的 UTF-8 输出集成覆盖。
- JSON report schema、case/profile 状态、退出码、临时根、changed-set 和 fail-fast 行为保持不变。

## 非目标

本包不修改 Sakura 产品代码、Runtime、依赖、用户数据或现有 suite timeout；不以提高 20 ms 为修复，
不新增通用进程协议、启动握手、后台 reader thread 或平台专用分支，也不修改 Work Package 人工验收语义。

## 验收条件

- 定向 Harness runner 测试必须覆盖真实 20 ms timeout、注入 bytes/str UTF-8 timeout 输出、正常输出、
  非零退出、临时根和原子报告；重复运行不得再依赖 Python 启动速度。
- `docs`、`smoke`、`unit` required profiles 全绿；`smoke` 至少连续运行 10 次，不得再出现
  `test_timeout_and_utf8_output_are_actionable` 波动。
- 自动门全绿后只能进入 `manual_pending`；项目负责人确认未放宽 timeout/失败语义后，才可接受本包并
  恢复 WP-4-01A。
