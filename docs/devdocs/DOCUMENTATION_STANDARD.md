---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 文档规范与履行流程

## 1. 先选文档类型

| 类型 | 只回答一个问题 | 必须包含 |
|---|---|---|
| `userdoc` | 用户怎样完成一件事？ | 前置条件、步骤、结果、故障排查 |
| `devdoc` | 开发者怎样使用或理解系统？ | 适用读者、入口、代码/命令示例 |
| `spec` | 系统必须满足什么？ | 范围、非目标、契约、验收条件 |
| `adr` | 为什么选择这个架构？ | 背景、候选方案、决策、后果 |
| `plan` | 怎样实施和回退？ | 范围、步骤、退出条件、测试、回退 |
| `record` | 实际发生了什么？ | 日期、环境、结果、证据 |

不要把实现进度写进 spec，不要把架构理由写进 userdoc，不要把历史验收结果伪装成 plan。

## 2. 元数据契约

每份 `docs/` 下的 Markdown 都必须从 front matter 开始：

```yaml
---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---
```

必填字段为 `kind`、`status`、`audience`、`source_of_truth` 和 `updated`。
`status_source` 只在文档状态由另一个真相源维护时使用。`source_of_truth: self` 表示正文自身是该
文档职责范围内的权威内容。

类型与状态约束如下：

- `userdoc`、`devdoc`：`current` 或 `deprecated`。
- `spec`：`draft`、`normative`、`superseded` 或 `archived`。
- `adr`：`proposed`、`accepted`、`superseded`、`deprecated` 或 `archived`。
- `plan`：`planned`、`active`、`stabilizing`、`accepted`、`cancelled` 或 `archived`。
- `record`：`recorded` 或 `archived`。

## 3. 变更流程

1. 新增架构方向或改变既有架构选择：先新增/更新 ADR。
2. 改变行为、接口、数据格式或平台契约：更新对应 spec，并补充验收条件。
3. 需要多个提交、阶段或独立回退：建立 plan/Work Package。
4. 实现和验证完成：把测试、CI、人工验收结果写入 record 或对应 Work Package 记录。
5. 用户可见行为改变：同步 userdoc 和根目录 `CHANGELOG.md`。
6. 文档被替代：新文档标明 `supersedes`，旧文档改为 `superseded` 并移入 `archive/`。

Runtime v2 例外规则：Work Package 状态只维护在
`docs/plans/runtime-v2/work-packages.md`；spec、ADR 和 record 必须引用它，不得复制另一份状态表。

## 4. 提交前检查

```text
python tools/check_docs.py
python -m harness run docs
python -m pytest tests/unit/test_docs_structure.py
```

检查器会验证目录职责、元数据、source/status 真相源、本地链接、索引覆盖、废弃路径和 Runtime v2
当前 Work Package 字段。文档变更提交时应在提交正文中说明受影响的文档类型和验证命令。
