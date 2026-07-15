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
