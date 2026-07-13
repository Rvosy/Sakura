# PetWindow 删除优先的状态收敛设计

- 日期：2026-07-11
- 状态：已批准
- 范围：`PetWindow` 及其直接依赖的内部兼容层

> **2026-07-12 Tauri 集成说明：** 新版 `dev` 已使用 Tauri 设置页取代 Qt `SettingsDialog`。本设计中的设置同步约束继续有效，但实现落点为 `app/ui/tauri_settings.py`、`tools/settings-tauri/` 与 `PetWindow` 的 Tauri 应用流程；不得恢复已删除的旧 Qt 设置文件。

## 1. 背景

Sakura 在持续开发中多次引入新状态源、新命名和新控制器，但旧实现没有同步退出生产代码。当前主要迹象包括：

- `app/ui/pet_window.py` 约 7300 行，承担 UI、交互编排、主动事件、记忆整理、设置同步和资源关闭等职责。
- 文件中存在约 359 处 `getattr`，其中一部分只为半初始化测试桩服务，使生产代码容忍本应非法的对象状态。
- `proactive_care` 重命名为 `screen_awareness` 后，新旧流程仍同时存在。生产定时器使用新流程，旧流程主要由旧测试和内部别名维持。
- `PetUiStateStore` 已被定义为 UI 状态唯一来源，但 `reply_waiting_ui_active` 等镜像状态仍在并行维护。
- 主动事件同时维护 `active_event`、`active_event_type`、`active_reminder_id` 和 `active_reminder_text`，清理和异常路径容易发生状态漂移。
- 记忆整理通过多个独立字段记录同一轮任务上下文。近期修复已经暴露角色切换时字段快照不完整的问题。

测试基线为：

- `tests/unit`：789 passed，3 skipped。
- `tests/integration`：144 passed。
- 当前 runtime 缺少 `pytest-qt`、`pytest-env`、`pytest-timeout`，导致 pytest 对 `qt_api`、`env` 和超时配置发出 unknown-option 警告。UI 测试结果在补齐开发依赖前不能作为完整证据。

## 2. 已确认决策

1. 宏观程序逻辑、功能入口、界面表现和正常用户流程保持不变。
2. 保留用户数据以及文档公开的插件 SDK。
3. 旧配置通过一次性版本迁移兼容；日常读取不再长期携带旧格式回退。
4. 测试桩调用方式、内部旧命名和未公开兼容层不属于兼容承诺。
5. 净删减按 `app/` 与 `plugins/` 的生产代码统计；测试和文档不计入该指标。
6. 采用“删除优先的状态收敛”，不进行大规模 Controller 重写，也不为了分层而平移代码。

## 3. 目标与非目标

### 3.1 目标

- 删除生产链之外的旧 `proactive_care` 实现和内部别名。
- 让每类运行状态只有一个权威来源，删除镜像字段和同步分支。
- 删除只为半初始化测试对象存在的生产容错。
- 保持现有信号顺序、QTimer 间隔、线程启动顺序、字幕/TTS 顺序和用户可见行为。
- 使 `app/` 与 `plugins/` 的总删除行数大于新增行数，预计净删 400–650 行。

### 3.2 非目标

以下已发现问题不进入本轮，分别留给后续规格：

- 工具参数校验、未知事件和插件空操作等边界真实性问题。
- Agent 原生工具、伪 tool-call 和文本总结的多协议收敛。
- TTS AudioSink 与 QMediaPlayer 双后端清理。
- ResourceManager、Qt/native 退出竞态和生命周期重构。
- 已无生产调用的独立视觉摘要链删除。
- 日志配置新旧双路径清理。
- `PetWindow` 全面拆分为多个新 Controller。

## 4. 架构边界

`PetWindow` 继续作为 QWidget、依赖装配点和 UI 业务编排入口。现有 `SubtitleController`、`BackchannelController`、`PortraitController`、`InputBarAnimator`、`BubbleAutoHideController` 和 `ResourceManager` 继续承担原职责。

本轮不新增通用状态机框架。只允许引入一个小型、不可变的记忆整理运行上下文；它必须通过删除多个镜像字段取得净删减。

状态权威如下：

| 状态域 | 唯一来源 | 删除的镜像状态 |
|---|---|---|
| UI 展示阶段 | `PetUiStateStore.state` | 无；继续表示 thinking/streaming/speaking/error |
| 主聊天/事件 worker 忙碌 | `worker_thread` / `worker`，由 `ResourceManager` 管理 | `reply_waiting_ui_active`、测试专用忙碌回调查找 |
| 待确认工具动作 | `pending_tool_action` | 无 |
| 当前主动事件 | `active_event` | `active_event_type`、`active_reminder_id`、`active_reminder_text` |
| 当前记忆整理任务 | 不可变 `_MemoryCurationRunContext` | mode、character_id、target_history_count、consumed_turns 四个字段 |
| 屏幕感知批次 | 现有 `screen_awareness_*` 字段和唯一新流程 | 全部 `proactive_*` 镜像字段与方法 |

## 5. 组件设计

### 5.1 屏幕感知单路径

保留当前 `screen_awareness` 生产流程，删除完整旧流程：

- 删除 `LEGACY_PROACTIVE_EVENT_TYPE` 和 `PROACTIVE_*` 常量别名。
- 删除 `PetWindow` 的 `proactive_care_settings`、`proactive_care_timer`、`last_proactive_*` 等属性别名。
- 删除 `_check_proactive_care`、`_can_run_proactive_care`、旧截图批次、旧事件构建器和旧定时器同步方法。
- `_is_screen_awareness_event_type` 只识别 `screen_awareness_check`。
- 删除 `app/agent/proactive_care.py` 兼容导入模块。
- `AppContext`、`SettingsDialog` 和 `AppSettingsService` 只暴露 `screen_awareness` 命名。
- 测试和内部导入统一迁移到 `app.agent.screen_awareness`。

配置兼容由一个新的版本化迁移完成：

1. 仅存在 `proactive_care` 时，将其规范化后写入 `screen_awareness`。
2. 新旧配置同时存在时，以 `screen_awareness` 为准并删除旧节。
3. 迁移通过原子写和备份完成；成功后才推进配置版本。
4. 旧节结构无效且没有可用新节时，迁移明确失败并保留原文件。
5. 正常设置加载不再回退读取 `proactive_care`。

历史 `.env` 键到新配置的版本化导入可继续留在迁移层，因为它属于用户数据兼容，不属于运行时双路径。

### 5.2 UI 等待状态

`PetUiStateStore` 继续表示 thinking/streaming/speaking/error 展示阶段，并继续驱动 speaking watchdog；它不承担 worker busy 状态。

`_set_busy()` 是 worker 忙碌变化的唯一 UI 入口，同时完成：

- 按钮、截图按钮和确认面板的可用性切换。
- “正在思考”占位文字和 `replyWaiting` 动态属性切换。
- 进入或离开等待态时的空输入焦点释放。

等待态的上一值从输入控件已有的 `replyWaiting` 动态属性读取，不再保存单独业务字段。启动初始化状态继续由现有启动逻辑显式覆盖。

删除：

- `reply_waiting_ui_active` 字段。
- 等待 UI 同步中对半初始化对象的 `hasattr/getattr` 路径。
- `_set_busy` 对内部方法的动态查找。
- 依赖镜像布尔值的点击逻辑，改为读取实际 worker 忙碌状态。

焦点释放规则保持不变：进入等待态时，或从等待态离开且输入框为空时，释放输入焦点；输入框已有下一条文本时保留焦点。

### 5.3 主动事件上下文

`active_event: AgentEvent | None` 成为唯一事件状态。

- `_run_event_worker` 不再单独接收 `reminder_id`；提醒事件已经在 payload 的 `id` 字段携带该信息。
- 事件类型读取 `active_event.type`。
- 提醒文本读取 `active_event.payload["text"]`。
- 提醒 ID 读取 `active_event.payload["id"]`。
- 忙碌判断统一使用 `active_event is not None`。
- 完成和失败处理器先保存局部 `event` 引用，再清空 `active_event`，随后继续过滤回复、标记提醒或生成错误回复。
- `_clear_active_event` 只需将 `active_event` 置空。
- 屏幕观察 follow-up 队列复用事件 payload 中的提醒 ID，删除 `pending_screen_observation_event_reminder_id`。

这保持现有提醒完成时机、主动感知静默结束规则和错误回复内容不变，同时删除四个字段的同步维护。

### 5.4 记忆整理运行上下文

引入私有不可变数据类 `_MemoryCurationRunContext`，字段为：

- `mode`
- `character_id`
- `target_history_count`
- `consumed_turns`

`PetWindow` 只保留 `memory_curation_run: _MemoryCurationRunContext | None`。

- 启动任务时一次性创建上下文，并用其中的 `character_id` 创建 scoped memory store。
- 完成、失败和清理处理器读取同一上下文。
- 当前角色与上下文角色不一致时，继续跳过进度写入或 pending turns 消费。
- 回调到达但上下文为空时，记录明确的内部状态错误并停止后续写入，不使用空字符串模拟正常状态。
- worker 清理时将运行上下文置空。

### 5.5 必需组件与可选边界

构造完成后必然存在的内部对象使用直接访问，包括：

- `ui_state`
- `subtitle_controller`
- `input_edit`、`send_button`、`screenshot_button`
- `_set_reply_waiting_ui` 的替代内部同步方法
- `screen_awareness_settings`
- 当前记忆整理运行上下文的正常路径

继续保留防御性处理的边界包括：

- 可选插件贡献和插件 hook。
- 可选渲染器。
- 平台相关 Qt/原生能力。
- 退出阶段可能已被 Qt 销毁的对象。
- 明确允许为空的可选 TTS、MCP 或外部服务。

判断标准不是“异常会不会发生”，而是“该依赖在当前生命周期阶段是否按契约允许缺失”。

## 6. 状态与数据流

| 流程 | 状态变化 | 保持不变的行为 |
|---|---|---|
| 用户发送消息 | `IDLE/ERROR → THINKING → STREAMING? → SPEAKING → IDLE` | 等待动画、进度回复、分段字幕和 TTS 顺序不变 |
| 请求失败 | `THINKING/STREAMING → ERROR` | 错误提示、历史记录和下次发送恢复逻辑不变 |
| 主动屏幕感知 | 创建 `screen_awareness_check` → worker → 回复或静默结束 | 检查间隔、冷却、截图批次和健康提醒过滤不变 |
| 到期提醒 | reminder 信息写入事件 payload → worker → 标记完成 | 提醒文本、失败兜底回复和完成时机不变 |
| 记忆整理 | 创建不可变运行上下文 → worker → 按该上下文写入进度 | 自动触发次数、失败重试和角色切换保护不变 |

按钮和等待占位仍由 worker 生命周期控制；`PetUiStateStore` 独立描述 thinking/streaming/speaking/error，避免把“模型仍在工作”和“TTS 正在播放”混为同一种状态。

各信号连接、QTimer 间隔、线程启动顺序、ResourceManager 注册方式和关闭顺序不在本轮改变。

## 7. 错误处理与兼容边界

- 迁移失败时保留旧配置及备份，明确报告错误，不以默认值覆盖用户数据。
- 已公开插件 SDK、角色包、聊天历史和运行时数据格式不变。
- `PetWindow` 内部属性、旧方法名和测试桩调用方式不属于公共接口。
- 必需组件缺失时暴露装配错误，不再通过 `getattr(..., None)` 跳过关键操作。
- 可选插件、渲染器和平台特性继续记录结构化错误并与主窗口隔离。
- 退出阶段保留必要的幂等保护。
- 退役的 `proactive_check` 意外进入内部事件链时，记录来源并明确失败，不返回看似正常的角色回复。
- 记忆整理上下文缺失时不写入任何进度；角色确实切换时继续安全跳过写入。

## 8. 测试设计

### 8.1 测试环境可信度

运行 UI 验证前，runtime 必须具备 `requirements-dev.txt` 中的 pytest 插件。以下 warning 视为测试环境失败，而不是可忽略告警：

- unknown config option: `env`
- unknown config option: `qt_api`
- unknown config option: `timeout`
- unknown config option: `timeout_method`

### 8.2 行为锁定测试

在删除旧代码前，用当前生产入口锁定：

- 用户发送、进度回复、最终分段、TTS 开始与交互结束的状态顺序。
- worker 忙碌期间按钮状态、等待占位文字、空输入焦点释放和已有下一条输入时的焦点保留。
- 主动屏幕感知的间隔、冷却、批次上限、事件 payload 和静默失败结束。
- 到期提醒成功、失败和完成标记时机。
- 记忆整理成功、连续失败、角色切换和清理后的自动重启行为。

### 8.3 迁移测试

- 只有旧节：完整迁移到新节并删除旧节。
- 新旧并存：新节优先，旧节删除。
- 旧节无效：原文件和版本保持不变，错误可定位。
- 重复执行：迁移幂等。

### 8.4 结构测试

生产代码中除迁移层外不得再出现：

- `proactive_care_settings`
- `proactive_care_timer`
- `_check_proactive_care`
- `LEGACY_PROACTIVE_EVENT_TYPE`
- `reply_waiting_ui_active`
- `active_event_type`
- `active_reminder_id`
- `active_reminder_text`
- `pending_screen_observation_event_reminder_id`
- `memory_curation_mode`
- `memory_curation_character_id`
- `memory_curation_target_history_count`
- `memory_curation_consumed_turns`

本轮触及的状态域不得再通过复制 `PetWindow` 未绑定方法到半初始化自定义类，来要求生产方法容忍缺失的必需属性。纯函数应直接测试；Qt 行为应使用真实组件或聚焦的正式测试夹具。未触及区域的测试替换留给对应后续规格，避免扩大本轮范围。

### 8.5 验证命令

实施时按风险逐步运行：

```powershell
.\runtime\python.exe -m pytest tests/unit/test_settings_service.py tests/unit/test_config.py -q
.\runtime\python.exe -m pytest tests/ui/test_ui_state.py tests/ui/test_pet_window.py -q
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest -q
```

## 9. 验收标准

1. 正常用户流程和界面表现不变。
2. 旧 `proactive_care` 用户配置可自动迁移，且迁移后运行时只读取新节。
3. 本文列出的镜像字段和旧内部入口从生产代码退出。
4. 相关测试覆盖生产入口，不再反向要求生产代码支持非法测试对象。
5. pytest 配置插件生效，不存在 unknown-option warning。
6. 相关测试和全量测试通过；若存在既有 native crash，必须给出可复现证据并与本轮改动区分，不能以重跑一次代替分析。
7. `app/` 与 `plugins/` 合计删除行数大于新增行数；预期净删 400–650 行。
8. 不新增仅为测试桩服务的生产分支。
9. 不通过删除有效测试来实现净删减指标。

## 10. 实施边界与提交组织

实现应按可独立回滚的小提交组织：

1. `test:` 补齐生产入口的行为锁定测试。
2. `refactor:` 增加配置迁移并收口设置读取。
3. `refactor:` 删除旧 proactive/screen-awareness 双路径。
4. `refactor:` 删除等待态镜像字段，让 `_set_busy()` 直接同步等待表现。
5. `refactor:` 收敛主动事件镜像字段。
6. `refactor:` 收敛记忆整理运行上下文。
7. `refactor:` 删除必需组件的测试桩容错并迁移测试。
8. `test:` 完成全量验证和生产代码净删减核对。

每个提交都必须保持其相关测试通过。不得把行为锁定、旧路径删除和多个状态域重构压成一个不可审查的大提交。

## 11. 风险与缓解

### UI 状态时序变化

风险：删除镜像布尔值后，等待占位文字可能在 worker 完成与首段字幕开始之间提前或延后恢复。

缓解：在修改前锁定 worker busy 切换、`THINKING/STREAMING/SPEAKING/IDLE` 转移和焦点行为；等待表现仍在 `_set_busy()` 的原时点更新，只删除镜像字段和动态方法查找。

### 配置迁移覆盖用户值

风险：新旧节并存时错误合并。

缓解：明确采用新节优先，原子写、备份、幂等测试，失败时不推进版本。

### 旧测试仍在验证退役实现

风险：删除旧测试后遗漏真实行为。

缓解：先把行为断言迁移到当前生产入口，再删除旧路径；不得先删测试再凭现状补断言。

### 外部代码引用内部名称

风险：第三方代码可能直接导入 `app.agent.proactive_care` 或 `PetWindow` 内部属性。

缓解：这些入口未列入公开插件 SDK，按已确认兼容策略不继续保留；发布说明需明确内部导入不受兼容保证。

## 12. 后续审查队列

本轮完成后，后续规格建议依次处理：

1. 工具、事件和插件边界真实性。
2. TTS 双播放后端与完成兜底。
3. ResourceManager 和退出竞态。
4. 生产死代码，包括旧视觉摘要链。
5. 日志和 API 配置迁移的运行时双路径。
