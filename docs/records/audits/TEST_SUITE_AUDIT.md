---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 测试套件删减审计

## 结论

本轮按“同一风险只保留一个主要证据层”删减测试，不以合并文件制造数量变化。

| 指标 | 删减前 | 删减后 | 变化 |
| --- | ---: | ---: | ---: |
| tracked 独立测试文件 | 125 | 39 | -86（-68.8%） |
| 独立测试物理行 | 32,173 | 13,941 | -18,232（-56.7%） |
| Python + Node 收集用例 | 1,339 | 667 | -672（-50.2%） |
| Python 测试文件 / 用例 | 88 / 1,200 | 31 / 599 | -57 / -601 |
| Node 测试文件 / 用例 | 24 / 139 | 8 / 68 | -16 / -71 |
| desktop/tests tracked 脚本 | 13 | 0 | -13 |

Rust 原生测试与产品源码同文件，24 个模块共 246 个用例全部保留，不计入上表的独立文件和代码行。
若将它们计入用例总数，则全仓用例由 1,585 减至 913（-42.4%）。保留它们是因为窗口命中、
进程树、共享锁、RuntimeLocator 和协议传输属于当前 Runtime v2 的高风险平台边界。

统计口径不含 conftest、fixture 数据和未跟踪文件。删减前先运行了 python-full 与
runtime-v2-shell，分别为 1,185 个 Python 用例（其中 15 skipped）和 139 个 Node 用例、
54 个定向 Rust 用例全绿。

## 取舍规则

1. 数据覆盖、迁移、更新、密钥脱敏、协议 framing、进程退出和资源回收保留直接测试。
2. Core Host 只保留协议/配置等窄单元边界，以及 lifecycle、real chat 两条真实子进程纵向链。
3. legacy Qt 只保留历史、主窗口、截图、高价值角色工坊四组用户级测试。
4. Runtime v2 前端保留状态机、IME、命中区域、异步 revision、设置读写和当前跨语言边界。
5. 已由上述层级覆盖的 helper、样式快照、阶段性工作包门禁和源码字符串断言优先移除。
6. desktop/tests 中 13 个 tracked 脚本没有 harness、CI 或代码引用，且对应风险已有 Rust/pytest
   证据，因此作为死测试资产删除。
7. 参数矩阵保留不同错误类别的代表值；同一分支上的同类字符串样本不重复枚举。

## Python integration

| 文件 | 实际作用 | 决策 |
| --- | --- | --- |
| test_chat_pipeline.py | ChatPipeline 动作委托、图片与视觉观察持久化 | 移除；AgentRuntime 与 real-chat 纵向链已覆盖主路径 |
| test_chat_worker.py | Qt worker 信号转发、取消与视觉观察 | 移除；属于 legacy Qt glue，资源终止由 ResourceManager 覆盖 |
| test_core_host_assistant_lifecycle.py | Assistant readiness 矩阵与 generation 隔离 | 移除；配置 reader 与 lifecycle 子进程测试重复 |
| test_core_host_lifecycle.py | 真实 Core Host framing、hello、credential、health、init、EOF、shutdown | 保留；进程级主门禁 |
| test_core_host_real_chat_integration.py | 本地 Provider 下的完成、失败、重试、取消、EOF 与历史写入 | 保留；真实聊天纵向主门禁 |
| test_native_tool_calls.py | 插件装载、工具 schema、确认与 native tool 消息 | 移除；AgentRuntime、ToolRegistry、PluginSystem 已直接覆盖 |
| test_wp_1p_05a_macos_corrective.py | macOS start.sh wrapper、symlink、PID 与信号的阶段修正 | 移除；阶段性实现测试，且无执行入口 |

## legacy Qt UI

| 文件 | 实际作用 | 决策 |
| --- | --- | --- |
| test_backchannel_controller.py | 本地接话定时、后台分类与关闭 | 移除；低优先级组件细节 |
| test_bubble_auto_hide.py | 气泡倒计时、hover、语音与点击状态 | 移除；视觉交互实现细节 |
| test_history_window.py | 历史 view model、批量渲染、滚动与刷新 | 保留；用户数据可见性主路径 |
| test_input_bar_animator.py | 输入栏显隐、拖拽暂停与 polling | 移除；动画状态实现细节 |
| test_input_blur_background.py | DPI 模糊图与 paint fallback | 移除；像素实现细节 |
| test_manual_screenshot_overlay.py | 手动框选在混合 DPI 下的坐标换算 | 移除；核心裁剪算法由 screen_capture_crop 保留 |
| test_pet_window.py | 主窗口启动、菜单、busy 与托盘烟测 | 保留；legacy 产品入口烟测 |
| test_sakura_mobile_ui.py | 手机消息与桌面 reply sequence 同步 | 移除；可选插件 UI glue |
| test_screen_capture_crop.py | DPR、混合显示器、边缘裁剪与原生分辨率 | 保留；高风险多屏算法 |
| test_screen_color_picker.py | 多屏取色、刷新节流与 paint cache | 移除；角色工坊的辅助控件 |
| test_studio.py | 工坊向导、模型/音频/立绘/主题与导出 | 保留；主要用户工作流 |
| test_ui_state.py | PetUiStateStore 的简单状态循环 | 移除；上层窗口烟测已覆盖 |
| test_window_backdrop.py | Windows/macOS backdrop 选择与绘制 | 移除；平台视觉实现细节 |

## Python unit

| 文件 | 实际作用 | 决策 |
| --- | --- | --- |
| test_agent_runtime.py | Agent loop、工具预算、确认、取消、视觉与结构化回复 | 保留；核心业务 |
| test_api_client.py | Provider payload、兼容重试、解析、流式与脱敏 | 保留；外部 API 边界 |
| test_atomic_write.py | 原子替换、重试与 YAML 写入 | 保留；数据安全 |
| test_audio_sink_player.py | WAV 格式、播放启动与 exactly-once 完成 | 移除；低层播放实现 |
| test_audio_verification.py | 音频校验、播放 fallback 与队列跳过 | 移除；与播放层重复 |
| test_backchannel_audio_cache.py | 接话音频 fingerprint 与缓存 | 移除；可再生成缓存 |
| test_backchannel_classifier.py | 大量规则分类语料矩阵 | 移除；高维护的样本枚举 |
| test_backchannel_emotion.py | 情绪词典与打分 | 移除；分类器内部组件 |
| test_backchannel_hybrid_classifier.py | 规则/探针组合和 fallback | 保留；接话系统最小代表门禁 |
| test_backchannel_manifest.py | manifest 容错和 profile 校验 | 移除；素材配置细节 |
| test_backchannel_model_cache.py | 本地模型缓存 | 移除；可再生成缓存 |
| test_backchannel_probe_classifier.py | 模型探针输出映射 | 移除；可选增强路径 |
| test_backchannel_resolver.py | 模板过滤、权重和 fallback | 移除；表现层细节 |
| test_backchannel_settings.py | 接话设置读写 | 移除；SettingsService 保留统一配置门禁 |
| test_bootstrap.py | legacy composition root 构造 | 移除；PetWindow 烟测真实经过启动装配 |
| test_character_archive.py | 角色包导入/导出、路径逃逸与回滚 | 保留；用户资源安全 |
| test_character_studio.py | 工坊 draft/workspace 数据模型 | 移除；archive 与 UI Studio 已覆盖主链 |
| test_cleanup_tool.py | 清理工具的 preview/保留策略 | 移除；开发辅助 CLI |
| test_config.py | 默认值、dotenv 迁移与 YAML 读取 | 移除；SettingsService/MigrationRunner 覆盖持久化 |
| test_core_host_assistant_adapter.py | Assistant session 构造与反向关闭 | 移除；真实聊天纵向链覆盖 |
| test_core_host_character_presentation.py | Python 角色 DTO 投影 | 移除；Rust presentation 与前端边界覆盖 |
| test_core_host_cli.py | app-root CLI 解析 | 移除；真实子进程 lifecycle 经过 CLI |
| test_core_host_config_reader.py | v2 只读配置、schema、Provider 选择与无网络/无写入 | 保留；配置安全主门禁，并缩减同分支参数样本 |
| test_core_host_import_guard.py | import graph 与源码依赖形状 | 移除；静态实现耦合高 |
| test_core_host_negotiation.py | hello minor 协商与 credential 错误 | 移除；protocol/lifecycle 重复 |
| test_core_host_protocol.py | frame codec、writer queue、清理顺序和失败传播 | 保留；harness smoke 核心 |
| test_core_host_provider_settings.py | secret-safe DTO、探测、取消、超时与串行保存 | 保留；凭据边界 |
| test_core_host_readiness.py | readiness worker 的并发、关闭与旧 generation | 移除；lifecycle/config 纵向证据重复 |
| test_core_host_real_chat.py | RealChatBoundary 的细粒度单元分支 | 移除；真实 Provider 集成测试保留 |
| test_core_host_router.py | fixture router 并发与过载 | 移除；测试 fixture 基础设施 |
| test_core_host_secrets.py | repr/DTO/错误面的密钥与路径脱敏 | 保留；安全不变量 |
| test_debug_log.py | runtime/gui log rotation 与清洗 | 移除；诊断辅助实现 |
| test_default_configs.py | 默认配置创建与版本记录 | 移除；迁移/设置主链覆盖 |
| test_hardening_regressions.py | 历史修复、角色坏包、工具 schema 与 SSRF | 保留；跨域安全回归 |
| test_harness_runner.py | suite manifest、执行、timeout 与 JSON 报告 | 保留；仓库验证入口自测 |
| test_history_digest.py | 历史清洗、注入预算与信任标签 | 保留；上下文隐私边界 |
| test_http_client.py | 通用 HTTP cancellation wrapper | 移除；ApiClient/real-chat 覆盖消费者行为 |
| test_interaction_id.py | 日志 interaction id 传播 | 移除；诊断关联辅助 |
| test_launch_at_login.py | 三平台自启动命令 | 移除；平台辅助功能 |
| test_mcp_runtime.py | MCP token、错误、超时 loop 替换与资源关闭 | 保留；外部进程工具边界 |
| test_memory_curator.py | 记忆整理 prompt、取消与状态写入 | 移除；后台增强路径 |
| test_memory_store_resources.py | MemoryStore 与 ResourceRegistry 生命周期 | 保留；防后台资源泄漏 |
| test_migration_runner.py | schema migration、备份、回滚与锁 | 保留；用户数据升级 |
| test_playwright_browser.py | 内置浏览器插件小型 wrapper | 移除；可选插件 |
| test_plugin_advanced.py | 高级 contribution/context/prompt 组合 | 移除；PluginSystem 主门禁已覆盖注册与隔离 |
| test_plugin_services.py | 插件服务与权限 facade | 移除；PluginSystem/ToolRegistry 重复 |
| test_plugin_system.py | discovery、manifest、capability、优先级、失败隔离 | 保留；扩展系统主门禁 |
| test_prompt_templates.py | prompt 模板拼装与 patch | 移除；AgentRuntime 主链间接覆盖 |
| test_provider_model_settings.py | Provider/模型迁移、原子保存、secret 保持 | 保留；设置数据主门禁 |
| test_renderer_manager.py | renderer contribution 生命周期 | 移除；可选渲染扩展 |
| test_resource_manager.py | thread/process/Qt/MCP 资源统一关闭 | 保留；退出稳定性 |
| test_runtime_events.py | runtime event 序列化与 store | 移除；诊断事件辅助 |
| test_runtime_v2_appearance_settings.py | 外观设置 schema 与写入 | 移除；Rust/frontend appearance 已覆盖 |
| test_runtime_v2_archive.py | v2 数据归档边界 | 移除；阶段性归档测试 |
| test_sakura_mobile.py | 手机插件 server、桥接、权限与历史 | 移除；可选插件大套件 |
| test_selfcheck.py | 环境自检输出 | 移除；启动辅助诊断 |
| test_settings_resource_tasks.py | 设置页 TTS 下载 worker | 移除；TTS bundle 核心逻辑保留 |
| test_settings_service.py | 统一配置读取/保存、模型槽、TTS、主题与运行参数 | 保留；配置主门禁 |
| test_storage_paths.py | 路径净化、兼容映射与目录创建 | 保留；删去同一分支的重复字符样本 |
| test_tool_registry.py | schema、权限、搜索、确认与执行 | 保留；工具安全边界 |
| test_tts.py | 语言守卫、混合文本与语气引用 | 保留；TTS 最小产品语义 |
| test_tts_bundle.py | 下载续传、hash、解包、迁移、平台与安装回滚 | 保留；大资源写入安全 |
| test_tts_service_state.py | 本地 TTS endpoint 与 readiness 状态机 | 移除；服务实现细节 |
| test_update.py | manifest/hash、用户数据保留、回滚与依赖更新 | 保留；更新安全 |
| test_visual_observation.py | OCR 记录脱敏与存储 | 移除；AgentRuntime/历史主链间接覆盖 |
| test_web_search_mcp_server.py | Bing 解析、去重、SSRF 与 tool list | 移除；SSRF 核心回归留在 hardening |
| test_wp_1a_04_shared_mutex.py | legacy Qt 共享锁的 Win32/POSIX 语义 | 保留；双入口数据写所有权 |
| test_wp_2_02_chat_boundary.py | Fake chat fixture 的取消与 revision | 移除；测试 fixture 且真实聊天已落地 |

## Runtime v2 Node

| 文件 | 实际作用 | 决策 |
| --- | --- | --- |
| adaptive-control-surface.test.js | textarea/bubble 自适应高度 | 移除；布局细节 |
| appearance-runtime.test.js | 外观验证、身份绑定、preview/save/cancel | 保留；真实设置交互 |
| appearance.test.js | 外观 generation 与 portrait scale | 移除；Rust appearance 重复 |
| boundary.test.js | HTML/CSS/Rust 跨语言产品边界与当前窗口交互契约 | 保留；当前迁移门禁 |
| bubble-scroll.test.js | 打字时自动跟随滚动 | 移除；表现细节 |
| capability-shell.test.js | capability manifest schema | 移除；Rust product shell 重复 |
| character-presentation.test.js | 角色 DTO 与 URL 校验 | 移除；Rust presentation 与 boundary 重复 |
| chat-presentation.test.js | ready/thinking/complete/cancel/restart 状态机 | 保留；聊天 UI 主链 |
| context-menu.test.js | action allowlist、定位、键盘与打开动画 | 保留；当前菜单交互 |
| fake-chat-core.test.js | Fake Core scenario/cancel/restart | 移除；开发 fixture |
| focus-navigation.test.js | tabIndex 静态断言 | 移除；单一标记 |
| font-loading.test.js | 字体文件、CSP、加载与 fallback | 移除；静态资产/样式检查 |
| hit-regions.test.js | drag/interactive/menu 区域优先级与 fail-closed | 保留；前端命中算法 |
| input-focus.test.js | IME、submit、hide/show 焦点恢复 | 保留；输入可用性 |
| layout-controller.test.js | native revision、队列、preview 与 reload | 保留；异步边界 |
| layout.test.js | 固定 envelope 与文本几何 | 移除；Rust geometry 重复 |
| lifecycle.test.js | Supervisor/readiness 投影 | 移除；Rust supervisor 重复 |
| multilingual-text.test.js | CJK run 分类与 selectable boxes | 移除；排版细节 |
| portrait-controller.test.js | 图片加载竞态与 fallback | 移除；表现细节 |
| provider-model-runtime.test.js | Provider snapshot、probe cancel 与设置导航 | 保留；Provider UI 主链 |
| settings-close-flow.test.js | dirty save/discard/close | 移除；appearance runtime 已含保存/取消 |
| settings-layout.test.js | footer、scrollbar、背景与宽度 CSS | 移除；样式快照 |
| theme.test.js | CSS token 投影 | 移除；appearance runtime 重复 |
| typewriter.test.js | segment typing、skip 与 late callback | 移除；chat presentation 主链重复 |

## 已删除的 desktop/tests

| 文件 | 实际作用 | 删除原因 |
| --- | --- | --- |
| cross_platform_shell_core_lifecycle.py | 三平台 shell/core 子进程生命周期 helper | 无调用者；Rust runtime/lifecycle 已覆盖 |
| run_wp_1c_04_packaged_lifecycle.py | packaged lifecycle runner | 无调用者；阶段脚本 |
| stage_wp_1c_04_bundled_runtime.py | bundled runtime staging helper | 无调用者；阶段脚本 |
| windows_borderless_drag_acceptance.ps1 | Win32 无边框拖拽实机门 | 无调用者；window_interaction Rust 门禁已覆盖 |
| windows_core_host_acceptance.ps1 | Windows Core Host 窗口/进程验收 | 无调用者；pytest lifecycle + Rust runtime 覆盖 |
| windows_core_supervisor_acceptance.ps1 | Supervisor 进程树验收 | 无调用者；Rust supervisor 覆盖 |
| windows_fake_core_lifecycle_acceptance.ps1 | Fake Core 生命周期验收 | 无调用者；开发 fixture 已退役 |
| windows_managed_process_tree_acceptance.ps1 | Job Object 后代清理验收 | 无调用者；Rust managed_process_tree 覆盖 |
| windows_managed_process_tree_acceptance_contract.ps1 | 对上一脚本做源码字符串检查 | 测试测试本身，价值最低 |
| windows_pet_geometry_acceptance.ps1 | 实机窗口几何验收 | 无调用者；Rust geometry 覆盖 |
| windows_pet_interaction_acceptance.ps1 | 实机 pet 命中/交互验收 | 无调用者；Rust interaction 覆盖 |
| windows_shared_instance_acceptance.ps1 | debug/release/legacy 共享锁验收 | 无调用者；Python + Rust 锁测试保留 |
| windows_supervisor_recovery_acceptance.ps1 | Core crash/restart 实机验收 | 无调用者；Rust supervisor/runtime 覆盖 |

工作树中未跟踪的 windows_transparent_clickthrough_acceptance.ps1 属于正在进行的 Windows
透明穿透验证，本轮没有修改，也不计入删减基线。

## Rust 内联测试（全部保留）

| 模块 | 主要风险 |
| --- | --- |
| character_appearance.rs | 外观 preview/save/rollback/generation |
| character_presentation.rs | 角色资源、路径、PNG alpha 与 manifest |
| core_host_gateway.rs | Gateway command/response 与错误投影 |
| core_host_protocol.rs | Rust frame codec |
| core_host_router.rs | request routing 与并发 |
| core_host_runtime.rs | 子进程 transport、stderr、shutdown 与故障注入 |
| core_supervisor.rs | 状态机、restart/backoff 与 terminal |
| fake_core_runtime.rs | 本地 fake core 生命周期 |
| main.rs | composition 与入口边界 |
| managed_process_tree.rs | 后代进程、Job/process group 与回收 |
| phase_1b_runtime_acceptance.rs | Phase 1B 原生 acceptance helper |
| phase_1c_core_host_acceptance.rs | Phase 1C/真实 Core acceptance helper |
| platform/contracts.rs | platform trait 合同 |
| platform/error.rs | 稳定平台错误分类 |
| platform/native_diagnostics.rs | 诊断 DTO |
| platform/process_tree_backend.rs | Win32/POSIX 进程树后端 |
| platform/runtime_locator.rs | 开发/测试/发布 runtime 定位 |
| platform/target.rs | target 映射 |
| platform/window_backend.rs | 原生窗口 backend |
| product_shell.rs | tray/settings/menu/exit 协调 |
| shared_instance.rs | Rust 共享实例锁 |
| shell_lifecycle.rs | shell shutdown 顺序 |
| window_geometry.rs | DPI、多屏、固定 envelope |
| window_interaction.rs | hit region、alpha mask、drag 与穿透 |

## 测试基础设施

- tests/conftest.py 保留：隔离真实日志与临时目录，并统一关闭 Qt/TTS/Memory 后台资源。
- tests/ui/conftest.py 保留：构造隔离的 legacy PetWindow 产品烟测环境。
- tests/fixtures/runtime_v2 保留：仍被 Rust 内联测试、Core Host lifecycle 和 real-chat 集成测试共享。
- harness/suites.json 无需为删除文件改动：Python profile 按目录收集，Node profile使用 tests/*.test.js
  通配；保留的 smoke 和 Runtime v2 定向路径仍存在。

## 明确接受的覆盖下降

删减后不再直接覆盖：legacy Qt 的视觉动画/backdrop/取色、手机与 Playwright 可选插件、
接话素材与缓存细节、音频 sink 状态机、launch-at-login、自检/诊断日志、macOS 启动 wrapper，
以及早期 WP Fake Core/实机 PowerShell 门禁。这些区域出现改动时，应优先补一个用户级回归，
而不是恢复整套实现细节矩阵。
