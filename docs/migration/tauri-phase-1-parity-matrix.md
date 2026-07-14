# Sakura Tauri Assistant 第一阶段功能对照矩阵

> 基线日期：2026-07-14（Asia/Shanghai）  
> 基线提交：`0ed04c5e6`  
> 实施分支：`feat/tauri-assistant-migration`

## 1. 基线结论

### 1.1 自动测试

执行命令：

```powershell
.\runtime\python.exe -m pytest
```

结果：`1456 passed, 3 skipped in 54.93s`。

运行环境：Windows、Python 3.12.8、pytest 9.1.1、PySide6 6.11.1。三个跳过项来自现有 TTS bundle 测试，不是本次迁移新增跳过。

### 1.2 启动与资源基线

测量方法：从 PowerShell 启动现有 `runtime/python.exe main.py`，以进程输入空闲和首个非零 `MainWindowHandle` 作为可自动采集的启动代理；窗口出现 10 秒后采样 5 秒 CPU，并读取 Python 主进程内存。该结果包含当前用户配置、已启用插件、MCP 和 TTS 后台预热，只用于同机迁移前后对比。

| 指标 | Qt 基线 | 说明 |
|---|---:|---|
| 进程启动至输入空闲探测返回 | 151.2 ms | `WaitForInputIdle` 未报告成功，不作为首帧结论 |
| 进程启动至首个窗口句柄 | 1546.5 ms | 作为“首个可见窗口/首帧”的自动化代理 |
| 10 秒后空闲 CPU | 0.312% | 5 秒采样，按逻辑处理器数归一化 |
| 工作集 | 386.6 MiB | Python 主进程 |
| 私有内存 | 818.5 MiB | Python 主进程 |
| 线程数 | 38 | Python 主进程 |
| 窗口标题 | `N.A.V.I.` | 当前角色标题 |

本次测量通过主窗口关闭流程退出，但进程返回 `0xC0000409`，且崩溃日志没有 Python traceback。迁移验收需把“正常退出返回 0 且无残留 Python/MCP/TTS/浏览器进程”作为硬门槛。

### 1.3 Task 13 Tauri 实机测量

同机使用 `runtime/python.exe main.py` 启动 release Tauri，首个非零 `MainWindowHandle` 继续作为窗口代理；启动 10 秒后采样 5 秒 CPU。窗口指标采集两次热启动，Brain 就绪状态随后通过窗口可访问性文本确认。

| 指标 | 样本 1 | 样本 2 | 结论 |
|---|---:|---:|---|
| 进程启动至首个窗口句柄 | 391.7 ms | 485.8 ms | 平均 438.8 ms，比 Qt 代理快约 71.6% |
| 完整进程树空闲 CPU | 0.137% | 0.059% | 平均 0.098%，未见空闲 CPU 回归 |
| 完整进程树工作集 | 814.4 MiB | 816.6 MiB | 平均 815.5 MiB，包含 WebView、Brain、MCP 与控制台宿主 |
| 完整进程树私有内存 | 1090.0 MiB | 1097.4 MiB | 平均 1093.7 MiB；旧基线未统计子进程，不能直接同比 |
| 完整进程树线程数 | 288 | 288 | WebView 多进程模型占主要部分 |
| Brain Host 工作集 | 313.0 MiB | 未单独重复采样 | 比 Qt 主进程低约 19.0% |
| Brain Host 私有内存 | 768.8 MiB | 未单独重复采样 | 比 Qt 主进程低约 6.1% |
| Brain Host 线程数 | 28 | 未单独重复采样 | 比 Qt 主进程少约 26.3% |

这两次测量发生在本地模型缓存已存在的环境，不能代表全新机器首次冷启动。此前同一配置下真实 Brain 帧握手约 4 秒；首次混合接话模型冷加载曾明显变慢。精确 Brain ready/首帧应用内打点和干净机冷启动仍是发布验收风险。

### 1.4 手工基线限制

- 中文/日文 IME、透明窗口黑边、混合 DPI、多显示器拖动、真实音频播放和真实截图必须在交互式 Windows 桌面验收，pytest 只能覆盖结构和部分行为。
- “首帧”当前没有应用内打点；Task 1 起应在 Tauri `setup`、主窗口 `DOMContentLoaded`/首个渲染完成处增加可比较时间点。
- 性能对比必须使用同一机器、同一角色、同一插件/MCP/TTS 配置，并分别记录首次冷启动和第二次热启动。

### 1.5 Gate A 启动路由收口验证

本轮先在 Node.js `v24.16.0` 下复现完整 Python 门禁：`1545 passed, 3 skipped, 13 failed, 12 errors`。其中 7 个失败来自 Node 24 已移除的 `--experimental-default-type=module`，其余失败/错误来自测试对受限 `D:\` 和 pytest 临时目录链接的环境假设。测试运行器现会先探测旧 Node 参数支持情况，Node 24 自动改用标准 ESM 入口；路径夹具改用仓库安全临时目录或 `tmp_path`，未修改生产逻辑。

修复和 Gate A 收口后的自动验证：

- `runtime/python.exe -m pytest`：`1571 passed, 3 skipped in 31.41s`。
- desktop Rust：`37 passed`；`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings` 和 release build 通过。
- `desktop/frontend` 下 17 个 JavaScript 文件全部通过 `node --check`。
- Rust 覆盖 Runtime 缺失、Hello 协议不兼容、`onboarding_required` 不重启、有限崩溃恢复、`session_generation` 更新、重复状态幂等和超过阈值进入修复路由。
- Python/前端覆盖无角色上下文设置、同进程重建角色上下文，以及同一 WebView 会话替换角色、主题、字幕和布局 Bootstrap。

隔离实机验证使用 release `sakura-desktop.exe`，只在 `temp/gate-a-*` 临时目录写入虚拟配置，验收后已删除：

| 场景 | 实际证据 | 结论 |
|---|---|---|
| `ready` | 隔离目录复制现有 N.A.V.I 角色并写入虚拟聊天模型配置；只显示桌宠业务窗口，Brain Host 数量为 1 | 通过 |
| 第二次启动 | 第二个 desktop 进程自动退出；既有 desktop 和 Brain Host 数量仍分别为 1 | 通过 |
| `brain_recovering` | 强制终止 Brain 后桌宠窗口保持可见；新 Brain PID 建立，旧 PID 未复用 | 通过 |
| 恢复后退出 | 主窗口关闭后 desktop 正常退出；未发现 Brain 或已记录子进程残留 | 通过 |
| `onboarding_required` | 空配置隔离目录显示 `Sakura 设置`，桌宠主窗保持隐藏，Brain Host 数量为 1 | 通过 |
| `runtime_repair` | 隔离目录缺少 Runtime；4 次有限启动尝试后显示 `Sakura 启动修复`，桌宠和首次设置均未显示 | 通过 |
| 同会话完成首次设置 | 自动契约通过；实机尝试刷新隔离设置 WebView 后，Windows UI Automation 未暴露“完成并启动 Sakura”按钮节点，无法替代真实点击 | 受限 |
| 修复页打开诊断 | 修复页命令、诊断窗口幂等和 Brain 不可用时本地诊断 DTO 均有自动覆盖；本轮未完成物理点击 | 部分通过 |

Gate A 的实现与自动门禁已收口；上述两项交互限制不提升为 Gate B 人工签字，也不作为删除 Qt 行为对照的依据。

## 2. 第一阶段等价矩阵

状态枚举：`自动覆盖`、`部分覆盖`、`手工覆盖`、`待迁移`。

| 能力 | 现有生产入口 | 现有代表测试 | Qt 基线 | 第一阶段必须达到的等价行为 | Tauri 验收入口 |
|---|---|---|---|---|---|
| 透明、无边框、桌宠窗口 | `main.py`; `app/ui/pet_window.py` 的窗口 flags 与 `WA_TranslucentBackground`；`app/ui/window_backdrop.py` | `tests/ui/test_pet_window.py`; `tests/ui/test_window_backdrop.py` | 部分覆盖 | 主窗口透明、无系统边框、无黑边；WebView 不获得任意 Shell/文件访问 | Rust 窗口测试 + Windows 手工截图 |
| 始终置顶与临时抑制 | `PetWindow._window_flags`、`_apply_window_flags`、`_toggle_always_on_top`、次级窗口抑制逻辑 | `test_pet_window_toggle_always_on_top_saves_and_applies`; `test_registered_secondary_window_suppresses_topmost_until_hidden` | 自动覆盖 | 设置可持久化；设置/工作室/截图覆盖层显示时正确降级，关闭后恢复 | `windows` Rust 测试 + 前端状态测试 |
| 拖动 | `PetWindow._handle_mouse_press/_move/_release`; Wayland `startSystemMove` 兼容 | `test_pet_window_drag_uses_window_local_anchor_not_frame_geometry`; `test_pet_window_drag_uses_start_system_move_when_window_handle_supports` | 自动覆盖 | 立绘与空白区均可拖动；点击不误判为拖动；Windows 使用 Tauri drag API | 前端事件测试 + Windows 手工拖动 |
| 多显示器与 DPI | `main._configure_windows_high_dpi`; `PetWindow._schedule_screen_change_relayout`; `app/ui/screen_capture.py` | `test_pet_window_screen_change_restores_stage_geometry`; `tests/ui/test_screen_capture_crop.py`; `tests/unit/test_pet_layout.py` | 部分覆盖 | 100/125/150/200% DPI 正常；混合 DPI 移动后尺寸、底边锚点和截图坐标不漂移 | Rust monitor/geometry 测试 + 多屏手工验收 |
| 中文/日文输入法 | `PetWindow.input_edit`（Qt `QLineEdit`） | 无专门 IME 自动测试 | 手工覆盖 | WebView 输入框支持中文拼音、日文组合输入；组合阶段 Enter 不误发送 | JS composition 契约测试 + Windows IME 手工验收 |
| 立绘加载与表情切换 | `app/ui/portrait_controller.py`; `app/ui/portrait_utils.py`; `PetWindow.portrait_controller` | `test_portrait_controller_scales_pixmap_by_configured_percent`; `test_portrait_controller_never_resizes_parent_window`; `tests/unit/test_pet_layout.py` | 自动覆盖 | 保持角色默认立绘、tone/portrait 映射、预加载、缩放和过渡；角色资源只能经受控 asset URL 读取 | `test_tauri_pet_frontend.py` + 角色切换手工验收 |
| 字幕、分段与打字机 | `app/ui/subtitle_controller.py`; `PetWindow.subtitle_controller` | `test_subtitle_controller_updates_display_speed`; `test_reply_segments_queue_while_current_segment_is_active`; `test_subtitle_ignores_late_finished_callback_from_previous_segment` | 自动覆盖 | 分段顺序、打字速度、等待态、取消、迟到回调隔离、语言切换与气泡动画等价 | JS 定时器测试 + 聊天契约测试 |
| 输入栏、发送与忙碌态 | `PetWindow.send_message`; `app/core/chat_worker.py`; `app/core/chat_pipeline.py` | `tests/integration/test_chat_worker.py`; `tests/integration/test_chat_pipeline.py`; `test_set_busy_*`; `test_send_message_*` | 自动覆盖 | 同时仅一个前台聊天；空消息规则、视觉附件、发送反馈和控件锁定保持一致 | `test_assistant_service.py`; `test_tauri_brain_chat_contract.py` |
| 取消、迟到结果与错误 | `ChatWorker.cancel`; `EventWorker.cancel`; `PetWindow` worker 清理与 error/reply handler | `test_shutdown_ignores_late_progress_and_reply`; `test_event_error_cleans_transient_progress_during_shutdown`; `tests/integration/test_chat_worker.py` | 自动覆盖 | 取消能结束当前交互；晚到进度/回复不污染新会话；网络、模型、格式错误有稳定 DTO 和可理解 UI | Python 服务测试 + IPC/前端契约测试 |
| 工具确认与拒绝 | `app/agent/actions.py`; `app/ui/tool_confirmation_panel.py`; `PetWindow.pending_tool_action`; `AgentRuntime` | `tests/integration/test_agent_core.py`; `tests/integration/test_native_tool_calls.py`; `tests/unit/test_tool_registry.py`; `test_action_resolution_clears_queued_reply_batches` | 自动覆盖 | 前端只发送 action ID；参数由 Python pending map 取回；过期、重复、跨 session ID 拒绝；确认/拒绝后继续对话 | `test_assistant_service.py`; `test_tauri_brain_chat_contract.py` |
| TTS 合成 | `app/voice/tts.py`; `tts_synthesis.py`; `tts_service.py` | `tests/unit/test_tts.py`; `tests/unit/test_tts_service_state.py`; `tests/unit/test_audio_verification.py` | 自动覆盖 | Provider、语言守卫、预生成、取消和错误回退保持；Python 不依赖 Qt signal/player | `test_tts_synthesis_service.py` |
| 音频播放 | `app/voice/tts_playback.py`; `playback_controller.py`; `audio_sink_player.py` | `tests/unit/test_audio_sink_player.py`; `tests/unit/test_tts.py` | 自动覆盖 | Rust 单一拥有播放、停止、音量和播放事件；角色/设置切换和退出停止旧音频；临时文件有 TTL | Rust `audio` 测试 + 实机播放 |
| 快速接话（Backchannel） | `app/backchannel/controller.py`; `PetWindow` backchannel cache/prepare/play | `tests/ui/test_backchannel_controller.py`; `test_pet_window_backchannel_*`; `tests/unit/test_backchannel_*` | 自动覆盖 | 保留选择、缓存和字幕回退；不与正式 TTS 重叠；统一走 Rust 音频链 | Assistant/TTS 契约测试 + 手工连续对话 |
| 手动框选截图 | `app/ui/manual_screenshot_overlay.py`; `app/ui/screen_capture.py`; `PetWindow._handle_screenshot_button_clicked` | `tests/ui/test_manual_screenshot_overlay.py`; `tests/ui/test_screen_capture_crop.py`; `test_manual_screenshot_*` | 自动覆盖 | Tauri 覆盖层完成框选；区域坐标跨 DPI 正确；截图以临时资源描述符交给 Brain，默认不持久化 | Rust `capture` + JS overlay + 手工多屏框选 |
| 自动屏幕观察 | `app/agent/screen_observation.py`; `screen_awareness.py`; `PetWindow._check_screen_awareness` | `tests/unit/test_visual_observation.py`; `test_screen_awareness_*`; `test_screen_observation_encode_worker_*` | 自动覆盖 | Python 保留决策、缩放、编码、视觉消息与摘要；Rust 执行捕获；关闭后停止调度；不抢占聊天 | `test_tauri_observation_contract.py` + Rust `capture` |
| 主动互动 | `app/agent/runtime.py`; `app/agent/runtime_events.py`; `PetWindow` EventWorker/event handlers | `tests/integration/test_agent_core.py`; `test_due_reminder_does_not_start_while_active_event_exists`; `test_screen_awareness_does_not_start_while_active_event_exists` | 自动覆盖 | 主动事件通过统一事件进入前端，不抢占用户聊天；忙碌时排队或丢弃规则保持 | `test_assistant_service.py`; `test_tauri_observation_contract.py` |
| 提醒 | `app/agent/reminders.py`; `PetWindow._check_due_reminders` 的 `QTimer` 调度 | `test_due_reminder_passes_single_agent_event_argument`; `test_reminder_event_reply_marks_payload_id_after_consuming_result`; agent core tests | 自动覆盖 | 无 Qt scheduler 定期轮询；提醒只消费一次；关闭顺序不泄漏 worker | scheduler/Assistant 服务测试 |
| 设置 | `app/ui/tauri_settings.py`; `tools/settings-tauri/frontend`; `PetWindow.show_settings` | `tests/unit/test_tauri_settings.py`（若存在）及 `tests/ui/test_pet_window.py` 中 `test_tauri_settings_*` | 自动覆盖 | 合并进同一 Tauri App；通过主 IPC 读写 Settings Service；应用后只刷新受影响服务；共享角色/主题 | `test_tauri_secondary_windows.py` + Rust windows |
| 角色工作室 | `app/ui/tauri_studio.py`; `tools/studio-tauri/frontend`; `app/config/character_studio.py` | `tests/unit/test_tauri_studio.py`; `tests/unit/test_character_studio.py`; `tests/ui/test_studio.py` | 自动覆盖 | 同一 Tauri App 独立窗口；保存格式和路径校验不变；重复打开聚焦现有实例 | `test_tauri_secondary_windows.py` |
| 历史 | `app/ui/history_window.py`; `app/storage/chat_history.py`; `PetWindow.show_history` | `tests/ui/test_history_window.py`; `test_chat_history_store_*`; `test_reply_history_*` | 自动覆盖 | 分页 DTO 读取；不一次载入全部；tone/portrait 兼容旧记录；发送与工具链继续写现有 store | `test_tauri_secondary_windows.py`; 聊天契约测试 |
| 运行日志与诊断 | `app/ui/log_window.py`; `app/core/runtime_log.py`; `PetWindow.show_runtime_log` | `tests/unit/test_debug_log.py`; `tests/unit/test_gui_log.py`; `tests/unit/test_runtime_events.py` | 自动覆盖 | 诊断窗口显示 Brain、插件、MCP、TTS、资源状态；日志仍脱敏且不写协议 stdout | 次级窗口/Brain Host 契约测试 |
| 托盘与显示/隐藏 | `app/ui/tray_menu.py`; `PetWindow._create_tray_icon`; application activation handler | `test_pet_window_application_activation_restores_when_hidden_to_tray`; tray/topmost tests | 自动覆盖 | 托盘包含显示、隐藏、设置、历史、工作室、退出；重复启动聚焦当前实例 | Rust `tray`/single-instance 测试 + Windows 手工验收 |
| 开机启动 | `app/platforms/launch_at_login.py`; 设置服务 | `tests/unit/test_launch_at_login.py` | 自动覆盖 | Windows 启动项指向 Tauri 主程序而非 `python main.py`；保留启用/禁用与错误提示 | `test_tauri_runtime_events.py` |
| 单实例 | `app/core/instance.py`; `main.py` | `test_main_selfcheck_runs_before_single_instance_guard`; `tests/unit/test_selfcheck.py` | 部分覆盖 | Tauri 主进程拥有单实例；第二次启动只聚焦，不再创建第二个 Brain Host | Rust single-instance 测试 + 手工双开 |
| 退出清理 | `PetWindow.request_quit/close_external_tools`; `app/core/resource_manager.py` | `test_close_external_tools_*`; `test_shutdown_ignores_late_progress_and_reply`; `tests/unit/test_resource_manager.py` | 自动覆盖但实测异常退出 | 停止新请求，再停止 Brain、MCP、插件、TTS/音频和临时资源；返回 0；无残留进程 | Assistant shutdown + Brain supervisor + 手工进程检查 |
| 插件 | `app/plugins/manager.py`; `services.py`; `events.py`; `PetWindow._sync_plugin_chat_ui_widgets` | `tests/unit/test_plugin_system.py`; `test_plugin_services.py`; `test_plugin_advanced.py` | 自动覆盖 | 无 UI/声明式插件继续运行；Qt widget 插件第一阶段明确拒绝并报告不兼容；事件改走 Brain Host | `test_tauri_runtime_events.py` |
| MCP | `app/agent/mcp`; `app/core/bootstrap.py`; `PetWindow.close_mcp_tools` | `tests/unit/test_mcp_runtime.py`; agent/native tool integration tests | 自动覆盖 | MCP 工具和现有配置兼容；Brain 退出时关闭；第一阶段仍由 Python ToolRegistry 执行，不提前实现 Broker | Brain runtime event tests + shutdown tests |
| Sakura Mobile | `plugins/sakura_mobile`; `app/core/mobile_chat_bridge.py`; `MobileChatWorker` | `tests/unit/test_sakura_mobile.py`; `tests/ui/test_sakura_mobile_ui.py` | 自动覆盖 | 无 Qt bridge 提交聊天；与桌面共享忙碌状态、角色和历史；Host 关闭后拒绝请求 | `test_tauri_runtime_events.py`; mobile unit tests |

## 3. 数据兼容与边界

- 第一阶段继续使用现有 `data/`、`characters/`、`plugins/` 格式，不执行破坏性迁移。
- `ChatPipeline`、`AgentRuntime`、Memory Store、Prompt、现有工具执行与确认策略继续复用。
- 第一阶段确认安全边界仅保证：前端不回传执行参数，只发送 Python 端保存的 action ID；不宣称已经具备最终 Permission Manager 或 Capability Broker。
- 不在本阶段实现插件沙箱、Credential Broker、更新体系、Agent Planner 或新的高权限默认能力。
- 旧 Qt 入口在对应 Tauri 功能、自动测试和手工验收完成前保留；生产切换后只作为明确的开发回退构建存在，不自动回退。

## 4. Task 验收记录

| Task | 自动测试 | 手工验收 | 提交 | 备注 |
|---|---|---|---|---|
| 0 | 1456 passed, 3 skipped | 启动资源基线已采集 | `docs: 建立Tauri迁移基线` | Qt 关闭返回 `0xC0000409` |
| 1 | Python 5 passed；Rust 4 passed；fmt/build 通过 | 单屏 100% 下透明、IME、音频 API、截图、穿透、隐藏/恢复、单实例已验证；其余见 ADR-0001 | `feat: 建立Tauri桌面主程序` | Task 6 前补物理拖动、托盘、多 DPI/多屏和人耳音频签字 |
| 2 | Python 17 passed；Rust IPC 6 passed | 不适用 | `feat: 实现Brain Host帧协议` | Python/Rust 共用 golden frame；8 MiB、大端、分片、错误与 session 约束已覆盖 |
| 3 | 指定测试 13 passed；兼容回归 468 passed | `python -m app.brain_host` 实际帧握手/健康/关闭通过 | `feat: 建立无Qt Brain Host` | 真实 AppContext 保留角色/配置；正常 Host 导入图无 PySide6/app.ui；stdout 仅协议帧 |
| 4 | 计划测试 141 passed；扩展回归 222 passed；PetWindow 272 passed | 不适用 | `refactor: 抽离无Qt助手服务` | 单前台交互、协作取消、session action map、标准线程池与无 Qt scheduler；Headless 导入图无 PySide6/app.ui |
| 5 | Rust 监管 6 passed；Rust 全套 16 passed；Python 契约 15 passed；fmt/build 通过 | fake host 已覆盖崩溃、三次退避重启、诊断态、优雅/强制关闭；真实进程故障注入留待最终手工验收 | `feat: 实现Brain Host监管` | 每次启动生成新 session/credential；重启清空旧请求和临时资源；Tauri 退出先停止接收请求 |
| 6 | 指定 Python 6 passed；扩展 Python 49 passed；Rust windows 5 passed；Rust 全套 20 passed；fmt/build 通过 | 单屏 100% 下真实角色立绘、主题、字幕、输入和受控资源 URL 已验证；多 DPI/混合多屏、物理鼠标与托盘仍见 ADR-0001 | `feat: 迁移Tauri桌宠前端` | 单一 Store；JS 布局与 Qt 纯模型逐项等价；资源协议拒绝路径逃逸 |
| 7 | 指定与扩展 Python 41 passed；Rust 全套 21 passed；fmt/build/clippy 通过 | Python/Node/fake Brain 已覆盖发送、进度、最终回复、取消、错误和 action ID 确认；真实在线模型与真实工具留 Task 13 | `feat: 接通Tauri聊天与工具确认` | Brain 请求立即返回；后台事件串行转发；现有 ChatHistoryStore 格式不变；公开 DTO 不泄露 continuation context |
| 8 | 指定与扩展 Python 127 passed；Rust audio 3 passed、全套 23 passed；fmt/build/clippy 通过 | 实机 Tauri 启动后正常退出码 0、无 Brain 残留；自动覆盖无 Qt 合成、受控资源、顺序播放、取消和快速接话；真实设备人耳验收留 Task 13 | `feat: 迁移Tauri语音播放链` | Rust 不向 WebView暴露路径；随机 token、TTL、单次消费；预置/缓存接话音频与即时合成都走同一播放链 |
| 9 | 指定 Python 24 passed；扩展 Python 47 passed；Rust capture 5 passed、全套 27 passed；fmt/build/clippy 通过 | 单屏 100% 的既有 xcap 能力已自动验证；真实框选、125/150/200% DPI 与混合多屏留 Task 13 | `feat: 迁移Tauri截图与主动事件` | 私有路径不进 WebView；Brain 即读即删；Python 保留空闲/冷却批次、视觉消息与摘要；提醒和主动观察统一事件 |
| 10 | 指定 Python 58 passed；扩展 Python 203 passed；Rust 全套 30 passed；四个前端脚本语法、fmt/build 通过 | 同一 `sakura-desktop.exe` 内四窗口可用；设置重复打开只聚焦、应用成功；工作室读取真实角色；历史 50→100 游标分页；诊断状态完整 | `feat: 合并Tauri次级窗口` | 动态 WebView 必须派发到 Tauri 主线程；旧 Qt 独立桥按回退约束留 Task 12，工作室真实写入留 Task 13 验收副本 |
| 11 | 指定 Python 81 passed；扩展 Python 239 passed；Rust 全套 31 passed；fmt/build 通过 | 双开只聚焦既有窗口；设置页显示 2 个插件及声明式字段；Mobile API 返回当前角色；退出无 Tauri/Brain/MCP 残留；物理托盘与真实开机启动留 Task 13 | `feat: 迁移Tauri启动与插件交互` | Brain 装配无 Qt 插件/MCP/Mobile；原生 UI 权限导入前拒绝；`SAKURA_DESKTOP_EXE` 指向 Tauri；混合接话冷启动性能留最终测量 |
| 12 | 完整 Python 1560 passed、3 skipped；生产入口契约 6 passed；Rust 31 passed；fmt/release build 通过 | `runtime/python.exe main.py` 启动 release Tauri，Brain 就绪；所有 Sakura Python 进程 Qt 模块数为 0；Alt+F4 返回 0 且无残留 | `feat: 切换Tauri生产入口` | 旧 Qt 仅保留为 `legacy_qt_main.py` 显式开发回退；基础依赖和正常启动图不含 PySide6 |
| 13 | 完整 Python 1563 passed、3 skipped；desktop Rust 31 passed；settings 6 passed；studio 5 passed；fmt/debug/release build 通过；发布契约 9 passed | 启动/诊断/空闲崩溃恢复/双开/退出/开机启动注册表/工作室副本保存通过；干净机、多 DPI 多屏、物理托盘与人耳音频受限 | `chore: 完成Tauri迁移发布验收` | 安装与打包链已携带生产桌面程序；受限项不标记为通过 |
| 14 | 完整 Python 1571 passed、3 skipped；desktop Rust 37 passed；17 个前端脚本语法、fmt/clippy/release build 通过 | 隔离 ready、首次设置、Runtime 修复、双开、Brain 恢复和退出清理通过；同会话首次设置真实点击与修复页诊断点击受限 | `fix: 收口Tauri启动状态机门禁` | Node 24 与临时路径环境阻断已消除；启动路由按 session generation 持续评估且重复事件幂等 |

## 5. Task 13 逐项签字

状态：`通过` 表示自动测试和所需实机证据均具备；`部分通过` 表示主要契约通过但仍缺指定人工/硬件场景；`受限` 表示当前机器无法提供所需条件。

| 能力 | 签字 | Task 13 证据或未验证风险 |
|---|---|---|
| 透明、无边框、桌宠窗口 | 通过 | 当前单屏 100% DPI 实机无黑边，受控 WebView 资源契约通过 |
| 始终置顶与临时抑制 | 部分通过 | Rust/前端与次级窗口自动测试通过，缺物理窗口层级切换签字 |
| 拖动 | 部分通过 | 布局与输入契约通过，缺物理鼠标拖动签字 |
| 多显示器与 DPI | 受限 | 当前只有单屏 100% DPI；125/150/200% 与混合 DPI 多屏未验收 |
| 中文/日文输入法 | 通过 | Task 1 当前 Windows 桌面已验收组合输入 |
| 立绘加载与表情切换 | 通过 | 真实 N.A.V.I. 资源、主题与受控 asset URL 已验收 |
| 字幕、分段与打字机 | 部分通过 | 初始字幕实机正常，完整多段回复由前端/契约测试覆盖，未调用在线模型复验 |
| 输入栏、发送与忙碌态 | 部分通过 | IME 与输入 UI 实机正常，真实在线聊天未执行 |
| 取消、迟到结果与错误 | 部分通过 | Python/Brain/Rust 契约通过，真实在线模型取消未执行 |
| 工具确认与拒绝 | 部分通过 | action ID 与 continuation 自动测试通过，真实在线工具确认链未执行 |
| TTS 合成 | 部分通过 | 无 Qt 合成与 Provider 契约通过，未做真实设备人耳签字 |
| 音频播放 | 受限 | Rust 顺序/停止/TTL 测试通过，当前自动化无法判断实际听感 |
| 快速接话 | 部分通过 | 选择、缓存、取消和统一播放链通过，缺真实音频重叠签字 |
| 手动框选截图 | 部分通过 | 单屏捕获与坐标契约通过，混合 DPI 物理框选受限 |
| 自动屏幕观察 | 部分通过 | 调度、私有资源与视觉 DTO 通过，混合多屏真实观察受限 |
| 主动互动 | 部分通过 | 统一事件和忙碌仲裁测试通过，未执行真实在线模型并发场景 |
| 提醒 | 通过 | 无 Qt scheduler、单次消费和退出清理测试通过 |
| 设置 | 通过 | 同一 Tauri App 打开、应用和插件声明式设置实机通过 |
| 角色工作室 | 通过 | 隔离副本真实保存成功，原角色包未修改，临时副本已删除 |
| 历史 | 通过 | 实机从 50 条游标分页到 100 条且主窗口不阻塞 |
| 运行日志与诊断 | 通过 | Brain 缺资源三次失败后 Tauri 保持诊断模式 |
| 托盘与显示/隐藏 | 部分通过 | 菜单项、路由和隐藏/恢复测试通过；物理系统托盘壳层无法自动定位 |
| 开机启动 | 通过 | 真实 HKCU Run 启用/禁用成功并指向 `sakura-desktop.exe`，原值已恢复 |
| 单实例 | 通过 | 实机双开只聚焦已有窗口，不增加 Brain Host |
| 退出清理 | 通过 | 多次 Alt+F4 返回 0，无 Tauri、Brain、MCP 或 WebView 残留 |
| 插件 | 通过 | 两个声明式插件实机可见；原生 Qt UI capability 在导入前拒绝 |
| MCP | 通过 | 真实 Web Search MCP 随 Brain 创建并在退出后清理 |
| Sakura Mobile | 通过 | 本机 API 返回当前 N.A.V.I.，共享 Brain 状态 |

发布前仍必须在干净 Windows x64、多 DPI 多屏、物理托盘和真实音频设备上补齐“受限/部分通过”项；在这些签字完成前，不删除 `legacy_qt_main.py` 与旧 Qt 回退测试。

## 6. Gate A 状态机签字

| 状态或行为 | 自动证据 | 实机证据 | 当前结论 |
|---|---|---|---|
| `ready` 只路由桌宠 | Rust Bootstrap 路由和非 ready 禁止构建桌宠 DTO | 隔离 ready 仅显示桌宠业务窗 | 通过 |
| `onboarding_required` 健康且不重启 | Python health、Rust 单 Host/零重启测试 | 隔离空配置显示首次设置，Brain 数量为 1 | 通过 |
| 同会话首次设置完成后进入桌宠 | Python 同进程上下文重建、Rust 重新评估、前端 Bootstrap 替换测试 | Windows UI Automation 无法访问 WebView 按钮，未完成真实提交 | 自动通过、实机受限 |
| `runtime_repair` 独立呈现 | Runtime 缺失与协议不兼容进入 Diagnostic；诊断 DTO 可脱离 Brain 返回 | 缺 Runtime 隔离目录显示修复页 | 通过；诊断按钮物理点击部分通过 |
| `brain_recovering` 有限恢复 | Rust 退避、阈值、请求/资源失效和新 generation 测试 | 强杀 Brain 后 UI 保留并建立新 PID | 通过 |
| 状态和窗口幂等 | 同 generation 重复 ready、重复 diagnostic 不重复 present；窗口标签复用 | 双开保持 1 个 desktop 和 1 个 Brain | 通过 |
