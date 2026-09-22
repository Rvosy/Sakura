---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# 文档职责与维护规范

Sakura 的长期工程知识以 Spec 和 ADR 为核心，Tests/Product Harness 提供可执行验证，Git 保存修改历史。
Plan 与 Record 只在确有实施或历史价值时使用，不是每次开发的强制产物。

开发协作规则集中在根目录 [AGENTS.md](../../AGENTS.md)。本文维护文档职责，各产品 Spec 不再复制智能体审批、
模型选择或文件修改范围。

## 文档类型

| 类型 | 目录 | 回答的问题 |
|---|---|---|
| `userdoc` | `docs/userdocs/` | 用户怎样安装、配置或使用？ |
| `devdoc` | `docs/devdocs/` | 开发者怎样理解、扩展或验证？ |
| `spec` | `docs/specs/` | 当前必须保持哪些行为、不变量和兼容契约？ |
| `adr` | `docs/adr/` | 为什么选择这项重要架构，以及放弃了什么？ |
| `plan` | `docs/plans/` | 某项大型、分阶段工作怎样实施和回退？ |
| `record` | `docs/records/` | 哪次发布、验收或事故实际发生了什么？ |
| 历史文档 | `docs/archive/` | 哪些资料已完成、废弃或被替代？ |

普通 Bug 通常只需要代码修复和 regression test。只有 Bug 暴露新的长期产品不变量时才更新 Spec；只有
产生新的长期架构取舍时才新增 ADR。Plan 适用于多个阶段或迁移/回退复杂的工作，Record 适用于发布、
事故、人工验收或以后确实需要追溯的事实。

## Spec 与 ADR

Spec 以当前产品真相为中心，优先包含 Purpose、Invariants、Compatibility、Verification 和 Related
Decisions。不要写修改文件、开发步骤、Agent 流程或 Work Package 权限。

产品行为变化时，随实现更新对应契约。尚未交付的设计放在 Plan 或 draft Spec 中，注明实现状态。
文档和代码冲突时先核实调用链与测试，不凭日期或标题机械取舍。

历史 WP 中的文件白名单、激活前置条件、固定提交顺序和任务级 required profiles 已由
[ADR-0021](../adr/0021-product-harness-outcome-verification.md) 废止。维护现行 Spec 时删除这些流程限制；
混在其中的协议、数据保护和资源回收要求保留在对应产品契约中。带日期的验收记录可以保留事实，但需明确历史身份，
不把当时的审批和停止条件用于当前开发。自动测试结果与人工验收分别记录，不以工作包状态推断结果。

ADR 记录 Context、Decision、Alternatives、Reasons 和 Consequences。已接受的 ADR 被新方向替代时，
新增 ADR 并明确 supersedes 关系；旧 ADR 标为 `superseded` 后移入 archive，不直接改写历史理由。

## 内容与来源

正式文档用项目作者的口吻说明有效用法、规则和决定。助手与作者的对话、授权和工作汇报留在协作记录中；
UI、提示、日志说明、配置注释和生成模板也遵循这一要求。界面文本的具体判断见[界面文案规范](UI_COPY_GUIDELINES.md)。

README 负责项目介绍和快速上手，详细步骤进入对应指南。每项事实保留一个详细来源，其他入口用简短介绍和
具体链接引导；官网也引用仓库指南，不复制整份字段表、默认值或安装流程。代码拥有的规则引用文件与符号。

长期文档说明如何查询版本、产物和状态。带日期的 Record 保存当次版本、环境、实测结果、失败与结论；
尝试和未完成方案留在 Plan，已结束的过程归档。移出历史段落时保留原始证据并更新引用。

用具体的测量条件、适用范围和操作后果代替反复的免责声明。保留许可证原文与第三方归属。
改动生成文本时检查模板、生成器、示例和分发副本，副本与对应版本保持一致；实现、验证和发布分别依据其记录。

## 元数据与索引

`docs/**/*.md` 必须从 front matter 开始，并包含：

```yaml
---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---
```

必填字段为 `kind`、`status`、`audience`、`source_of_truth`、`updated`。`status_source` 是可选引用，只在
文档状态确由另一个当前文档维护时使用；Product Harness 和文档检查器不据此决定代码能否修改或测试是否
通过。

允许状态：

- `userdoc`、`devdoc`：`current`、`deprecated`；
- `spec`：`draft`、`normative`、`superseded`、`archived`；
- `adr`：`proposed`、`accepted`、`superseded`、`deprecated`、`archived`；
- `plan`：`planned`、`active`、`stabilizing`、`accepted`、`cancelled`、`archived`；
- `record`：`recorded`、`archived`。

活跃文档必须从所属目录的 `README.md` 索引。被替代的文档移入 `docs/archive/`，并更新索引和所有本地
Markdown 链接；不要保留旧路径兼容页或复制第二份“当前”内容。

## 维护判断

- 产品行为、公共接口、配置或数据兼容变化：更新相关 Spec。
- 新的重要架构方向或难以逆转的取舍：新增 ADR。
- 安装、配置和用户可见故障排查变化：更新 userdoc；确属发布变化时再更新 CHANGELOG。
- 开发入口、扩展点或测试方法变化：更新相关 devdoc。
- 大型分阶段迁移：按需要建立 Plan；发布、事故或重要人工证据发生后再写 Record。

不要为普通实现过程创建 Development Trace、Agent Action Log、Task Contract 或审批流水账。

## 检查

文档变更运行 `docs` profile，它已执行 `tools/check_docs.py`：

```text
python -m harness run docs
```

检查器验证目录职责、必需元数据、索引和本地链接，不校验开发范围、Work Package 状态或 Agent 行为。
使用当前平台的 bundled Python。排查检查器时也可直接运行 `python tools/check_docs.py`，无需重复执行两个入口。
同时修改代码时，按实际风险选择受影响能力的测试或 Harness profile。
