# WP-0-01 legacy Qt、工具链和验收环境基线

> Phase / Work Package：Phase 0 / WP-0-01
>
> 基线日期：2026-07-15
>
> 工作分支：`refactor/tauri-runtime-v2`
>
> 取证起点：`f31d60b38691496900c6d5c53198f0274a384d54`
>
> 最终状态：`accepted`
>
> 关联 ADR：ADR-0001、ADR-0003
>
> 计划提交：`test(runtime): 收口 legacy Qt 基线验收`

## 1. 范围、非目标和证据边界

本 Work Package 记录迁移前的 legacy Qt、工具链和本机验收能力，不创建 Runtime v2 Tauri 工程。稳定化门禁确认了三处与退出条件直接相关的 P1，因此按 Bug Budget 只修复 legacy Qt 的 QThread、asyncio loop 和 Memory 首次导入竞态，不改变 Assistant 业务语义。

允许写入：

- `docs/runtime-v2/baselines/WP-0-01-legacy-qt-baseline.md`
- `docs/runtime-v2/baselines/run_wp_0_01_baseline.ps1`
- `docs/runtime-v2/baselines/wp_0_01_qt_smoke.py`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 中 WP-0-01 的状态和验收记录
- `main.py`、`app/core/resource_manager.py`、`app/agent/memory.py` 中仅与门禁确认 P1 直接相关的关闭/启动稳定化
- `tests/unit/test_resource_manager.py`、`tests/unit/test_memory_store_resources.py` 中对应回归测试

明确禁止修改：

- 除上述窄修复外的 `main.py`、`app/` 生产代码
- `desktop/`、`plugins/` 生产代码
- `data/` schema、启动入口、角色资源
- `third_party/`、`tools/mcp/` 和旧迁移分支代码

本基线没有安装依赖，没有调用真实 LLM，没有执行真实工具动作，没有触发 TTS 合成或播放，没有采集用户桌面截图。

## 2. legacy Qt 启动与退出链路

### 2.1 启动链

入口是 `main.py::main()`，当前 `start.bat` 最终执行 `runtime/python.exe main.py`。主要顺序如下：

1. `_enable_crash_diagnostics(BASE_DIR)` 打开 `faulthandler` 和未捕获异常记录。当前实现会准备 `data/logs/sakura-crash.log`。
2. 安装 Qt 消息处理器，调用 `_configure_windows_high_dpi()`，然后创建 `QApplication`，设置应用名、常驻托盘语义和亮色 Fusion palette。
3. `run_startup_self_check(BASE_DIR)` 对 `data/`、配置、TTS 缓存和日志目录执行真实写入探针，并检查配置可读写、Qdrant 锁和磁盘空间。
4. `SingleInstanceGuard(BASE_DIR).acquire()` 获取 `data/sakura.lock`；失败时显示已有实例提示并返回 `0`。
5. `ensure_default_configs(BASE_DIR)` 可能创建或补齐 `data/config/mcp.yaml`、`plugins.yaml`。
6. `record_app_version(BASE_DIR)` 可能更新 `data/config/system_config.yaml` 中的版本标记。
7. `MigrationRunner(BASE_DIR).run()` 执行版本化数据迁移；可能更新配置、历史和 `data/migration_backup/`。
8. `_initial_setup_required(BASE_DIR)` 判断角色和聊天 Provider 是否齐备：
   - 需要首次设置时，`_open_first_run_settings()` 启动现有 Tauri Settings；其内部可通过 `_open_first_run_studio()` 打开角色工作室。
   - 配置齐备时，`build_initial_app_context(BASE_DIR)` 建立初始 Qt 应用上下文。
9. `_ensure_launch_at_login_state()` 对登录自启动状态做一致性检查；Windows 路径可能读写当前用户 Run 注册表项。
10. 构造 `PetWindow(context)`，连接 `aboutToQuit -> close_external_tools`，调用 `pet_window.show()`。
11. 零延迟调用 `_start_tts_migration_or_deferred()`：有旧 TTS 整合包时先显示迁移对话框，否则通过 `DeferredStartupWorker` 后台创建 TTS、Tools、MCP、插件等延迟服务。
12. `app.exec()` 进入 Qt 事件循环。

关键代码入口：

- `main.py::main`
- `app/core/selfcheck.py::run_startup_self_check`
- `app/core/instance.py::SingleInstanceGuard`
- `app/core/bootstrap.py::build_initial_app_context`
- `app/core/bootstrap.py::build_deferred_services`
- `app/ui/pet_window.py::PetWindow`

### 2.2 退出链

正常 UI 退出的主要顺序如下：

1. `PetWindow.closeEvent()` 调用 `request_quit()`；直接关闭窗口不是简单隐藏或销毁。
2. `request_quit()` 先阻止 TTS 数据迁移期间退出，并对活动 TTS 下载给出确认和取消逻辑。
3. 退出获批后设置 `_quit_approved`，调用 `close_external_tools()`，再调用 `QApplication.quit()`。
4. `close_external_tools()` 使用 `_shutdown_in_progress` 保证幂等，并依次：
   - 关闭 Tauri Settings 和 Tauri Studio 子进程；
   - 写入 `app.closed` 运行事件；
   - 停止字幕、backchannel、活动任务和 Qt worker；
   - 通过 `ResourceManager.stop_all()` 停止已登记资源；
   - 关闭 TTS Provider、MCP Provider、插件和渲染器等外部服务。
5. `QApplication.aboutToQuit` 再次触发幂等的 `close_external_tools()`，并释放 `SingleInstanceGuard`。
6. `app.exec()` 返回后，`main()` 最多等待 15 秒让已转入 lingering 的 QThread 自然结束，再将退出码交还启动进程；UI 关闭槽仍只做原有 1 秒有限等待。

关键代码入口：

- `app/ui/pet_window.py::closeEvent`
- `app/ui/pet_window.py::request_quit`
- `app/ui/pet_window.py::close_external_tools`
- `app/ui/pet_window.py::close_tts_tools`
- `app/ui/pet_window.py::close_mcp_tools`
- `app/ui/pet_window.py::close_plugins`
- `app/core/resource_manager.py`

代表测试入口：

- `tests/ui/test_pet_window.py`
- `tests/unit/test_selfcheck.py`
- `tests/unit/test_resource_manager.py`
- `tests/unit/test_hardening_regressions.py`
- `tests/unit/test_tauri_studio.py`
- `tests/unit/test_tauri_studio_startup.py`

## 3. 当前功能代码入口和测试入口

下表只记录现有入口，不表示人工真实链路已经通过。

| 能力 | 当前代码入口 | 代表测试入口 |
|---|---|---|
| 首次设置 | `main.py::_initial_setup_required`、`_open_first_run_settings`、`_open_first_run_studio`；`app/ui/tauri_settings.py`；`app/ui/tauri_studio.py`；`tools/settings-tauri/`；`tools/studio-tauri/` | `tests/ui/test_pet_window.py` 中首次设置与首次工作室用例；`tests/unit/test_settings_service.py`；`tests/unit/test_tauri_studio.py`；`tests/unit/test_tauri_studio_startup.py` |
| 聊天 | `app/ui/pet_window.py::send_message`、`_start_chat_worker`、`_handle_reply`；`app/core/chat_worker.py`；`app/core/chat_pipeline.py`；`app/agent/runtime.py::handle_user_message` | `tests/integration/test_chat_pipeline.py`；`tests/integration/test_chat_worker.py`；`tests/integration/test_agent_core.py`；`tests/ui/test_pet_window.py` |
| 取消 | `app/core/cancellation.py::CancellationToken`；`app/core/chat_worker.py::ChatWorker.cancel`、`EventWorker.cancel`；`app/ui/pet_window.py::cancel_pending_action`；关闭链中的 `ResourceManager.stop_all` | `tests/integration/test_chat_worker.py::test_chat_worker_cancel_suppresses_result_and_error`；`tests/unit/test_agent_runtime.py`；`tests/unit/test_resource_manager.py`；`tests/ui/test_pet_window.py` |
| 角色切换 | `app/ui/pet_window.py::_apply_tauri_settings_result`、`_apply_character`；`app/config/settings_service.py::save_current_character_id` | `tests/ui/test_pet_window.py` 中 `_apply_character`、角色切换期间 Memory/TTS 用例；`tests/unit/test_settings_service.py` |
| 历史 | `app/ui/pet_window.py::show_history`、`_record_history`；`app/ui/history_window.py::HistoryWindow`；`app/storage/chat_history.py::ChatHistoryStore`；`app/storage/history_digest.py` | `tests/ui/test_history_window.py`；`tests/ui/test_pet_window.py` 中历史持久化/回复回看用例；`tests/unit/test_history_digest.py` |
| Memory | `app/agent/memory.py::MemoryStore`；`app/agent/memory_curator.py`；`app/agent/memory_curation_worker.py`；`app/ui/pet_window.py::_start_memory_curation`、`_maybe_start_memory_backfill` | `tests/unit/test_memory_curator.py`；`tests/unit/test_memory_store_resources.py`；`tests/unit/test_agent_runtime.py`；`tests/ui/test_pet_window.py` |
| Tools | `app/agent/builtin_tools.py`；`app/agent/tools/registry.py::ToolRegistry`；`app/agent/tool_policy.py`；`app/agent/runtime.py` 工具循环；`app/ui/tool_confirmation_panel.py` | `tests/unit/test_tool_registry.py`；`tests/integration/test_native_tool_calls.py`；`tests/unit/test_agent_runtime.py` |
| MCP | `app/agent/mcp/config.py`；`app/agent/mcp/provider.py::MCPToolProvider`；`app/agent/mcp/bridge.py`；`app/core/bootstrap.py::build_deferred_services` | `tests/unit/test_mcp_runtime.py`；`tests/unit/test_web_search_mcp_server.py`；`tests/integration/test_agent_core.py` |
| 插件 | `app/plugins/discovery.py`；`app/plugins/manager.py::PluginManager`；`app/plugins/services.py`；`app/core/bootstrap.py::build_deferred_services`；实现位于 `plugins/` | `tests/unit/test_plugin_system.py`；`tests/unit/test_plugin_services.py`；`tests/unit/test_plugin_advanced.py`；`tests/unit/test_playwright_browser.py` |
| TTS | `app/voice/factory.py::create_tts_provider`；`app/voice/tts_synthesis.py`；`app/voice/tts_playback.py`；`app/voice/tts_service.py`；`app/ui/pet_window.py` 的 TTS warmup/播放/关闭链 | `tests/unit/test_tts.py`；`tests/unit/test_tts_service_state.py`；`tests/unit/test_audio_sink_player.py`；`tests/unit/test_audio_verification.py`；`tests/ui/test_pet_window.py` |
| 截图 | `app/ui/pet_window.py::_show_manual_screenshot_overlay`、`_capture_virtual_desktop`；`app/ui/manual_screenshot_overlay.py`；`app/ui/screen_capture.py`；`app/agent/screen_observation.py` | `tests/ui/test_manual_screenshot_overlay.py`；`tests/ui/test_screen_capture_crop.py`；`tests/unit/test_visual_observation.py`；`tests/ui/test_pet_window.py` |
| 主动互动 | `app/ui/pet_window.py::_check_due_reminders`、`_check_screen_awareness`、`_run_event_worker`、`emit_runtime_event`；`app/agent/runtime.py::handle_event`；`app/agent/runtime_events.py`；`app/agent/reminders.py` | `tests/unit/test_agent_runtime.py`；`tests/unit/test_runtime_events.py`；`tests/unit/test_visual_observation.py`；`tests/ui/test_pet_window.py` |
| 设置 | `app/ui/pet_window.py::show_settings`、`_try_show_tauri_settings`、`_apply_tauri_settings_result`；`app/ui/tauri_settings.py`；`tools/settings-tauri/` | `tests/ui/test_pet_window.py` 中设置进程、预览、保存、回滚、Memory RPC 用例；`tests/unit/test_settings_service.py`；`tests/unit/test_settings_resource_tasks.py` |
| 工作室 | `app/ui/pet_window.py::_open_tauri_studio_from_settings`；`app/ui/tauri_studio.py`；`tools/studio-tauri/`；legacy 实现仍位于 `tools/studio/` | `tests/unit/test_tauri_studio.py`；`tests/unit/test_tauri_studio_startup.py`；`tests/unit/test_character_studio.py`；`tests/ui/test_studio.py` |

## 4. 工具链和参考机器

### 4.1 版本

| 项目 | 版本或状态 | 取证方式 |
|---|---|---|
| Windows | Windows 10 Pro 20H2，build `19042.928`，64 位 | Windows 注册表 `CurrentVersion` |
| Python | CPython `3.12.8`，64 位，`D:\Project\sakura\runtime\python.exe` | `runtime/python.exe -c ...` |
| PySide6 | `6.11.1` | Python import |
| Qt runtime / compiled | `6.11.1` / `6.11.1` | `qVersion()` 与 pytest-qt 会话头 |
| pytest | `9.1.1` | 完整测试会话头 |
| Rust | `rustc 1.97.0 (2d8144b78 2026-07-07)` | `rustc --version` |
| Cargo | `cargo 1.97.0 (c980f4866 2026-06-30)` | `cargo --version` |
| Node | `v24.16.0` | `node --version` |
| npm | `12.0.1` | `npm --version` |
| Tauri CLI | 缺失；`cargo tauri --version` 返回 `no such command: tauri` | 本机命令探测；未安装新依赖 |
| WebView2 Runtime | `149.0.4022.80` | Windows Uninstall 注册表和安装目录 |

现有 Settings/Studio release 二进制存在：

- `tools/settings-tauri/src-tauri/target/release/sakura-settings.exe`
- `tools/studio-tauri/src-tauri/target/release/sakura-studio.exe`

这只证明已有构建产物存在，不替代 Tauri CLI 基线，也不证明可在干净机重建。

### 4.2 参考机器

| 项目 | 当前机器 |
|---|---|
| 制造商/型号 | ASUS / System Product Name |
| CPU | AMD Ryzen 7 5700G，8 核 16 线程 |
| 内存 | 34,142,007,296 bytes，约 31.8 GiB |
| GPU | NVIDIA GeForce GT 730；同时存在 OrayIddDriver Device |
| 仓库磁盘 | ZHITAI Ti600 1TB，NVMe，GPT；accepted 批次仓库位于 `D:` |
| 会话 | Windows console session |
| 显示器 | 1 块 `PHL 275E1`，2560×1440，可用区 2560×1400，约 59.951 Hz |
| DPI | device pixel ratio 1.0，logical DPI 96×96，即 100% |

## 5. 本机人工验收能力

| 能力 | 本机条件 | 当前结论 |
|---|---|---|
| 单屏 | 1 块 2560×1440 物理显示器 | 可执行；本轮只验证真实窗口逻辑可见，未做逐像素视觉审查 |
| 多屏/负坐标 | 当前只有 1 块屏幕 | 受限 |
| 100% DPI | 当前为 100% | 可执行；未完成物理布局审查 |
| 125%/150% DPI | 当前会话未配置 | 受限 |
| 中文 IME | 系统文化为 `zh-CN`，键盘布局 `00000804` | 环境存在；候选框位置、组合输入和焦点恢复未物理验收，受限 |
| 音频设备 | `PHL 275E1 (NVIDIA High Definition Audio)` 和 `Realtek Digital Output` 状态 OK | 设备存在；未做可听播放、切换或断开故障验收，受限 |
| 干净机 | 当前机器包含源码、Runtime、构建产物、开发配置和缓存 | 不具备，受限 |
| 多角色切换 | 当前存在 Sakura、N.A.V.I 两个角色包 | 具备资源条件；本轮未物理操作切换 UI，受限 |
| 真实 Provider | 已配置聊天 Provider | 为避免外部请求、费用和用户历史写入，本轮未调用，受限 |
| TTS | 当前真实配置启用 GPT-SoVITS；隔离副本不复制约 16 GiB 的外部 TTS runtime，因此启动时安全降级为 Null Provider | 未启动真实 TTS 服务，合成和播放受限 |
| WebView2 Settings/Studio | WebView2 和已有二进制存在 | 未做物理交互，受限 |

## 6. 自动测试基线

### 6.1 要求的完整测试

accepted 门禁命令：

```powershell
.\docs\runtime-v2\baselines\run_wp_0_01_baseline.ps1
```

脚本在 `temp/runtime-v2-wp-0-01/<run-id>/workspace` 创建完整独立源码树，显式把该源码树放在 `runtime/python.exe` 的固定 `python312._pth` 项之前，并使用全新 `.pytest-basetemp`。最终结果：

```text
collected 1463 items
1460 passed
3 skipped
pytest reported: 52.93s
exit code: 0
```

三个 skipped 均为 `tests/unit/test_tts_bundle.py` 中只适用于 macOS/bash 路径的 source installer 测试。

### 6.2 初始失败的归因与关闭

| 分组 | 数量 | 事实与复现 |
|---|---:|---|
| `tests/unit/test_backchannel_model_cache.py` | 6 errors | 固定 `--basetemp=.pytest-basetemp` 中存在 2026-07-14 创建的悬空 `hf-homecurrent -> hf-home0` 符号链接；pytest 清理时在 `PermissionError`/`FileNotFoundError` 之间失败。没有删除该既有工件。使用新的系统临时 basetemp 复跑本文件为 `6 passed in 0.32s`。 |
| `tests/unit/test_storage_paths.py::TestStoragePathsSnapshot` | 6 errors | `setup_method` 直接执行 `Path("D:/").exists()`；当前账号访问 `D:\` 根目录返回 `PermissionError [WinError 5]`。 |
| `tests/unit/test_tts_service_state.py` | 6 failed | `_stub_provider()` 同样直接执行 `Path("D:/").exists()`，在进入被测逻辑前失败。 |

失败集合复跑命令：

```powershell
.\runtime\python.exe -m pytest tests/unit/test_backchannel_model_cache.py tests/unit/test_storage_paths.py tests/unit/test_tts_service_state.py -q
```

复跑结果：`33 passed, 6 failed, 12 errors in 1.01s`。backchannel 复跑仍被既有 basetemp 工件阻断；D 盘相关 12 项稳定复现。

backchannel 隔离诊断：

```powershell
.\runtime\python.exe -m pytest tests/unit/test_backchannel_model_cache.py -q --basetemp=<系统临时目录>
```

结果：`6 passed in 0.32s`。

最终门禁在正常 Windows 权限下、全新隔离 basetemp 中执行，以上 18 项全部通过；没有删除或修改仓库原有 `.pytest-basetemp`。因此它们被归因为旧固定 basetemp 工件和受限执行环境，不再是 accepted 门禁失败。

### 6.3 测试数据副作用

初始完整 pytest 后观察到真实 `data/config/` 中 `api.yaml`、`characters.yaml`、`mcp.yaml`、`plugins.yaml`、`system_config.yaml` 的修改时间统一刷新到测试运行时刻。对有既有 `.bak` 的三份文件做 SHA-256 比较：

- `api.yaml` 与 `api.yaml.bak` 相同。
- `characters.yaml` 与 `characters.yaml.bak` 相同。
- `system_config.yaml` 与 `system_config.yaml.bak` 相同。

稳定化期间第一次“独立工作目录”诊断仍被 `runtime/python312._pth` 中固定的仓库父目录穿透，向真实 `data/logs/sakura-runtime.log` 追加了测试日志；该历史追加没有自动回写或删除。

最终脚本同时记录真实 `data/` 所有文件的相对路径、长度、UTC 修改时间和 SHA-256。accepted 批次前后清单逐项相同，配置、日志、运行事件、历史、Memory、插件数据和 TTS 数据均未变化。

## 7. 冷启动可见时间基线

### 7.1 定义

本基线把“可见时间”定义为：

```text
t0 = 父进程即将创建新的 runtime/python.exe 进程
t_visible = 真实 Windows Qt 进程中，PetWindow 在调用 show() 后首次满足
            top-level widget + isVisible() == true
visible_ms = t_visible - t0
```

此定义包含 Python 进程创建、模块导入、自检、单实例锁、默认配置/版本/迁移检查、初始上下文和 `PetWindow` 构造；不等待 MCP、插件、Memory 或 TTS 延迟服务全部 ready。

### 7.2 数据隔离和有界执行方法

accepted 测量使用：

1. 用 `git archive HEAD` 创建独立源码树，并覆盖当前工作区内待验收的已跟踪改动。
2. 复制当前角色包和必要的 `data/` 快照；不复制日志、诊断、缓存和 TTS 整合包大文件。
3. pytest 启动器在导入 pytest 前把隔离源码树插入 `sys.path[0]`，覆盖 bundled Python `python312._pth` 中固定的真实仓库路径。
4. 每个 GUI 样本在同一隔离源码树中创建全新的 Python 进程。
5. 只在进程内临时包裹 `PetWindow.show`：轮询真实顶层窗口可见状态，可见后 1 秒调用 `request_quit()`。
6. 子进程可见等待上限 15 秒，父进程总等待上限 20 秒；超时只终止该次根进程及已记录后代 PID。
7. 每次退出后用 `psutil` 持续记录该样本的后代进程，并核查是否仍有存活 PID；不使用按进程名全局清理。
8. 整批执行前后对真实 `data/` 建立内容和修改时间清单，任何差异都使脚本失败。

脚本路径为 `docs/runtime-v2/baselines/run_wp_0_01_baseline.ps1`；GUI 子进程驱动位于 `docs/runtime-v2/baselines/wp_0_01_qt_smoke.py`。最终 accepted 批次的真实 `data/` 前后清单相同。

### 7.3 采样和统计

- 样本数：10。
- 每次使用新进程和相同的预运行数据副本。
- 没有重启 Windows，也没有清空系统 page cache；因此是“新进程冷启动代理”，不是严格 OS-cold/reboot-cold。
- 统计：最小值、中位数、算术平均值、最近秩 p95 和最大值；10 个样本的最近秩 p95 等于第 10 个有序样本。

原始样本，单位 ms：

```text
1537.577
1102.601
1164.903
1066.849
1069.251
1073.402
1117.099
1094.203
1130.674
1096.477
```

统计：

| 指标 | 数值 |
|---|---:|
| min | 1066.849 ms |
| median | 1099.539 ms |
| mean | 1145.304 ms |
| p95，nearest-rank | 1537.577 ms |
| max | 1537.577 ms |

主计划参考机器 p95 目标为不高于 1 秒；当前独立源码树新进程代理 p95 为 1.538 秒，存在约 538 ms 的基线差距。本 WP 不把性能差距误判为功能 P1，留作后续性能工作。

## 8. Qt 真实冒烟清单

状态只使用“通过 / 失败 / 受限 / 未执行”。“通过”仅覆盖表中写明的范围。

| 项目 | 状态 | 证据或限制 |
|---|---|---|
| 当前配置启动并创建真实 `QApplication` | 通过 | 10/10 样本进入事件循环，退出码均为 0 |
| `PetWindow` 真实顶层窗口变为 visible | 通过 | 10/10 样本检测到 `PetWindow.isVisible()` |
| 单实例锁获取与释放 | 通过 | 10/10 启动均越过锁门禁并正常退出；未执行双实例冲突 UI |
| MCP/插件延迟启动期间退出 | 通过 | 首样本日志观察到 MCP 和插件启动，随后 `request_quit()` 关闭；没有临时目录相关残留 |
| 正常退出 | 通过 | 10/10 `request_quit()` 返回 True，main 返回 0，stderr 为空 |
| 退出后残留进程 | 通过 | 每次临时命令行残留为 0；批次结束没有新增 Python、Node、浏览器、Settings、Studio 进程 |
| 强杀/崩溃退出 | 未执行 | WP-0-01 不进行故障注入；只记录正常退出基线 |
| 首次设置完整流程 | 受限 | 当前数据已配置；物理表单输入、保存和取消未执行 |
| 真实聊天 | 受限 | 未调用真实 Provider，避免外部请求、费用和用户历史写入 |
| 聊天取消 | 受限 | 没有启动真实聊天请求 |
| 角色切换 | 受限 | 隔离副本包含两个真实角色包，但未做物理 A/B 切换 |
| 历史窗口查看/清空 | 受限 | 未进行物理点击；清空会改变用户数据 |
| Memory 读写/整理 | 受限 | 未进行真实对话和 Memory 后端验收 |
| Tools 确认和执行 | 受限 | 未选择可证明无副作用的真实工具动作 |
| MCP 工具调用 | 受限 | 只验证服务器生命周期，没有执行真实 MCP tool call |
| 插件功能交互 | 受限 | 只观察加载/关闭，没有操作移动端或浏览器插件 |
| TTS 合成/播放/取消 | 受限 | 隔离副本不复制外部 TTS runtime，Provider 安全降级；未启动服务或做可听验收 |
| 手动截图 | 受限 | 为避免采集真实桌面内容，未打开选区和保存链路 |
| 主动互动/提醒/自动观察 | 受限 | 没有等待触发窗口，也没有授权桌面观察 |
| 设置窗口 | 受限 | 已有 Tauri Settings 二进制和 WebView2，但未做物理交互 |
| 工作室 | 受限 | 已有 Tauri Studio 二进制和 WebView2，但未做导入/编辑/保存 |
| 单屏布局视觉质量 | 受限 | 真实窗口逻辑可见；未由人在屏幕上审查锚点、清晰度和抖动 |
| 多屏/负坐标 | 受限 | 本机仅一块屏幕 |
| 125%/150% DPI | 受限 | 本机当前只有 100% |
| 中文 IME 候选框与焦点 | 受限 | 中文输入布局存在，但没有物理键盘组合输入 |
| 音频设备切换和故障 | 受限 | 设备存在，但未播放、切换或拔出设备 |
| 干净 Windows | 受限 | 没有干净机环境 |

## 9. 已知问题、风险和阻塞

1. Tauri CLI 缺失，无法在本机直接执行 `cargo tauri`；本 WP 按要求不安装依赖。
2. 当前只有单屏、100% DPI；多屏、目标 DPI、中文 IME 物理输入和干净 Windows 无法验收。
3. accepted 隔离门禁没有启动真实 TTS runtime；音频设备存在不等于合成和播放链已通过。
4. 独立源码树新进程启动代理 p95 为 1.538 秒，高于主计划 1 秒目标。
5. 初始取证和一次稳定化诊断曾写入真实配置修改时间、runtime log 和运行事件；由于无法证明安全回退边界，这些历史追加没有自动删除。最终 accepted 脚本已证明当前重复执行不再产生真实数据变化。
6. 稳定化门禁曾确认三处 P1，均已添加回归覆盖并在最终 10 次真实冒烟中未复现：
   - 延迟启动 QThread 超过 1 秒关闭等待后，解释器销毁运行中 wrapper 导致 `0xC0000409`。
   - MCP loop 重复 stop 导致 pending task 清理中断和未等待协程警告。
   - Memory preload 与 MCP 并发首次导入 anyio，导致 partially initialized module 和启动超时。
7. accepted 批次没有确认新的 P0/P1、死锁或残留进程；以上人工受限项继续作为后续阶段验收输入，不阻塞 WP-0-01。

## 10. 重复执行方式

### 10.1 自动测试

```powershell
cd D:\Project\sakura
.\docs\runtime-v2\baselines\run_wp_0_01_baseline.ps1
```

脚本会创建唯一隔离目录，不删除或复用仓库原有 `.pytest-basetemp`，并在完成后删除包含配置快照和角色资源的工作副本，仅保留忽略目录中的结果日志。任一 pytest、GUI、残留进程、数据清单或清理步骤失败都会返回非零退出码。

### 10.2 工具链

```powershell
.\runtime\python.exe --version
.\runtime\python.exe -c "import PySide6; from PySide6.QtCore import qVersion; print(PySide6.__version__, qVersion())"
rustc --version
cargo --version
node --version
npm --version
cargo tauri --version
```

WebView2 版本从 Windows Uninstall 注册表中的 `Microsoft Edge WebView2 Runtime` 读取。

### 10.3 GUI/启动

GUI/启动与完整 pytest 由同一脚本执行。重复时保留：新进程计时、15 秒可见超时、20 秒父级硬上限、可见后 `request_quit()`、后代 PID 跟踪、stderr 检查和真实 `data/` 前后清单。不得恢复旧目录联接方法，也不得用 `taskkill /IM python.exe` 一类全局清理方式影响其他进程。

## 11. 独立回退

本 Work Package 没有 schema、角色资源或 Runtime v2 实现变更；包含 legacy Qt 启动/退出 P1 的窄修复。独立回退方式是：

```powershell
git revert <WP-0-01-commit>
```

回退应整体撤销基线脚本、状态记录、对应测试以及 `main.py`、`app/core/resource_manager.py`、`app/agent/memory.py` 的窄修复；不得触碰 `data/`、角色资源、`desktop/` 或其他生产模块。

本轮观察到的真实日志、配置时间戳和 `app.closed` 追加没有在 WP-0-01 中自动回写或删除，因为缺少可证明不会覆盖用户同期数据的安全回退边界。该残留必须作为已知风险保留事实记录。

## 12. 退出门复核

| 退出项 | 结果 |
|---|---|
| 代码和测试入口已记录 | 满足 |
| 工具链和验收环境已记录 | 满足；Tauri CLI 明确为缺失 |
| 完整 pytest 已执行并记录数量/耗时 | 满足；1460 passed，3 skipped，退出码 0 |
| Qt 启动、可见、正常退出和残留进程有真实证据 | 满足 |
| 人工受限项明确 | 满足 |
| 冷启动定义、参考机器、样本和统计明确 | 满足 |
| 重复执行方法安全 | 满足；完整独立源码树、唯一 basetemp、后代 PID 跟踪和有界清理 |
| 数据损坏/污染风险为零 | 满足；accepted 批次真实 `data/` 前后清单完全一致 |
| 可标记 accepted | 是；WP-0-01 更新为 `accepted` |
