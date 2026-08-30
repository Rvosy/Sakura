---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
status_source: ../../../plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# WP-H-01：Agent Development Harness Foundation

> 当前状态只以 [`work-packages.md`](../../../plans/runtime-v2/work-packages.md) 为准。
> 架构依据：[ADR-0008](../../adr/0008-agent-development-harness.md)
> 本规范已由 [`WP-H-02`](WP-H-02-lean-agent-development-harness.md) 替代，
> 仅保留 v1 历史行为。

## 范围与非目标

本规范把现有 profile runner 扩展为任务级门禁：解析当前 Work Package、严格加载任务契约、检查 Git
范围和依赖、运行 required profiles、汇总自动/人工验收并原子写报告。`list`、`run`、现有 profile 与
profile 报告结构必须兼容。

不得改变 Sakura 产品业务、真实 `data/**`、角色包或第三方代码；不得实现 `WP-3-04`；不得新增 Python
依赖、远程服务、数据库、通用工作流或多 Agent 调度。

## 任务契约 v1

契约位于 `harness/tasks/<WP-ID>.json`，必须符合 `harness/tasks/schema.json`。根对象和所有嵌套对象
拒绝未知字段；schema_version 只接受整数 `1`。以下字段必填：任务 ID/标题、状态真相源、文档引用、
依赖、`base_ref`、允许/禁止/受保护路径、依赖策略、required profiles、自动与人工验收、回退步骤。

加载器还必须执行 JSON Schema 无法可靠表达的语义检查：

- 所有路径使用仓库相对 `/` 分隔形式，不允许绝对路径、`..`、空模式或反斜杠；v1 只接受精确文件或
  `directory/**` 递归目录，不接受其他 `*`、`?`、`[]` glob；精确重复失败。
- allowed、forbidden、protected 之间出现相同模式或可证明的父子覆盖冲突时失败；无法静态证明的 glob
  在实际 changed set 上按“禁止/受保护优先”求值。
- 文档必须存在且位于声明职责目录；profile 必须在 `suites.json` 注册；JSON 中的 `base_ref` 必须是
  完整 40 位提交 SHA，禁止 `HEAD`、分支和 tag，并且必须解析为提交。
- 依赖必须存在于 Work Package 表且全部 accepted；自动验收和 rollback 不得为空。
- 错误按稳定 `CODE: message` 形式返回，一次列出所有可独立判断的阻断项。

## Work Package 解析

解析器只接受总表的精确四列表头 `Work Package | 主要结果 | 依赖 | 当前状态`，状态只接受
`planned/active/stabilizing/accepted`。front matter `active_work_package` 必须与表中唯一
active/stabilizing 行一致；零个或多个当前项、重复 ID、未知依赖、metadata/表格不一致均失败关闭。

`current --json` 输出 schema_version、task、status、status_source；文本模式适合人读。不得从独立 spec、
ADR 或提交消息推断当前状态。

## Git 范围与依赖检查

changed set 必须合并：`base_ref..HEAD` 的已提交变化、index、unstaged 和 untracked。重命名同时保留旧
路径与新路径用于规则判断；删除文件仍按原路径检查。输出去重、排序、POSIX 化，不依赖当前 cwd。

检查顺序为 protected/forbidden、allowed、依赖、关键测试删除、冻结治理边界。`data/**`、`characters/**`、
`third_party/**` 默认受保护，但具体 Harness 任务仍必须在契约中显式列出。Harness 自身不是永久禁止项；
修改它的任务必须显式允许。

激活或修订锚点位于 `harness/activations/<WP-ID>/<sequence>.json`。Harness 必须从 Git 历史找到锚点
首次加入的提交，拒绝未提交锚点、序号断裂、覆写既有锚点、非完整 SHA、任务不匹配，以及同一锚点
提交夹带实现文件。最新锚点冻结任务契约、引用的 Spec/ADR/Plan 和 `status_source`；当前 `base_ref`
必须与锚点逐字相等。锚点提交之后改变任一冻结治理文件时，scope 状态为 `owner_review_required`，不能
由普通实现门直接通过。合法的一次性激活或修订只能新增下一个递增锚点，并由项目负责人独立审查。

依赖文件集合至少覆盖 requirements、pyproject/uv、npm/pnpm/yarn 和 Cargo manifest/lock 以及
`desktop/rust-toolchain.toml`。`forbidden` 模式拒绝任何变化；`allowlisted` 只允许契约列出的文件，
同时报告声明与锁文件的成对变化，不根据文件名把锁文件自动判为通过或失败。

## 命令与退出码

- `current [--json]`：查询唯一当前 Work Package。
- `preflight <ID>|--active`：契约、状态、依赖、文档、profile、base ref、路径规则和开始工作树检查。
- `check <ID>|--active`：快速运行冻结、scope、protected 和 dependency 检查。
- `verify <ID>|--active [--report PATH]`：按 contract → preflight → check → profiles → acceptance 顺序执行。

退出码固定为：`0` 全部自动门通过且没有必需人工待办；`1` 验证失败；`2` 调用、清单、契约或解析错误；
`3` 没有自动失败，但存在必需人工验收或冻结治理文件变化而处于 `owner_review_required`。`verify` 前置
失败或等待治理审查时不得运行昂贵 profile。

## 报告契约

任务报告 schema_version 为 1，至少包含 command、task、status、解析后的完整 base SHA、HEAD SHA 或
WORKTREE、UTC 起止时间、duration、preflight、scope、dependencies、profiles、acceptance 和 summary。
失败也尽可能落盘；写入使用同目录临时文件、UTF-8、flush/fsync 后原子 replace。报告不得复制环境、
credential、API Key 或用户配置内容，只记录命令 argv、稳定错误和必要的脱敏输出。

## 验收

单元测试必须覆盖任务 manifest、Work Package 解析、四类 Git 状态、删除/重命名、依赖、契约放宽、
runner 兼容、原子报告、退出码、窄控制台编码、空格与非 ASCII 路径。临时 Git 仓库必须证明允许路径
通过，范围外/禁止/受保护失败，依赖未 accepted 的 preflight 失败，契约放宽失败，profile 失败传播，
全自动门能生成有效 JSON。

CI 只在 `main/dev` push 和面向 `main/dev` 的 PR 上运行；PR 必须显式检出真实 head。Harness job 只运行
`harness-v1` 自测以及 `preflight --active`、`check --active`，不得写死具体 WP 编号，也不得重复 docs、smoke
或完整 unit。现有三平台准备、Xvfb/WebKit、Qt cleanup 和 platform matrix 保留。Windows 文档索引检查
必须使用统一 POSIX 绝对路径比较，避免 `/` 与 `\\` 导致本地 verify 误报。

## 回退

逆序回退 CI 入口、任务命令模块、测试、契约和文档；保留原有 `runner.py`、`suites.json` profile 与
报告能力。任何回退都不得删除现有测试、真实数据、角色资源或平台验收设施。
