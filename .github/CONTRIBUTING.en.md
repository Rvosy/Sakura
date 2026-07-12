# Contributing to Sakura

[中文](CONTRIBUTING.md)

Thanks for taking the time to improve Sakura. Small fixes can go straight to a pull request. For changes to public interfaces, configuration formats, plugin compatibility, or major interactions, open an issue first so the approach can be discussed before implementation.

## Repository layout

Sakura is a Python 3.12 and PySide6 desktop application. Most application code lives in `app/`.

| Path | Contents |
|---|---|
| `app/` | Agent runtime, configuration, storage, plugin system, TTS, and desktop UI |
| `plugins/` | Plugins shipped with Sakura |
| `tests/unit/` | Unit tests |
| `tests/integration/` | Cross-module integration tests |
| `tests/ui/` | PySide6 UI tests |
| `tools/settings-tauri/` | Tauri settings application |
| `tools/studio-tauri/` | Tauri Character Studio |

The `third_party/` and `tools/mcp/` directories contain third-party or external tool code. Leave them unchanged unless the task specifically requires it. Do not commit `runtime/`, `data/`, character assets, test caches, or Tauri build output.

## Development setup

Fork the repository, clone your fork, and add the main repository as `upstream`:

```powershell
git clone https://github.com/<your-github-name>/Sakura.git
cd Sakura
git remote add upstream https://github.com/Rvosy/Sakura.git
git fetch upstream
```

Development uses the bundled `runtime` in the repository root rather than the system Python installation. Source checkouts do not contain this directory. Download the runtime or a full package for your platform from [Releases](https://github.com/Rvosy/sakura/releases), then place `runtime/` in the project root.

Windows:

```powershell
.\install.bat
.\runtime\python.exe -m pip install -r requirements-dev.txt
```

macOS or Linux:

```bash
bash scripts/install.sh
./runtime/bin/python3 -m pip install -r requirements-dev.txt
```

Run the application on Windows with:

```powershell
.\runtime\python.exe main.py
```

On macOS or Linux, use `bash scripts/start.sh`.

## Branches and commits

Start each change from the latest `dev` branch. Do not commit directly to `main` or `dev`:

```powershell
git fetch upstream
git switch -c feat/short-name upstream/dev
```

Use a `feat/`, `fix/`, or `refactor/` prefix as appropriate. Keep branch names short and in English, for example `fix/tts-shutdown`.

Commit messages use a conventional type followed by a concise Chinese description:

```text
feat: 添加手机端图片发送
fix: 修复退出时的 TTS 残留进程
docs: 补充插件开发说明
test: 增加配置迁移回归测试
```

Accepted types include `fix`, `feat`, `style`, `docs`, `refactor`, `perf`, `test`, and `chore`. Keep unrelated formatting or refactoring out of the same commit.

## Code and tests

- Follow the surrounding code style. New Python interfaces should document their types and failure behavior.
- Bug fixes should include a regression test. New features should cover the main flow and relevant failure cases.
- Do not weaken assertions, hide exceptions, or rewrite unrelated tests just to make a suite pass.
- Preserve existing user changes. Do not use destructive cleanup commands such as `git reset --hard` or `git checkout --`.

Run the tests closest to your change first:

```powershell
.\runtime\python.exe -m pytest tests/unit
.\runtime\python.exe -m pytest tests/integration
.\runtime\python.exe -m pytest tests/ui
```

Expand coverage when changing the core runtime, tool calls, configuration loading, plugins, TTS, UI, or storage. A full test run is required before opening a pull request:

```powershell
.\runtime\python.exe -m pytest
```

Changes to either Tauri application also require Rust formatting and tests. Run the commands for the application you changed:

```powershell
cargo fmt --manifest-path tools/settings-tauri/src-tauri/Cargo.toml -- --check
cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml

cargo fmt --manifest-path tools/studio-tauri/src-tauri/Cargo.toml -- --check
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

For UI changes, manually check startup, settings persistence, window shutdown, and high-DPI rendering as relevant. If a test cannot be run, explain why and identify the unverified risk in the pull request.

## Pull requests

Open pull requests against `dev`. PR titles and descriptions should be in Chinese. A title may follow the commit format, for example `fix: 修复角色切换后的语音配置`.

The description should cover:

- what changed and what problem it solves;
- tests that were run and their results;
- compatibility changes or remaining risks;
- screenshots or a short recording for UI changes.

Review the final diff for API keys, tokens, chat history, logs, model files, and other local data. CI must pass. If CI behaves differently from your local environment, include the failure output and relevant environment details in the PR.

## License

By contributing, you agree that your changes will be published under the project's [MIT License](../LICENSE), and that you have the right to submit the code and assets involved.
