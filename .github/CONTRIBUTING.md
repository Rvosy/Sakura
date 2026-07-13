# 为 Sakura 贡献代码

[English](CONTRIBUTING.en.md)

感谢你愿意花时间改进 Sakura。小修复可以直接提交 Pull Request；如果改动会影响公开接口、配置格式、插件兼容性或整体交互，建议先开 Issue 说清楚问题和方案，省得写完才发现方向不同。

## 开始之前

Sakura 是 Python 3.12 / PySide6 桌面应用，主要源码在 `app/`。仓库还包含本地插件和两个 Tauri 工具：

| 目录 | 内容 |
|---|---|
| `app/` | Agent、配置、存储、插件系统、TTS 和桌面 UI |
| `plugins/` | 随项目提供的插件 |
| `tests/unit/` | 单元测试 |
| `tests/integration/` | 跨模块集成测试 |
| `tests/ui/` | PySide6 界面测试 |
| `tools/settings-tauri/` | Tauri 设置页 |
| `tools/studio-tauri/` | Tauri 角色工作室 |

`third_party/` 和 `tools/mcp/` 含有第三方或外部工具代码，除非改动确实属于当前问题，否则不要顺手调整。也不要提交 `runtime/`、`data/`、角色资源、测试缓存或 Tauri 构建产物。

## 准备开发环境

先 Fork 仓库，再克隆自己的 Fork，并把上游仓库添加为 `upstream`：

```powershell
git clone https://github.com/<你的 GitHub 用户名>/Sakura.git
cd Sakura
git remote add upstream https://github.com/Rvosy/Sakura.git
git fetch upstream
```

项目使用仓库根目录下的 `runtime`，不要改用系统 Python。源码仓库没有附带这个目录，请从 [Releases](https://github.com/Rvosy/sakura/releases) 下载对应平台的 runtime 或完整包，并把 `runtime/` 放到项目根目录。

Windows：

```powershell
.\install.bat
.\runtime\python.exe -m pip install -r requirements-dev.txt
```

macOS / Linux：

```bash
bash scripts/install.sh
./runtime/bin/python3 -m pip install -r requirements-dev.txt
```

安装完成后，可以运行：

```powershell
.\runtime\python.exe main.py
```

macOS / Linux 使用 `bash scripts/start.sh`。

## 分支和提交

每次开发都从最新的 `dev` 开始，不要直接在 `main` 或 `dev` 上提交：

```powershell
git fetch upstream
git switch -c feat/简短名称 upstream/dev
```

根据改动选择 `feat/`、`fix/` 或 `refactor/` 前缀。分支名用简短英文，例如 `fix/tts-shutdown`。

Commit 使用常规类型和简洁中文：

```text
feat: 添加手机端图片发送
fix: 修复退出时的 TTS 残留进程
docs: 补充插件开发说明
test: 增加配置迁移回归测试
```

可用类型包括 `fix`、`feat`、`style`、`docs`、`refactor`、`perf`、`test` 和 `chore`。一个 Commit 尽量只处理一件事，不要夹带无关格式化或重构。

## 编码和测试

- 保持现有代码风格，新增 Python 接口应写清类型和异常行为。
- 修复 Bug 时补充能复现问题的回归测试；新增功能至少覆盖主要流程和失败路径。
- 不要为了让测试通过而放宽断言、吞掉异常或改写无关测试。
- 保留用户已有改动。不要使用 `git reset --hard`、`git checkout --` 等命令清理工作树。

先运行与改动最相关的测试：

```powershell
.\runtime\python.exe -m pytest tests/unit
.\runtime\python.exe -m pytest tests/integration
.\runtime\python.exe -m pytest tests/ui
```

影响核心运行链路、工具调用、配置加载、插件、TTS、UI 或存储时，需要扩大测试范围。提交 PR 前必须运行完整测试：

```powershell
.\runtime\python.exe -m pytest
```

如果修改了 Tauri 设置页或角色工作室，还要检查对应 Rust 工程。以下命令中的目录按实际改动选择：

```powershell
cargo fmt --manifest-path tools/settings-tauri/src-tauri/Cargo.toml -- --check
cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml

cargo fmt --manifest-path tools/studio-tauri/src-tauri/Cargo.toml -- --check
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

界面改动除了自动测试，还应手动检查启动、保存设置、窗口关闭和高 DPI 显示。无法运行某项测试时，请在 PR 中写明原因和未验证的风险。

## 提交 Pull Request

PR 合并目标是 `dev`，标题和说明使用中文。标题建议沿用 Commit 格式，例如 `fix: 修复角色切换后的语音配置`。

PR 描述至少写清：

- 改了什么，原来的问题是什么；
- 运行过哪些测试，结果如何；
- 是否有兼容性变化或仍未覆盖的风险；
- 界面改动附截图或短录屏。

提交前再检查一次差异，确认没有 API Key、token、聊天记录、日志、模型文件或其他本地数据。CI 必须通过；如果 CI 和本地结果不同，把失败日志和本地环境写进 PR，方便继续排查。

## 许可证

提交代码即表示你同意按项目的 [MIT License](../LICENSE) 发布这些改动，并确认自己有权提交相关代码和资源。
