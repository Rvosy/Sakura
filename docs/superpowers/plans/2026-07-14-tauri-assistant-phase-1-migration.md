# Sakura Tauri Assistant 第一阶段迁移计划

> 本计划只负责完成主应用从 PySide6 到 Tauri 的迁移。平台安全 API、正式 Capability Broker、插件沙箱、Runtime 分包和更新体系按后续阶段实施。

**Goal:** 让 Tauri 成为 Sakura 唯一的生产桌面入口和所有可见窗口的宿主；将现有 Python 对话、人格、记忆、工具、TTS 合成和主动互动逻辑迁入无 Qt 的长期运行 Brain Host，并通过版本化 IPC 与 Tauri 通信。

**Architecture:** 新建 `desktop/` Tauri 2 主应用。Rust 负责窗口、托盘、单实例、Python Host 生命周期、IPC、音频播放和截图入口；Web 前端负责桌宠、聊天、字幕、输入、设置、工作室、历史和确认界面；Python Brain Host 复用现有 `AgentRuntime`、`ChatPipeline`、记忆、插件和配置服务，不再创建 `QApplication`、`QObject`、`QThread` 或任何 QWidget。第一阶段保留现有 Python 工具执行与确认策略作为兼容层，但确认后的执行只接受 Python 端保存的 `PendingToolAction.id`，不得由前端回传或替换执行参数。

**Tech Stack:** Python 3.12、Tauri 2、Rust、vanilla HTML/CSS/JavaScript、长度前缀 JSON IPC、pytest、Cargo tests。

---

## 1. 第一阶段边界

### 1.1 必须完成

- Tauri 成为桌宠主窗口和生产启动入口。
- Tauri 创建并监管一个长期运行的 Python Brain Host。
- 主窗口、聊天输入、字幕、立绘、工具确认、托盘、设置、工作室和历史全部使用 Tauri/WebView。
- Python 端保留现有：
  - 角色与人格；
  - `ChatPipeline` / `AgentRuntime`；
  - 聊天历史；
  - 基础记忆和记忆整理；
  - 提醒；
  - 主动互动；
  - 插件和 MCP；
  - TTS Provider 与语音合成。
- Rust 端接管：
  - 窗口和多显示器定位；
  - 透明、置顶、拖动和输入穿透；
  - 托盘和单实例；
  - Python Host 启停、健康检查和有限重启；
  - TTS 音频播放；
  - 屏幕和区域截图；
  - 临时音频、截图资源清理。
- 保持现有 `data/`、`characters/`、`plugins/` 数据格式兼容。
- 正常生产启动链路不得导入 PySide6。
- `python main.py` 仍作为开发入口，但其职责改为定位并启动 Tauri 主程序，不再创建 `QApplication`。

### 1.2 本阶段不做

- 不实现最终版 Rust Capability Broker。
- 不实现结构化 Permission Scope、授权摘要和 Audit System。
- 不承诺 Python Brain 或第三方插件已被 OS 沙箱隔离。
- 不重写 Agent Runtime、Memory Store 或 Prompt 管线。
- 不实现新插件签名、市场、发布者验证和插件更新。
- 不实现 Launcher / Updater、双版本回滚和 Runtime 独立安装。
- 不实现 Credential Broker；API Key 继续使用现有 Python 配置路径。
- 不发布 Agent Planner 或新的高权限工具。
- 不改变现有角色包、历史、记忆和配置 Schema。

### 1.3 过渡安全约束

- Tauri 前端不得直接执行 Python 模块或 Shell。
- 前端确认工具时只发送 `PendingToolAction.id` 和决定，不回传参数。
- Python Host 必须从内存中的待确认表取回原始参数。
- IPC 会话使用一次性随机凭据，但第一阶段不将其描述为抵御同用户恶意进程的完整安全边界。
- 现有插件继续在 Python Host 内运行，因此只能维持现有信任等级。
- 第一阶段不得新增比当前版本更高权限的默认工具或插件。

---

## 2. 现状与迁移原则

当前迁移不能按“重写几个 Qt 控件”处理：

- `main.py` 仍创建 `QApplication`。
- `app/ui/pet_window.py` 超过 7,600 行，同时承担窗口、聊天、TTS、截图、主动事件、历史、工具确认和外部进程管理。
- `ChatPipeline` 和 `AgentRuntime` 已基本独立于 UI，应继续复用。
- `ChatWorker`、`ResourceManager`、TTS 播放和部分截图逻辑仍依赖 Qt。
- 设置页和角色工作室已经存在独立 Tauri 前端，可以迁移到主 Tauri 应用。

迁移遵守以下原则：

1. 先抽离状态和服务，再迁移视觉层。
2. 保持 Python 业务行为，避免同时重写 Agent。
3. 每个 PR 都必须保持 `dev` 可启动。
4. 旧 Qt 主窗口在切换完成前作为临时回退路径存在。
5. 新旧 UI 不得同时写入同一会话状态。
6. 所有跨进程数据必须使用版本化 DTO，禁止传递 Python 对象或 Qt 类型。
7. 大图片和音频不进入普通 JSON Payload。
8. 最终切换前必须完成完整测试和 Windows 手工验收。

---

## 3. 目标目录

### 3.1 新建

```text
desktop/
├─ frontend/
│  ├─ index.html
│  ├─ app.js
│  ├─ styles.css
│  ├─ core/
│  │  ├─ bridge.js
│  │  ├─ store.js
│  │  ├─ events.js
│  │  └─ resources.js
│  ├─ pet/
│  │  ├─ pet_view.js
│  │  ├─ portrait_view.js
│  │  ├─ subtitle_view.js
│  │  └─ input_view.js
│  ├─ chat/
│  │  ├─ chat_controller.js
│  │  └─ confirmation_view.js
│  ├─ settings/
│  ├─ studio/
│  ├─ history/
│  └─ diagnostics/
└─ src-tauri/
   ├─ Cargo.toml
   ├─ build.rs
   ├─ tauri.conf.json
   ├─ capabilities/default.json
   └─ src/
      ├─ main.rs
      ├─ lib.rs
      ├─ app_state.rs
      ├─ brain_host.rs
      ├─ ipc.rs
      ├─ commands.rs
      ├─ windows.rs
      ├─ tray.rs
      ├─ audio.rs
      ├─ capture.rs
      └─ resources.rs

app/brain_host/
├─ __init__.py
├─ __main__.py
├─ protocol.py
├─ transport.py
├─ server.py
├─ application.py
├─ scheduler.py
├─ pending_actions.py
├─ dto.py
└─ errors.py
```

### 3.2 重点修改

- `main.py`
- `app/core/bootstrap.py`
- `app/core/app_context.py`
- `app/core/chat_pipeline.py`
- `app/core/resource_manager.py`
- `app/agent/memory_curation_worker.py`
- `app/agent/screen_observation.py`
- `app/backchannel/controller.py`
- `app/voice/tts.py`
- `app/voice/tts_synthesis.py`
- `app/plugins/manager.py`
- `tools/settings-tauri/frontend/*`
- `tools/studio-tauri/frontend/*`
- `README.md`
- `docs/TECHNICAL_README.md`
- `docs/SETUP.md`

### 3.3 第一阶段结束后删除或退出生产路径

- `app/ui/pet_window.py`
- `app/ui/history_window.py`
- `app/ui/tool_confirmation_panel.py`
- `app/ui/tray_menu.py`
- `app/ui/subtitle_controller.py`
- `app/ui/portrait_controller.py`
- `app/ui/manual_screenshot_overlay.py`
- `app/ui/screen_capture.py`
- `app/ui/tauri_settings.py`
- `app/ui/tauri_studio.py`
- `app/core/chat_worker.py`
- Qt 版 TTS 播放实现

纯算法或数据转换函数必须先移动到无 UI 模块，再删除旧文件。

---

## 4. 第一阶段 IPC 子集

第一阶段先固定一个可向 SAP-002 演进的最小协议，不等待完整平台安全规范。

### 4.1 传输

- Rust 启动 Python 子进程。
- 使用子进程 stdin/stdout 双向通信。
- 每帧格式为：

```text
4 字节大端无符号长度
+
UTF-8 JSON
```

- 第一阶段普通消息最大 8 MiB。
- stderr 只用于日志。
- 截图和音频通过受控临时资源描述符传递。
- 非法长度、非法 JSON、不完整帧和未知消息类型必须关闭当前 IPC 会话。

### 4.2 Envelope

```json
{
  "protocol": 1,
  "kind": "request",
  "id": "req_01",
  "session_id": "session_01",
  "sequence": 1,
  "method": "chat.send",
  "deadline_ms": 60000,
  "payload": {}
}
```

第一阶段支持：

```text
request
response
event
cancel
stream_chunk
stream_end
```

### 4.3 首批方法

系统：

```text
system.hello
system.health
system.shutdown
system.cancel
```

聊天：

```text
chat.send
chat.cancel
chat.confirm_action
chat.reject_action
```

角色、设置和历史：

```text
character.list
character.current
character.switch
settings.get
settings.apply
history.list
history.get
```

主动事件和观察：

```text
event.dispatch
observation.push
```

TTS：

```text
tts.synthesize
tts.cancel
tts.playback_state
```

诊断：

```text
runtime.status
memory.status
plugin.status
```

### 4.4 Python → Tauri 事件

```text
backend.starting
backend.ready
backend.degraded
backend.error

chat.progress
chat.reply
chat.error
chat.cancelled
chat.confirmation_requested

character.changed
settings.changed
history.changed
memory.status_changed

tts.audio_ready
tts.error

assistant.proactive_message
assistant.reminder_due
```

---

## 5. 交付策略

第一阶段不得作为一个超大 PR 实施。建议拆为以下 PR：

| PR | 分支 | 交付 |
|---|---|---|
| 1 | `feat/tauri-desktop-shell` | Tauri 主程序、透明窗口、托盘、单实例和技术验证 |
| 2 | `feat/brain-host-ipc` | Rust/Python IPC、握手、健康检查和 Host 监管 |
| 3 | `refactor/headless-assistant-service` | 无 Qt Assistant 服务、调度器和生命周期 |
| 4 | `feat/tauri-pet-chat` | 桌宠、聊天、字幕、立绘和工具确认 |
| 5 | `feat/tauri-audio-observation` | TTS 播放、截图、主动互动和提醒 |
| 6 | `feat/tauri-assistant-windows` | 设置、工作室、历史、诊断和托盘整合 |
| 7 | `refactor/tauri-production-cutover` | 生产入口切换、Qt 主链删除和文档更新 |

每个 PR 都从最新 `dev` 建分支，使用中文标题和说明，并在提交 PR 前运行完整 pytest。

---

## 6. 实施任务

## Task 0：建立基线和迁移清单

**Files:**

- Create: `docs/migration/tauri-phase-1-parity-matrix.md`
- Read: `main.py`
- Read: `app/ui/pet_window.py`
- Read: `tests/ui/test_pet_window.py`

- [ ] 从最新 `dev` 创建第一条功能分支。
- [ ] 运行完整 Python 测试，记录基线通过数量和耗时。
- [ ] 记录当前冷启动时间、首帧时间、空闲 CPU 和内存。
- [ ] 建立功能对照矩阵，至少覆盖：
  - 窗口透明和置顶；
  - 拖动和多显示器；
  - 中文/日文输入法；
  - 立绘和表情切换；
  - 字幕打字机；
  - 聊天发送、取消和错误；
  - 工具确认；
  - TTS 和快速接话；
  - 手动截图和自动屏幕观察；
  - 主动互动和提醒；
  - 设置、工作室、历史和日志；
  - 托盘、开机启动和退出清理；
  - 插件、MCP 和手机端。
- [ ] 标记每项功能的现有代码入口和测试入口。
- [ ] 在矩阵中明确第一阶段必须达到的等价行为。

**Verification:**

```powershell
.\runtime\python.exe -m pytest
```

---

## Task 1：Tauri 主应用技术门

**Files:**

- Create: `desktop/frontend/index.html`
- Create: `desktop/frontend/app.js`
- Create: `desktop/frontend/styles.css`
- Create: `desktop/src-tauri/*`
- Create: `tests/unit/test_tauri_desktop_layout.py`

- [ ] 创建 `sakura-desktop` Tauri crate。
- [ ] 使用与现有设置页相同的 vanilla HTML/CSS/JavaScript，不引入 React/Vue。
- [ ] 创建透明、无边框、置顶的主窗口。
- [ ] 实现窗口拖动、显示/隐藏和输入穿透切换。
- [ ] 实现托盘菜单和单实例聚焦。
- [ ] 验证中文和日文 IME。
- [ ] 验证 100%、125%、150%、200% DPI。
- [ ] 验证主显示器和混合 DPI 多显示器移动。
- [ ] 建立截图和音频播放最小原型。
- [ ] Rust 端不得向 WebView 暴露任意 Shell 或文件系统命令。

**Exit gate:**

- 透明窗口、IME、多显示器、拖动、托盘、音频和截图全部通过 Windows 手工验证。
- 未通过的项目必须先形成 ADR，不得直接进入主界面迁移。

**Verification:**

```powershell
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo build --manifest-path desktop/src-tauri/Cargo.toml
.\runtime\python.exe -m pytest tests/unit/test_tauri_desktop_layout.py -q
```

---

## Task 2：实现 Rust/Python 帧协议

**Files:**

- Create: `desktop/src-tauri/src/ipc.rs`
- Create: `app/brain_host/protocol.py`
- Create: `app/brain_host/transport.py`
- Create: `tests/unit/test_brain_host_protocol.py`

- [ ] 先为 Python 帧编码、分片读取、超长消息和非法 JSON编写失败测试。
- [ ] 为 Rust 编写等价的 codec 测试。
- [ ] 固定大端长度、8 MiB 上限和 UTF-8 JSON。
- [ ] 实现 request/response/event/cancel/stream 类型。
- [ ] 实现稳定错误格式。
- [ ] 拒绝重复请求 ID 和异常 sequence。
- [ ] 关闭会话时终止所有未完成请求。
- [ ] 日志只写 stderr。
- [ ] 加入 Python/Rust 互操作 golden fixture，确保双方使用同一帧。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_brain_host_protocol.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml ipc
```

---

## Task 3：建立 Python Brain Host

**Files:**

- Create: `app/brain_host/__main__.py`
- Create: `app/brain_host/server.py`
- Create: `app/brain_host/application.py`
- Create: `app/brain_host/dto.py`
- Create: `app/brain_host/errors.py`
- Create: `tests/unit/test_brain_host_server.py`
- Modify: `app/core/bootstrap.py`
- Modify: `app/core/app_context.py`

- [ ] Brain Host 支持 `python -m app.brain_host` 启动。
- [ ] 启动时读取受控的 `base_dir`、会话凭据和协议版本。
- [ ] 完成 `system.hello`、`system.health` 和 `system.shutdown`。
- [ ] 将 `AppContext` 组装放在 Brain Host 内部。
- [ ] 将启动状态转换为 JSON DTO。
- [ ] 后端初始化失败时返回稳定错误，不打印协议外 stdout。
- [ ] 添加导入守卫测试：Brain Host 正常路径不得加载 `PySide6` 或 `app.ui`。
- [ ] 保持现有数据目录、角色和配置加载行为不变。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_brain_host_server.py tests/unit/test_bootstrap.py -q
```

---

## Task 4：抽出无 Qt AssistantApplication

**Files:**

- Create: `app/core/assistant_service.py`
- Create: `app/brain_host/scheduler.py`
- Create: `app/brain_host/pending_actions.py`
- Create: `tests/unit/test_assistant_service.py`
- Modify: `app/core/chat_pipeline.py`
- Modify: `app/agent/memory_curation_worker.py`
- Modify: `app/backchannel/controller.py`
- Modify: `app/core/resource_manager.py`

- [ ] 将 `PetWindow` 中的聊天会话状态移入 `AssistantApplication`。
- [ ] 为每次交互维护：
  - interaction ID；
  - active request；
  - cancellation token；
  - pending action；
  - progress callback；
  - reply result。
- [ ] 使用标准线程池或受控 worker 替代 `ChatWorker` / `EventWorker`。
- [ ] 将提醒轮询、主动互动和记忆整理调度移出 QTimer。
- [ ] 保证同一时间只有一个前台聊天任务。
- [ ] 主动事件不得抢占当前用户聊天。
- [ ] 将待确认动作保存在 Python 端映射中。
- [ ] `confirm_action` 只接受 action ID。
- [ ] 为关闭顺序、取消和 worker 泄漏建立测试。
- [ ] 保留 `ChatPipeline` 和 `AgentRuntime` 的行为测试。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_assistant_service.py tests/integration/test_chat_pipeline.py tests/integration/test_agent_core.py -q
```

---

## Task 5：实现 Python Host 监管

**Files:**

- Create: `desktop/src-tauri/src/brain_host.rs`
- Create: `desktop/src-tauri/src/app_state.rs`
- Create: `tests/fixtures/fake_brain_host.py`
- Add Rust tests in: `desktop/src-tauri/src/brain_host.rs`

- [ ] Rust 解析当前 Python 路径；开发模式优先使用调用者传入的 `SAKURA_PYTHON_EXE`。
- [ ] Rust 创建一次性会话凭据并启动 Brain Host。
- [ ] 完成握手、健康检查、优雅关闭和强制终止。
- [ ] Python 崩溃时 UI 保持可用。
- [ ] 自动重启最多 3 次，并使用退避。
- [ ] 重启后旧 session、请求和资源全部失效。
- [ ] 超过阈值后显示诊断页，不进入无限重启。
- [ ] App 退出时先停止新请求，再关闭 Brain、音频和临时资源。

**Verification:**

```powershell
cargo test --manifest-path desktop/src-tauri/Cargo.toml brain_host
```

---

## Task 6：迁移桌宠窗口和前端状态

**Files:**

- Create: `desktop/frontend/core/*`
- Create: `desktop/frontend/pet/*`
- Modify: `desktop/src-tauri/src/windows.rs`
- Create: `tests/unit/test_tauri_pet_frontend.py`

- [ ] 建立单一前端 Store，不允许各窗口复制不可同步的状态。
- [ ] 实现立绘加载、表情切换和过渡动画。
- [ ] 实现字幕显示、分段回复和打字机动画。
- [ ] 实现输入栏、发送、取消和截图入口。
- [ ] 实现窗口底边锚定、缩放和气泡布局。
- [ ] 实现主窗口隐藏、恢复和多显示器边界修正。
- [ ] Rust 提供最小窗口命令；前端不得自行读写任意路径。
- [ ] 将角色资源映射为受控 asset URL。
- [ ] 使用现有角色主题值驱动 CSS variables。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tauri_pet_frontend.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml windows
```

---

## Task 7：接通聊天、进度和工具确认

**Files:**

- Create: `desktop/frontend/chat/chat_controller.js`
- Create: `desktop/frontend/chat/confirmation_view.js`
- Modify: `app/brain_host/application.py`
- Create: `tests/integration/test_tauri_brain_chat_contract.py`

- [ ] `chat.send` 调用 `AssistantApplication`。
- [ ] 将 `AgentProgress` 转换为 `chat.progress`。
- [ ] 将最终 `ChatReply` 转换为稳定 DTO。
- [ ] 支持请求取消并忽略晚到结果。
- [ ] Tauri 显示待确认工具名称、原因和只读参数。
- [ ] 确认/拒绝只发送 action ID。
- [ ] Python Host 执行前验证 action ID 仍存在且属于当前 session。
- [ ] 确认完成后继续原有 Agent 对话链。
- [ ] 网络、模型和格式错误均显示用户可理解的错误状态。
- [ ] 历史记录继续由现有 `ChatHistoryStore` 写入。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/integration/test_tauri_brain_chat_contract.py tests/integration/test_chat_worker.py tests/integration/test_chat_pipeline.py -q
```

---

## Task 8：迁移 TTS、播放和快速接话

**Files:**

- Modify: `app/voice/tts.py`
- Modify: `app/voice/tts_synthesis.py`
- Create: `app/voice/tts_synthesis_service.py`
- Create: `desktop/src-tauri/src/audio.rs`
- Create: `tests/unit/test_tts_synthesis_service.py`
- Add Rust audio tests

- [ ] 将 TTS Provider 与 Qt Signal 解耦。
- [ ] Python 只负责合成和生成临时音频资源。
- [ ] Rust 负责播放、停止、音量和播放状态事件。
- [ ] 音频资源使用随机文件名、受控目录和 TTL。
- [ ] 前端根据播放开始/结束驱动字幕与说话状态。
- [ ] 快速接话沿用现有选择逻辑，但通过相同音频链播放。
- [ ] 角色切换、设置变更和应用退出必须取消旧播放。
- [ ] 删除生产路径对 `QMediaPlayer`、`QAudioSink` 的依赖。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tts_synthesis_service.py tests/unit/test_tts.py tests/unit/test_tts_service_state.py tests/unit/test_audio_sink_player.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml audio
```

---

## Task 9：迁移截图、视觉观察和主动事件

**Files:**

- Create: `desktop/src-tauri/src/capture.rs`
- Modify: `app/agent/screen_observation.py`
- Modify: `app/brain_host/scheduler.py`
- Create: `tests/integration/test_tauri_observation_contract.py`
- Add Rust capture tests

- [ ] Rust 枚举显示器并完成全屏、指定显示器和区域截图。
- [ ] 前端实现框选覆盖层，不使用 Qt Overlay。
- [ ] 截图通过临时资源描述符发送给 Brain。
- [ ] Python 端保留现有缩放、编码、视觉消息和摘要逻辑。
- [ ] 主动屏幕观察由 Python scheduler 决策，由 Rust 执行捕获。
- [ ] 用户聊天、截图和主动事件共享同一忙碌状态。
- [ ] 截图默认不持久化；正常退出和崩溃恢复均清理。
- [ ] 提醒和主动消息通过统一事件进入前端。
- [ ] 用户关闭主动观察后停止相关调度。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/integration/test_tauri_observation_contract.py tests/unit/test_visual_observation.py tests/ui/test_screen_capture_crop.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml capture
```

---

## Task 10：合并设置、工作室、历史和诊断窗口

**Files:**

- Move/adapt: `tools/settings-tauri/frontend/*` → `desktop/frontend/settings/*`
- Move/adapt: `tools/studio-tauri/frontend/*` → `desktop/frontend/studio/*`
- Create: `desktop/frontend/history/*`
- Create: `desktop/frontend/diagnostics/*`
- Modify: `desktop/src-tauri/src/windows.rs`
- Modify: `app/brain_host/application.py`
- Create: `tests/integration/test_tauri_secondary_windows.py`

- [ ] 设置和工作室改为同一 Tauri App 的独立 WebView 窗口。
- [ ] 删除原有“Qt 启动独立 Tauri 子进程”的控制方向。
- [ ] 设置页通过主 IPC 调用 Python Settings Service。
- [ ] 工作室通过主 IPC 调用 `CharacterStudioService`。
- [ ] 历史窗口通过分页 DTO 读取记录，不一次加载全部历史。
- [ ] 诊断窗口显示 Brain、插件、MCP、TTS 和资源状态。
- [ ] 重复打开窗口时聚焦现有实例。
- [ ] 所有窗口共享当前角色和主题状态。
- [ ] 设置应用后只刷新受影响服务，不重启整个 App。
- [ ] 兼容测试通过后删除独立 settings/studio 进程桥。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/integration/test_tauri_secondary_windows.py tests/unit/test_tauri_settings.py tests/unit/test_tauri_studio.py tests/unit/test_character_studio.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

---

## Task 11：迁移托盘、启动和插件交互

**Files:**

- Modify: `desktop/src-tauri/src/tray.rs`
- Modify: `app/platforms/launch_at_login.py`
- Modify: `app/plugins/manager.py`
- Modify: `plugins/sakura_mobile/*`
- Create: `tests/integration/test_tauri_runtime_events.py`

- [ ] 托盘提供显示、隐藏、设置、历史、工作室和退出。
- [ ] 单实例第二次启动只聚焦现有窗口。
- [ ] 开机启动指向 Tauri 主程序。
- [ ] 插件事件从 Qt Signal 改为 Brain Host 事件。
- [ ] 插件声明式设置继续由 Tauri 设置页渲染。
- [ ] 非声明式 Qt 插件 UI 在第一阶段标记不兼容并拒绝加载。
- [ ] Sakura Mobile 通过无 Qt bridge 提交聊天请求。
- [ ] MCP 进程和插件在 Brain Host 退出时正确关闭。

**Verification:**

```powershell
.\runtime\python.exe -m pytest tests/integration/test_tauri_runtime_events.py tests/unit/test_plugin_system.py tests/unit/test_plugin_services.py tests/unit/test_sakura_mobile.py -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml tray
```

---

## Task 12：生产入口切换和 Qt 主链清理

**Files:**

- Modify: `main.py`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Delete or archive: PySide6 production UI modules
- Modify: related tests

- [ ] `main.py` 不再导入 PySide6。
- [ ] 默认启动 Tauri 主程序。
- [ ] 开发入口把当前 `sys.executable` 通过 `SAKURA_PYTHON_EXE` 交给 Rust。
- [ ] Brain Host 由 Tauri 创建，不允许用户同时启动第二个 Host。
- [ ] 删除 Qt PetWindow、Qt History、Qt Tool Confirmation 和 Qt Tray 的生产路径。
- [ ] 将仍需 PySide6 的旧开发工具移到独立可选 requirements；基础运行依赖不再包含 PySide6。
- [ ] 删除旧 Qt UI 专用测试，保留并迁移其中的行为断言。
- [ ] 加入静态测试，确保生产启动模块依赖图不含 PySide6。
- [ ] 保留原数据目录，不执行破坏性迁移。
- [ ] 连续一个开发版本保留明确的回退构建，不在运行时自动回退。

**Verification:**

```powershell
rg -n "PySide6" main.py app/brain_host app/core/assistant_service.py
.\runtime\python.exe -m pytest
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo build --release --manifest-path desktop/src-tauri/Cargo.toml
```

预期 `rg` 在上述生产入口中无匹配。

---

## Task 13：文档、完整测试和发布验收

**Files:**

- Modify: `README.md`
- Modify: `docs/TECHNICAL_README.md`
- Modify: `docs/SETUP.md`
- Modify: `.github/CONTRIBUTING.md`
- Update: `docs/migration/tauri-phase-1-parity-matrix.md`

- [ ] 更新启动命令、构建命令和目录结构。
- [ ] 说明 Tauri 与 Python Brain 的职责。
- [ ] 标记第一阶段仍使用兼容权限和插件模型。
- [ ] 运行完整 Python 测试。
- [ ] 运行全部 Tauri crate Rust 测试。
- [ ] 构建 release 版本。
- [ ] 在干净 Windows x64 环境执行手工验收。
- [ ] 对照 parity matrix 逐项签字。
- [ ] 比较迁移前后的启动时间、CPU 和内存；明显回归必须记录原因。
- [ ] 使用中文提交 PR，目标分支为 `dev`。

**Full verification:**

```powershell
.\runtime\python.exe -m pytest
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
cargo build --release --manifest-path desktop/src-tauri/Cargo.toml
```

---

## 7. 手工验收清单

### 启动与恢复

- [ ] Tauri 窗口先显示，Brain 后台初始化。
- [ ] Brain 启动失败时 UI 仍可打开诊断页。
- [ ] Brain 崩溃后未完成聊天结束，并在有限次数内恢复。
- [ ] 重复启动只聚焦现有实例。
- [ ] 退出后不存在残留 Python、MCP、TTS 或浏览器进程。

### 桌宠

- [ ] 透明背景无黑边。
- [ ] 始终置顶开关正确。
- [ ] 鼠标拖动、点击和输入穿透正确。
- [ ] 混合 DPI 多显示器移动后尺寸和底边位置不漂移。
- [ ] 角色切换后立绘、主题、语气和初始消息更新。

### 聊天

- [ ] 中文和日文输入法正常。
- [ ] 普通聊天、视觉聊天、进度回复和最终回复正常。
- [ ] 发送期间可以取消。
- [ ] 工具确认和拒绝后会继续生成角色回复。
- [ ] action ID 过期、重复确认和跨 session 确认均被拒绝。

### TTS 与主动互动

- [ ] 多段回复按顺序播放。
- [ ] 快速接话不会与正式 TTS 重叠。
- [ ] 角色切换和退出会停止旧音频。
- [ ] 提醒、主动关怀和屏幕观察不会抢占当前聊天。
- [ ] 关闭主动观察后不再截图。

### 次级窗口

- [ ] 设置、工作室、历史和诊断可以重复打开并聚焦。
- [ ] 设置应用后主窗口即时刷新。
- [ ] 工作室保存后角色资源正确写入。
- [ ] 历史分页加载，不阻塞主窗口。

---

## 8. 阶段退出标准

第一阶段只有在全部满足时才算完成：

1. 所有用户可见窗口均由 Tauri 创建。
2. 正常启动路径不创建 QApplication，也不导入 PySide6。
3. Python Brain Host 可以独立完成聊天、记忆、工具、插件和主动事件。
4. Tauri 可以启动、监控、关闭和有限次数重启 Brain Host。
5. 桌宠、聊天、TTS、截图、主动互动、设置、工作室和历史达到 parity matrix 要求。
6. 当前角色、聊天历史、记忆、配置和插件数据无需破坏性迁移。
7. 全部 Python 测试和 Rust 测试通过。
8. Windows x64 手工验收通过。
9. 生产依赖不再强制安装 PySide6。
10. 文档明确第一阶段仍未完成最终 Capability、Permission 和插件安全边界。

---

## 9. 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `PetWindow` 职责过多 | 迁移遗漏、状态错乱 | 先抽 `AssistantApplication`，不直接翻译 7,600 行 UI |
| Qt 信号和线程隐藏业务语义 | Headless 后竞态 | 为聊天、取消、提醒和关闭顺序增加契约测试 |
| WebView 透明窗口和 DPI 差异 | 桌宠体验退化 | 技术门先验证，未通过不进入主迁移 |
| TTS 播放迁移 | 状态、设备和关闭崩溃 | Python 只合成，Rust 单一拥有播放 |
| 截图跨 DPI | 视觉坐标错误 | Rust 统一显示器坐标，保留混合 DPI 测试 |
| IPC 大对象和背压 | 卡顿或内存增长 | 普通帧限长，大对象走临时资源 |
| 插件依赖 Qt UI | 插件无法加载 | 第一阶段只支持无 UI/声明式插件，明确兼容提示 |
| 新旧 UI 同时运行 | 数据和进程冲突 | 每次只允许一个桌面宿主，切换使用明确构建 |
| 单个超大 PR | 难以审查和回退 | 按 7 个 PR 逐步合并，每个 PR 保持 dev 可用 |

---

## 10. 后续阶段接口

第一阶段完成后，第二阶段在不重写 UI 的前提下替换以下兼容实现：

| 第一阶段 | 第二阶段 |
|---|---|
| Python ToolRegistry 直接执行 | Rust Capability Broker |
| Python 确认策略 | Rust Permission Manager |
| action ID 绑定 | 规范化授权摘要 |
| 临时 Observation bridge | Core Observation Manager |
| Python 直接使用 API Key | Credential Broker / 受控网络请求 |
| Python 进程内插件 | 隔离 Sidecar / 签名插件 |
| 普通运行日志 | 结构化 Audit System |

第一阶段的 IPC Envelope、DTO 和前端事件模型必须为这些替换保留版本字段，但不得提前实现尚未验证的 Agent Planner API。
