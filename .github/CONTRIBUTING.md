# 为 Sakura 贡献代码

[English](CONTRIBUTING.en.md)

小修复可以直接提交 Pull Request。公开接口、配置格式、Plugin API 或主要交互的调整应说明问题、结果和兼容影响。
需求尚不明确时可先讨论；已有明确需求或维护者授权时可以直接实施，无需另开 Issue 才能开始。

使用编码智能体时，协作规则见根目录 [AGENTS.md](../AGENTS.md)。旧 Work Package 的文件范围、激活状态和
提交步骤是历史资料，不限制当前任务的调查与修改。

## 仓库结构

Sakura 的产品运行链是 Tauri Shell、Python Core Host 和逐插件 Plugin API v4 进程。

| 目录 | 内容 |
|---|---|
| `desktop/` | Tauri/Rust 后端和 WebView 前端，包含角色工作室 |
| `app/` | Core Host、Agent、配置、存储、MCP、Plugin Runtime 和语音领域 |
| `plugins/` | 随项目提供的 Plugin API v4 插件 |
| `harness/` | 按产品能力组织的验证入口 |
| `tests/` | Python 单元、集成和测试夹具 |
| `docs/` | 用户文档、开发指南和维护者工程资料 |

`third_party/` 与 `tools/mcp/` 包含第三方或外部工具代码。只有问题确实属于这些目录时才修改。

不要提交 `runtime/`、`data/`、角色资源、日志、模型、测试缓存或 Tauri 构建产物。测试需要数据时，使用临时 app root。

## 开发环境

Fork 仓库，克隆自己的 Fork，再添加上游：

```bash
git clone https://github.com/<你的 GitHub 用户名>/Sakura.git
cd Sakura
git remote add upstream https://github.com/Rvosy/Sakura.git
git fetch upstream
```

项目使用根目录下的 bundled Python Runtime，不使用系统 Python 替代。源码检出不包含 `runtime/`，请从 [Releases](https://github.com/Rvosy/sakura/releases) 获取对应平台的 Runtime 或完整包。

Windows：

```powershell
.\scripts\install.bat
.\runtime\python.exe -m pip install -r tools\requirements-dev.txt
.\scripts\start.bat
```

macOS / Linux：

```bash
bash scripts/install.sh
./runtime/bin/python3 -m pip install -r tools/requirements-dev.txt
bash scripts/start.sh
```

Windows 的 `scripts\start.bat` 与 macOS/Linux 的 `scripts/start.sh` 都会增量编译并启动 debug Shell。

## 分支和提交

准备贡献分支时，通常从最新 `dev` 建立分支：

```bash
git fetch upstream
git switch -c feat/short-name upstream/dev
```

已有任务分支或 worktree 时继续使用当前环境；上述命令是新贡献的示例，不要求智能体切换或清理用户工作区。

分支使用 `feat/`、`fix/` 或 `refactor/` 等简短英文前缀。Commit 使用常规类型和简洁说明：

```text
feat: 添加手机端图片发送
fix: 修复退出时的 TTS 残留进程
docs: 更新插件开发说明
test: 增加配置保存回归测试
```

一次 Commit 处理一件事。不要夹带无关格式化、重命名或清理。

## 修改代码

- 先读真实调用链和相关测试。长期行为受 Spec 约束时，再查看对应文档。
- 新接口写清输入、返回值和失败方式。
- 可自动复现的 Bug 应有能让原缺陷失败的回归测试；已有覆盖可直接复用，低影响文案或样式调整不需要照抄实现的测试。
- 不要吞掉异常、放宽断言或为假设场景增加自动重试。
- 保留工作树中已有修改，不使用破坏性 Git 命令清理用户工作。

插件作者应使用 [Plugin API v4 开发指南](../docs/devdocs/SAKURA_PLUGIN_SDK.md)。桌面窗口、MCP 和日志的开发入口位于 [开发者文档](../docs/devdocs/README.md)。

## 测试

下面使用 macOS/Linux 路径。Windows 把 `./runtime/bin/python3` 替换为 `.\runtime\python.exe`。

需要选择 Harness profile 时，先查看当前清单：

```bash
./runtime/bin/python3 -m harness list
```

从受影响能力的窄测试或 profile 开始。以下是不同任务的入口，按需选择：

```bash
./runtime/bin/python3 -m harness run core-host
./runtime/bin/python3 -m harness run runtime-v2-shell
./runtime/bin/python3 -m pytest -q tests/unit/test_plugin_runtime_v4.py tests/unit/test_core_host_plugins.py
```

前端或 Rust 改动可以使用各自入口，并按受影响模块选择测试：

```bash
npm test --prefix desktop/frontend
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml <相关测试过滤条件>
```

角色工作室属于主 Tauri 应用，使用同一 Cargo manifest。文档改动运行：

```bash
./runtime/bin/python3 -m harness run docs
```

`docs` 已包含文档检查器，其他 profile 也可能共享 case；通过的检查无需重复执行。仅在新改动、失败或未覆盖
风险出现时扩大验证，跨模块变化可选择 `python-full` 等更广的 profile。本地不必重复 CI 的完整平台矩阵。
无法执行真实桌面、设备或平台验证时，在 PR 中说明未验证内容和风险。

## 提交 Pull Request

PR 合并目标是 `dev`，标题和说明使用中文。说明中写清：

- 问题和修改结果；
- 运行过的测试及结果；
- 没有覆盖的风险；
- UI 改动的截图或短录屏。

提交前检查差异，确认没有 API Key、token、聊天记录、日志、模型文件或其他本地数据。CI 结果和本地不一致时，附上失败输出和相关环境信息。

## 许可证

提交代码即表示你同意按项目的 [MIT License](../LICENSE) 发布，并确认自己有权提交相关代码和资源。
