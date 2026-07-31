# AGENTS.md

本文件约束在当前 Sakura 仓库内工作的 AI Agent 行为。

## 项目概览

- 这是 Sakura Desktop Pet，一个基于 Python/PySide6 的桌面 Agent / 桌宠项目。
- 应用入口为 `main.py`。
- 主要源码位于 `app/`。
- 插件相关代码位于 `plugins/`（插件实现）和 `app/plugins/`（插件系统）。
- 配置与运行时数据主要位于 `data/`、`runtime/`。
- 角色包位于 `characters/`。
- 测试位于 `tests/`。
- `third_party/` 和 `tools/mcp/` 中包含第三方或外部工具代码，修改前需确认确实属于当前任务范围。

## 常用命令

项目使用 `/runtime/python.exe` 的运行环境，以下命令均在该环境下执行：

```powershell
python main.py
```

```powershell
python -m pytest
```

```powershell
python -m pytest tests/unit
```

## Harness

- 仓库级验证入口位于 `harness/`，用于把已有检查组织为稳定 profile，并生成机器可读 JSON 报告。
- 修改前可运行 `runtime\python.exe -m harness list` 查看可用 profile。
- 最小回归运行 `runtime\python.exe -m harness run smoke`。
- 完整 Python 单元测试运行 `runtime\python.exe -m harness run unit`。
- 默认报告写入已忽略的 `temp/harness/`；新增检查时，测试断言仍放在 `tests/`，只在 `harness/suites.json` 中注册执行入口。

### Agent Development Harness 强制流程

`current`、`preflight`、`check`、`verify` 已可用。WP-H-01 的一次性 bootstrap 例外已经关闭；下列规则
从当前 Work Package 起生效：

- 所有非微小开发任务必须绑定 Work Package ID 和 `harness/tasks/<WP-ID>.json`。
- 修改产品代码前运行 `runtime\python.exe -m harness preflight <WP-ID>`。
- 开发中运行 `runtime\python.exe -m harness check <WP-ID>`。
- 声称完成前运行 `runtime\python.exe -m harness verify <WP-ID>`；非零退出时不得声称完成。
- 不得修改任务契约、Spec、ADR、测试或 Harness 来弱化当前门禁；契约变化必须独立审查并重新预检。
- 无法执行验证时明确报告未验证命令、环境限制和风险。
- Agent 不得自动填写或伪造人工验收，不得擅自将 Work Package 标记为 `accepted`。

标准命令：

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness preflight WP-3-04
runtime\python.exe -m harness check WP-3-04
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify WP-3-04
```

## 文档治理与开发任务预检

文档按职责组织在 `docs/` 下。修改或新增文档前，必须先阅读
`docs/devdocs/DOCUMENTATION_STANDARD.md`，并在工作计划或首次进展说明中完成一次文档影响判断。

### 文档类型与目录

不得按个人习惯新增平行目录，也不得把不同职责的内容混写。使用以下目录：

| 类型 | 目录 | 只回答什么问题 |
| --- | --- | --- |
| `userdoc` | `docs/userdocs/` | 用户如何安装、配置和使用 |
| `devdoc` | `docs/devdocs/` | 开发者、插件作者如何开发、扩展和测试 |
| `spec` | `docs/specs/` | 系统必须具备什么行为、接口和数据契约 |
| `adr` | `docs/adr/` | 为什么选择某个架构或技术方案 |
| `plan` | `docs/plans/` | 如何分阶段实施、验证和回退 |
| `record` | `docs/records/` | 实际发生了什么，以及如何验收、审计或发布 |
| 历史文档 | `docs/archive/` | 已被替代、完成或不再维护的内容 |

所有 `docs/**/*.md` 都必须包含完整元数据：`kind`、`status`、`audience`、
`source_of_truth`、`updated`。文档的 `kind` 必须与目录职责一致；本地 Markdown 链接必须有效；
活跃文档必须能从对应目录的 `README.md` 找到。被取代的文档应在同一变更中移入 `docs/archive/`，
不得保留旧路径兼容页或复制一份“看起来最新”的内容。

### 新功能、修复和角色变更的文档预检

在开始实现前，按下面规则判断需要哪些文档；“需要”时必须在同一变更中创建或更新，不能等实现完成后再补一份与代码脱节的说明。

- **用户可见行为、公共接口、配置项、数据格式或插件契约变化**：创建或更新 `spec`。
- **跨模块、跨平台、影响运行时边界，或难以逆转的架构/技术选择**：创建或更新 `adr`。
- **包含多个提交、阶段、迁移步骤或明确回退要求的工作**：创建或更新 `plan`。
- **测试、验收、审计、发布或事故已经发生**：在 `records/` 写入对应 `record`，记录日期、环境、结果和证据链接；历史事实原则上只追加，不改写。
- **安装、配置、使用方式或用户可见故障排查发生变化**：同步 `userdoc` 和根目录 `CHANGELOG.md`。
- **开发流程、扩展点、测试方法或插件作者接口发生变化**：同步 `devdoc`。
- **新增角色包本身**：通常只需维护角色包内的用户/开发说明和清单；只有当角色引入新的公共能力、配置/数据契约或架构决策时，才需要 `spec` 或 `adr`。

`spec`、`adr`、`plan`、`record` 不是固定成套文件：只创建实际需要的类型。若一个需求同时改变“必须是什么”和“为什么这样选”，就分别维护 `spec` 与 `adr`；不要把决策理由塞进 spec，也不要用 ADR 代替行为契约。不要因为新增一个小功能或角色就机械地产生 ADR。

固定履行顺序如下：

1. 架构方向变化：先写或更新 ADR。
2. 行为、接口或数据契约变化：写或更新 spec。
3. 需要分阶段实施：创建或更新 plan。
4. 实现完成并验证：写入 records 中的测试或验收证据。
5. 用户可见行为变化：同步 userdocs 和 `CHANGELOG.md`。
6. 原文档被取代：在同一变更中标记其生命周期并移入 `archive/`。

### 真相源与状态约束

- Runtime v2 的工作包状态唯一维护在 `docs/plans/runtime-v2/work-packages.md`。
- Runtime v2 的 spec、ADR 和验收记录只能通过 `source_of_truth` 或 `status_source` 引用该计划，
  不得复制 `active`、`stabilizing` 等状态，不能形成第二个状态真相源。
- ADR 记录决策及其取舍；后续改变决策时新增或更新 ADR，并明确替代关系，不直接改写历史理由。
- plan 记录实施过程，不承担长期产品规范；record 记录已经发生的事实，不倒推修改成计划。

### 文档变更的最低验证

涉及 `docs/` 的变更至少运行：

```powershell
runtime\python.exe -m harness run docs
```

若同时修改 Python、Harness 或测试，再运行与改动相关的 pytest；涉及核心运行链路时扩大回归范围。
若文档检查失败，不能以“只是文档”为理由忽略。提交前还应确认旧路径引用、失效本地链接和重复真相源均已清理。

## 验证要求

- 涉及 Python 代码修改时，优先运行与改动范围最相关的 pytest。
- 若改动影响核心运行链路、工具调用、配置加载、插件、TTS、UI 或存储，需扩大测试范围。
- 若无法运行测试，应在最终回复中说明原因和未验证风险。

## Git 与文件安全
- Commit 使用 `fix:`、`feat:`、`style:`、`docs:`、`refactor:`、`perf:`、`test:`、`chore:` 等常规类型，并使用中文。
- Commit 标题保持简洁明确；正文应按改动风险详细记录背景、主要变更、明确非目标、验证结果、已知风险和回退方式，方便个人开发时回溯与 Review。
- 开发新功能或修复时，通常从最新 `dev` 新建 `feat/xxx`、`fix/xxx`、`refactor/xxx` 格式的分支，并直接在该功能分支提交。
- 本项目采用个人开发流程，不要求创建 PR。当前 Runtime v2 工作直接提交到 `refactor/tauri-runtime-v2`，不得直接提交到 `dev`。
- 合并到 `dev` 前必须运行完整测试，并由项目负责人完成最终审查；具体合并时间和方式由项目负责人决定。
- 不要还原用户已有改动，除非用户明确要求。
- 不使用 `git reset --hard`、`git checkout --` 等破坏性命令，除非用户明确要求。
- 可读写范围内只修改完成任务必需的文件。
- 对二进制、角色资源、运行时缓存、大型第三方目录进行修改前要格外谨慎。
