# 开机自启动跨平台适配自查

日期：2026-06-07
分支：feature/launch-at-login-cross-platform
基线：origin/dev @ 8f2da1f

## 结论

这次开机自启动适配是必要改动：检查 `origin/dev` 后没有发现已有的登录项/开机自启动实现，只有应用内部启动初始化逻辑。当前实现已经按平台分发到 macOS、Windows、Linux，并接入设置页、配置持久化、启动时状态修复和测试。

自查中发现并修复了一个真实问题：Windows 最初会优先写入 `runtime/pythonw.exe`/`python.exe main.py`，这会绕过 `start.bat` 里的环境变量和路径检查。现在 Windows 在源码包/zip 形态下会优先使用 `cmd.exe /c start.bat`，没有 `start.bat` 时才回退到打包 runtime Python。

## 本次自启动相关改动

- 新增 `app/platforms/launch_at_login.py`：统一封装平台检测和系统登录项写入/删除。
- 新增 `app/platforms/__init__.py`：平台适配包入口。
- `app/config/settings_service.py`：新增 `StartupSettings(launch_at_login: bool)`，读写 `system_config.yaml` 的 `startup.launch_at_login`。
- `app/core/app_context.py`、`app/core/bootstrap.py`、`app/core/builder/app_builder.py`：把启动设置放进应用上下文，保证主路径和 builder 路径一致。
- `app/ui/settings_dialog.py`：系统设置页新增“登录时自动启动 Sakura”复选框，并按平台支持情况启用/禁用；不支持的平台保存时保留原配置，避免误触发系统写入。
- `main.py`：启动时在配置启用时修复/同步登录项；首次设置页仅在开关变化时应用登录项。
- `app/ui/pet_window.py`：普通设置页仅在开关变化时应用登录项并持久化。
- `tests/unit/test_launch_at_login.py`：覆盖 macOS LaunchAgent、Windows Run、Linux XDG autostart、unsupported 平台。
- `tests/unit/test_settings_service.py`、`tests/ui/test_pet_window.py`：覆盖配置持久化、设置页结果和 PetWindow 保存链路。
- `tmp/launch_at_login_self_review.md`：迁移后的自查文档。

## 平台行为

- macOS：写入/删除 `~/Library/LaunchAgents/com.rvosy.sakura.launch-at-login.plist`，使用 `scripts/start.sh` 或 frozen executable。
- Windows：写入/删除 HKCU `Software\Microsoft\Windows\CurrentVersion\Run` 下的 `Sakura Desktop Pet`。存在 `start.bat` 时优先使用它，否则回退到 `runtime/pythonw.exe`、`runtime/python.exe`、venv Python 或当前 Python。
- Linux：写入/删除 `~/.config/autostart/sakura-desktop-pet.desktop`，使用 XDG autostart。
- 不支持的平台：设置项禁用，底层调用会抛出 `LaunchAtLoginError`。

## 不必要改动检查

- 新分支从 `origin/dev` 独立 worktree 创建，只迁移开机自启动相关代码、测试和本文档。
- 没有带入原工作区里的长期记忆失败弹窗延迟显示改动。
- 没有带入 `.DS_Store`、其它 `tmp/` 文件或原工作区未跟踪噪声。
- 没有发现为了自启动而引入的大范围重构；改动集中在平台适配、设置持久化、设置 UI、应用启动和测试。

## 已知边界和风险

- macOS 当前只写 LaunchAgent plist，没有立即执行 `launchctl bootstrap/kickstart`。优点是不会在用户勾选设置后立刻拉起一个重复进程；行为是在下次登录时生效。若未来做正式 `.app`/notarized 分发，可以评估迁移到 `SMAppService`。
- Windows 使用 `cmd.exe /c start.bat` 是为了复用现有启动环境，但如果 `start.bat` 在错误或退出路径上保留 `pause`，登录启动时可能留下控制台窗口。后续可以考虑增加 silent 启动脚本或 `--no-pause` 模式。
- Linux XDG autostart 依赖桌面环境支持；headless/server 环境即使平台是 Linux，也不会有实际桌面登录自启动效果。
- 启动时的 `ensure_launch_at_login_state` 只在配置为 true 时修复登录项；配置为 false 时不会主动清理历史残留项。设置页里关闭该选项仍会删除本应用注册的登录项。这个取舍是为了避免默认 false 在启动时误删用户外部手动配置的登录项。

## 验证结果

已通过：

```bash
git diff --check
```

已通过：

```bash
/Users/nothing/Sakura/sakura-macos-tts-fix/.venv/bin/python -m compileall -q main.py app tests/unit/test_launch_at_login.py
```

已通过自启动相关聚焦测试：

```bash
/Users/nothing/Sakura/sakura-macos-tts-fix/.venv/bin/python -m pytest tests/unit/test_launch_at_login.py tests/unit/test_settings_service.py tests/unit/test_bootstrap.py tests/ui/test_pet_window.py -q
# 168 passed in 3.32s
```

全量测试当前仍有 1 个失败：

```bash
/Users/nothing/Sakura/sakura-macos-tts-fix/.venv/bin/python -m pytest -q
# 1 failed, 535 passed in 10.29s
# FAILED tests/unit/test_visual_observation.py::test_visual_observation_store_redacts_sensitive_text_and_omits_images
# AssertionError: assert '[REDACTED]' in ''
```

这个失败点在 visual observation 的敏感文本脱敏测试，不在开机自启动链路中；从当前结果看，自启动相关改动没有引入聚焦测试回归。

## 建议

- 自启动功能可以作为独立变更继续推进，提交时只纳入自启动相关文件、测试和本文档。
- Windows release 前最好再在真实 Windows 环境验证一次 HKCU Run + `start.bat` 登录启动体验，重点看是否有控制台窗口残留。
