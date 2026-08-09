---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-09
---

# ADR-0009：Harness 收敛为测试执行与安全边界

> 决策日期：2026-08-08  
> 执行状态来源：[`work-packages.md`](../plans/runtime-v2/work-packages.md)  
> 替代：[ADR-0008](../archive/adr/0008-agent-development-harness.md)

## 背景

ADR-0008 建立了 task contract、Git changed-set、Work Package 状态、activation anchor、治理文件冻结、
自动/人工验收映射和统一报告。它证明了 AI 辅助开发可以被机器可执行的范围门约束，也保住了用户数据、
依赖和测试删除边界。

实际运行七个 Work Package 后，测试执行层仍然简单可靠；维护成本主要来自 activation 历史验证、契约
修订账本、重复的依赖/保护/验收散文，以及 profile 之间重复执行同一 case。Git 已经保存契约历史，Spec
已经保存人工验收步骤，Work Package 表已经保存依赖与状态，再在 Harness 中复制这些事实没有产生相称
的产品置信度。

## 候选方案

### A. 保持 v1 并继续扩展治理

保留 activation、冻结文件和 per-WP 审批语义，为后续 Journey 再增加映射。该方案延续已有行为，但每个
业务 WP 都继续承担治理概念和历史 Git 查询成本。

### B. 删除任务范围门，只保留 profile runner

任务直接运行测试，不检查 changed-set。实现最轻，但失去 AI 辅助开发中很有价值的越界、用户数据、
依赖和测试删除告警。

### C. 采用轻量 task v2

保留测试执行、仓库安全边界、changed-set、Work Package 依赖和 JSON 证据；删除审批账本及重复散文，
由 Git、Spec 和 Work Package 表各自承担唯一职责。

## 决策

采用方案 C：

- task v2 只保存 `schema_version`、`id`、固定 `base_ref`、`allowed_paths` 和
  `required_profiles`。依赖从 Work Package 表读取，人工步骤从对应 Spec 读取，Harness 不再复制。
- WP-H-02 的 `0001` 是最后一个 activation。历史 v1 task 与 activation 原样保留为惰性证据；运行时不
  解析、不校验，也不允许为后续任务新增 anchor。
- `base_ref` 只定义 changed-set 起点。它必须是完整祖先 SHA，并等于 task 文件第一次提交时的值；已提交
  的 allowlist/profile 修订在报告中列出变化字段，不形成批准链。工作树或 index 中的 task 修订必须等待
  负责人审查，且不运行 profile。
- `data/**`、`characters/**`、`third_party/**` 是代码内全局保护边界，任何 allowlist 都不能覆盖。未被
  allowlist 命中的路径直接失败，因此删除 per-WP forbidden/protected/dependency policy。
- 删除独立 `preflight` 命令；`check` 一次执行当前任务、表中依赖、base ancestry、完整 Git 状态、
  allowlist、依赖文件和测试删除检查。
- `verify` 按稳定顺序展开 required profiles，case ID 全局去重后只运行一次，再反推各 profile 状态。
  自动门全绿但仍需人工验收时继续返回 `manual_pending`/exit 3。
- 每次 Harness 运行建立唯一仓库内临时根，并覆盖 `TMPDIR`、`TMP`、`TEMP`；case 显式环境变量仍可覆盖。
- Journey 随真实业务 WP 渐进增加，表达产品用户旅程；WP-H-02 不批量重组历史 acceptance。

## 2026-08-09 修订：暂停任务单向续基

WP-4-01A 在实现候选完成后暂停，期间插入并验收 WP-H-02A。恢复时，原 base 到 HEAD 的 changed-set
必然包含已验收的 Harness 依赖文件；把这些文件加入 Memory allowlist 会错误扩大业务任务范围，而创建
新的批准账本又违背本 ADR 的轻量方向。项目负责人因此明确批准直接移动该固定 base。

本修订只替代上文“`base_ref` 始终等于 task 第一次提交值”的绝对限制，其他方案 C 决策不变：

- 默认 base 仍固定为 task 第一次提交值；只有暂停任务因已验收的插入依赖而恢复，且项目负责人明确
  批准时，才可前移。
- 新 base 必须是初始 base 的后代和当前 HEAD 的祖先；后退、无关历史或跨分支移动硬失败。
- base 变化必须进入 task 的普通 Git 修订；未提交或 staged 时返回 `owner_review_required` 并跳过
  profile，提交后在 `contract.revision_fields` 中公开 `base_ref`。
- 新 base 只排除其提交之前已经验收的依赖状态；负责人批准点之后的记录和当前任务修改继续进入
  changed-set，仍受 allowlist、全局保护和测试删除门约束。

## 后果

新业务 WP 只需一份不超过 30 行的 task 配置，正常情况下不修改 Harness Python。测试执行、用户数据
保护、依赖变化、测试删除、报告和人工待办语义继续保留，activation 历史查询与验收散文映射被删除。

代价是已提交的 allowlist/profile 修订不再拥有 Harness 内部批准账本，审查依赖普通 Git diff 与负责人
判断；这是有意把版本历史职责还给 Git。Harness 仍是质量门和安全告警，不是操作系统级权限边界。

若 v2 需要回退，整体 revert WP-H-02 的重构和调用文档；历史 v1 task/activation 保留在 HEAD，可以恢复
旧 loader。回退不得触碰产品代码、用户数据或已验收的 WP-4-01 Memory 实现。
