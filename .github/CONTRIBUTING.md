# 为 Sakura 贡献代码

[English](CONTRIBUTING.en.md)

小修复可以直接提交 Pull Request。改动公开接口、配置格式、Plugin API 或主要交互前，先开 Issue 说明问题和方案。

## 仓库结构

Sakura 的产品运行链是 Tauri Shell、Python Core Host 和逐插件 Plugin API v4 进程。

| 目录 | 内容 |
|---|---|
| `desktop/` | Tauri/Rust 后端和 WebView 前端 |
| `app/` | Core Host、Agent、配置、存储、MCP、Plugin Runtime 和语音领域 |
| `plugins/` | 随项目提供的 Plugin API v4 插件 |
| `tools/studio-tauri/` | Tauri 角色工作室 |
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
.\install.bat
.\runtime\python.exe -m pip install -r tools\requirements-dev.txt
cargo build --manifest-path desktop\src-tauri\Cargo.toml
.\start.bat
```

macOS / Linux：

```bash
bash scripts/install.sh
./runtime/bin/python3 -m pip install -r tools/requirements-dev.txt
bash scripts/start.sh
```

`main.py` 只定位已经构建的 Tauri Shell，不负责构建。

## 分支和提交

从最新 `dev` 建立分支，不直接在 `dev` 上提交：

```bash
git fetch upstream
git switch -c feat/short-name upstream/dev
```

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
- Bug 修复要有能复现问题的回归测试。
- 不要吞掉异常、放宽断言或为假设场景增加自动重试。
- 保留工作树中已有修改，不使用破坏性 Git 命令清理用户工作。

插件作者应使用 [Plugin API v4 开发指南](../docs/devdocs/SAKURA_PLUGIN_SDK.md)。桌面窗口、MCP 和日志的开发入口位于 [开发者文档](../docs/devdocs/README.md)。

## 测试

下面使用 macOS/Linux 路径。Windows 把 `./runtime/bin/python3` 替换为 `.\runtime\python.exe`。

先列出 Harness profile：

```bash
./runtime/bin/python3 -m harness list
```

从受影响能力的 focused tests 开始。例如：

```bash
./runtime/bin/python3 -m harness run smoke
./runtime/bin/python3 -m harness run core-host
./runtime/bin/python3 -m harness run runtime-v2-shell
./runtime/bin/python3 -m pytest -q tests/unit/test_plugin_runtime_v4.py tests/unit/test_core_host_plugins.py
```

Python 改动按需要运行：

```bash
./runtime/bin/python3 -m pytest tests/unit
./runtime/bin/python3 -m pytest tests/integration
./runtime/bin/python3 -m harness run python-full
```

桌面端改动运行：

```bash
npm test --prefix desktop/frontend
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

角色工作室使用自己的 Cargo manifest。文档改动至少运行：

```bash
./runtime/bin/python3 tools/check_docs.py
./runtime/bin/python3 -m harness run docs
```

本地不必重复 CI 的完整平台矩阵。无法执行真实桌面、设备或平台验证时，在 PR 中说明未验证内容和风险。

## 提交 Pull Request

PR 合并目标是 `dev`，标题和说明使用中文。说明中写清：

- 问题和修改结果；
- 运行过的测试及结果；
- 没有覆盖的风险；
- UI 改动的截图或短录屏。

提交前检查差异，确认没有 API Key、token、聊天记录、日志、模型文件或其他本地数据。CI 结果和本地不一致时，附上失败输出和相关环境信息。

## 许可证

提交代码即表示你同意按项目的 [MIT License](../LICENSE) 发布，并确认自己有权提交相关代码和资源。
