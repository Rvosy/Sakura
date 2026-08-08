---
kind: adr
status: superseded
audience: maintainer
source_of_truth: self
updated: 2026-08-08
---

# ADR-0008：以任务契约驱动 Agent Development Harness

> 状态：Proposed
> 决策日期：2026-07-31
> 执行状态来源：[`work-packages.md`](../../plans/runtime-v2/work-packages.md)
> 已由 [`ADR-0009`](../../adr/0009-lean-agent-development-harness.md) 替代；本文仅保留 v1 历史决策。

## 背景

现有 Harness 能从 `suites.json` 选择验证 profile、顺序执行 case 并原子写入 JSON 报告，但它不知道
当前 Work Package、任务允许范围、依赖、受保护路径或人工验收状态。完全由 AI Agent 开发时，仅靠
人类文字约束无法稳定阻止跨 Work Package 实现、修改任务标准后自证通过，或遗漏 staged、untracked
和已提交差异。

现有 ADR、Spec、Work Package、`AGENTS.md`、Tests 与 Harness 各自继续承担原职责；本决策只建立它们
之间的机器可执行连接，不替代任何一层。

## 候选方案

### 方案 A：版本化 JSON 任务契约与本地确定性检查

每个非微小任务拥有严格 JSON 契约。Harness 从唯一 Work Package 真相源读取状态，以 Git 和
`suites.json` 校验范围、依赖、冻结契约与验证入口，并输出统一 JSON 报告。CI 复用同一入口。

### 方案 B：继续依赖 AGENTS.md 和人工审查

不增加可执行约束，Agent 自行解释允许目录和验收条件。该方案无法为 CI 提供同一规则，也无法稳定
发现工作树四种差异来源或契约自我放宽。

### 方案 C：引入通用 Agent/工作流平台

使用数据库、远程服务、签名或多 Agent 调度保存任务状态。它扩大信任边界、依赖和维护成本，超出
当前仓库基础设施需求。

## 决策

采用方案 A，并遵守以下边界：

- 任务契约是 `harness/tasks/<WP-ID>.json`；schema 版本独立于 profile 报告版本。
- Work Package 当前状态仍唯一来自 `docs/plans/runtime-v2/work-packages.md`，契约不得复制或覆盖状态。
- `base_ref` 必须直接保存完整 40 位提交 SHA，固定任务实现差异起点；禁止 `HEAD`、分支和 tag。所有
  Git 命令使用 argv、仓库根 cwd 和有界 timeout。
- 激活或契约修订在 `harness/activations/<WP-ID>/<sequence>.json` 保存独立锚点。Harness 从 Git 历史
  定位锚点文件首次加入的提交，并要求该提交只改变任务契约、状态源、契约引用文档和锚点本身；实现
  代码必须在后续提交中出现。当前契约的 `base_ref` 必须与最新锚点完全一致，不再归一化或允许
  `base_ref-only` 变化。
- 契约、引用的 Spec/ADR/Plan 和唯一状态源以锚点提交中的内容冻结。实现阶段若改写这些治理文件，
  自动门返回 `owner_review_required`，不得直接通过；合法修订必须新增递增锚点并接受独立负责人审查。
- changed set 是 `base_ref..HEAD`、index、unstaged 和 untracked 的并集，路径统一为仓库相对 POSIX 形式。
- v1 路径规则只接受精确文件和 `directory/**` 两种形式，避免 Python `fnmatch` 让 `app/*.py` 意外匹配
  多级目录。
- 依赖策略默认禁止；只有契约显式列出 manifest/lock 文件时才允许变化，且声明文件与锁文件按同一
  变更审查，不能把锁文件一律视为失败或安全。
- 人工验收只汇总为 `pending/passed/failed`；Agent 不得自动填写 passed，也不得因此把 Work Package
  标记 accepted。
- `verify` 在 preflight/scope/dependency 门失败后不运行昂贵 profile；诊断模式只能扩大报告，不能改变
  通过标准。
- v1 只使用 Python 标准库、Git 和既有验证工具，离线、无 API Key、无 `shell=True`。

`WP-H-01` 是一次性 bootstrap：在它 accepted 前，新命令尚不存在，因此它的激活只能使用现有
`run docs`、`run smoke`、定向 pytest 和人工契约审查；该例外只适用于建立 Harness 本身，必须在
`WP-H-01` accepted 时关闭，不能被后续产品 Work Package 复用。

## 后果

收益：本地 Agent 与 CI 使用同一任务边界；报告能区分自动失败、调用错误和人工待办；已有 profile 与
测试体系保持兼容。

代价：任务激活前多一个契约准备提交；Markdown Work Package 表格成为严格解析输入，格式错误必须
失败关闭；Git 范围检查不是权限系统，仍需要代码审查和分支保护。

本 ADR 在 `WP-H-01` 实现、故障测试和 CI 自测通过前保持 `Proposed`。若以后引入远程任务服务、签名、
数据库或通用工作流引擎，应新增 ADR，不在本决策中逐步扩张。
