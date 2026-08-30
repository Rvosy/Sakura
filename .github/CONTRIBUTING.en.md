# Contributing to Sakura

[中文](CONTRIBUTING.md)

Small fixes can go directly to a pull request. Open an issue before changing public interfaces, configuration formats, Plugin API behavior, or major interactions.

## Repository layout

Sakura runs as a Tauri Shell, a Python Core Host, and one Plugin API v4 process per active plugin.

| Path | Contents |
|---|---|
| `desktop/` | Tauri/Rust backend and WebView frontend |
| `app/` | Core Host, agent runtime, configuration, storage, MCP, Plugin Runtime, and voice domain |
| `plugins/` | Plugin API v4 plugins shipped with Sakura |
| `tools/studio-tauri/` | Tauri Character Studio |
| `harness/` | Product-capability validation entry point |
| `tests/` | Python unit tests, integration tests, and fixtures |
| `docs/` | User guides, developer documentation, and maintainer records |

The `third_party/` and `tools/mcp/` directories contain third-party or external tool code. Change them only when the problem belongs there.

Do not commit `runtime/`, `data/`, character assets, logs, models, test caches, or Tauri build output. Tests that need application data must use a temporary app root.

## Development environment

Fork the repository, clone your fork, and add the upstream remote:

```bash
git clone https://github.com/<your-github-name>/Sakura.git
cd Sakura
git remote add upstream https://github.com/Rvosy/Sakura.git
git fetch upstream
```

Development uses the bundled Python Runtime in the repository root. Do not replace it with the system Python installation. Source checkouts do not include `runtime/`; obtain the matching Runtime or full package from [Releases](https://github.com/Rvosy/sakura/releases).

Windows:

```powershell
.\scripts\install.bat
.\runtime\python.exe -m pip install -r tools\requirements-dev.txt
.\scripts\start.bat
```

macOS or Linux:

```bash
bash scripts/install.sh
./runtime/bin/python3 -m pip install -r tools/requirements-dev.txt
bash scripts/start.sh
```

Both `scripts\start.bat` on Windows and `scripts/start.sh` on macOS/Linux incrementally build and launch the debug Shell.

## Branches and commits

Create each branch from the latest `dev`. Do not commit directly to `dev`:

```bash
git fetch upstream
git switch -c feat/short-name upstream/dev
```

Use a short English prefix such as `feat/`, `fix/`, or `refactor/`. Commit messages use a conventional type and a concise description:

```text
feat: 添加手机端图片发送
fix: 修复退出时的 TTS 残留进程
docs: 更新插件开发说明
test: 增加配置保存回归测试
```

Keep each commit focused. Do not include unrelated formatting, renaming, or cleanup.

## Changing code

- Read the real call path and related tests first. Consult a Spec when it defines the long-term behavior being changed.
- Document inputs, return values, and failure behavior for new interfaces.
- Add a regression test for bug fixes.
- Do not hide exceptions, weaken assertions, or add speculative retry systems.
- Preserve existing working-tree changes and do not use destructive Git commands to clean user work.

Plugin authors should use the [Plugin API v4 guide](../docs/devdocs/SAKURA_PLUGIN_SDK.md). Entry points for window, MCP, and logging work are listed in the [developer documentation](../docs/devdocs/README.md).

## Tests

The commands below use the macOS/Linux path. On Windows, replace `./runtime/bin/python3` with `.\runtime\python.exe`.

List the available Harness profiles first:

```bash
./runtime/bin/python3 -m harness list
```

Start with focused tests for the affected capability. For example:

```bash
./runtime/bin/python3 -m harness run smoke
./runtime/bin/python3 -m harness run core-host
./runtime/bin/python3 -m harness run runtime-v2-shell
./runtime/bin/python3 -m pytest -q tests/unit/test_plugin_runtime_v4.py tests/unit/test_core_host_plugins.py
```

Run the relevant Python suites when needed:

```bash
./runtime/bin/python3 -m pytest tests/unit
./runtime/bin/python3 -m pytest tests/integration
./runtime/bin/python3 -m harness run python-full
```

For desktop changes:

```bash
npm test --prefix desktop/frontend
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

Character Studio uses its own Cargo manifest. Documentation changes require at least:

```bash
./runtime/bin/python3 tools/check_docs.py
./runtime/bin/python3 -m harness run docs
```

There is no need to duplicate the complete CI platform matrix locally. State any unverified desktop, device, or platform behavior and its risk in the pull request.

## Pull requests

Open pull requests against `dev`. Titles and descriptions should be in Chinese. Include:

- the problem and resulting behavior;
- tests that were run and their results;
- remaining unverified risks;
- screenshots or a short recording for UI changes.

Review the final diff for API keys, tokens, chat history, logs, model files, and other local data. If CI and local results differ, include the failure output and relevant environment details.

## License

By contributing, you agree that your work will be published under the project's [MIT License](../LICENSE), and that you have the right to submit the code and assets involved.
