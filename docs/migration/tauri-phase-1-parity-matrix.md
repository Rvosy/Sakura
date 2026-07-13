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

### 1.3 手工基线限制

- 中文/日文 IME、透明窗口黑边、混合 DPI、多显示器拖动、真实音频播放和真实截图必须在交互式 Windows 桌面验收，pytest 只能覆盖结构和部分行为。
- “首帧”当前没有应用内打点；Task 1 起应在 Tauri `setup`、主窗口 `DOMContentLoaded`/首个渲染完成处增加可比较时间点。
- 性能对比必须使用同一机器、同一角色、同一插件/MCP/TTS 配置，并分别记录首次冷启动和第二次热启动。

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
| 5 | 待执行 | Brain 崩溃恢复待验收 | 待提交 | Host 监管 |
| 6 | 待执行 | 桌宠视觉待验收 | 待提交 | 前端状态/立绘/字幕/输入 |
| 7 | 待执行 | 聊天与确认待验收 | 待提交 | 聊天契约 |
| 8 | 待执行 | 真实音频待验收 | 待提交 | 合成/播放/快速接话 |
| 9 | 待执行 | 多屏截图待验收 | 待提交 | 观察与主动事件 |
| 10 | 待执行 | 次级窗口待验收 | 待提交 | 设置/工作室/历史/诊断 |
| 11 | 待执行 | 托盘/双开待验收 | 待提交 | 启动与插件交互 |
| 12 | 待执行 | 生产入口待验收 | 待提交 | PySide6 导入图必须为空 |
| 13 | 待执行 | 干净 Windows x64 待验收 | 待提交 | 最终发布验收 |
