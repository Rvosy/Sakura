---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# WP-4-04 Python 插件能力等价规范

## 1. 范围与非目标

本规范冻结 CAP-012 在 Runtime v2 的最小真实纵向链：当前 Core generation 通过
[`ADR-0016`](../../adr/0016-runtime-v2-generation-private-plugin-worker.md) 的私有插件 worker 发现和加载
Python 插件，消费 tool、prompt patch、context provider、受控事件，并通过 Tauri 设置窗口完成插件启停、
声明式设置和 action。当前执行状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

真实消费者是 bundled Python Core、聊天 ToolRegistry/AgentRuntime、Rust gateway/supervisor、设置窗口和
隔离 fixture 插件。不得用 Core 内直接构造 `PluginManager` 或只跑 legacy 单测代替产品纵向链。

本 WP 不修改顶层 `plugins/**`，不迁移 renderer、Qt `chat_ui_widget`/`tools_tab`、Playwright 插件实现、
Sakura Mobile、TTS、截图、角色 Studio、插件安装/更新/签名或通用 worker 平台。现有插件 API v2 的
不能跨进程能力必须明确显示 `unavailable`，不得通过 pickle、Qt 对象或裸 callable 穿透私有边界。

## 2. 发现、配置与权限

- 继续从 assistant root 的 `plugins/*/plugin.yaml` 发现插件，以 `data/config/plugins.yaml` 作为启停、
  priority 和 required 覆盖。缺失配置使用 manifest 默认值；损坏覆盖仅隔离覆盖文件，损坏 manifest 只
  隔离对应插件。
- `plugin_id`、entry、API version、priority、required、permissions 和 contribution descriptor 必须有类型、
  长度、数量及唯一性校验。未知 API/permission、ID/manifest 不一致、重复工具/section/provider/patch ID
  或越权贡献使该插件失败，不影响已健康插件。
- 配置保存沿用 Python 数据 owner、revision、窗口 generation 和原子替换语义。required 插件不得在 UI
  禁用；保存失败保留页面草稿和旧运行 generation，不产生半写。
- 启停变化通过既有受控 Core restart 生效；不得在当前 worker 内热卸载后继续复用旧 callable。设置窗口
  原位绑定新 generation，旧状态、响应或 action 不得覆盖新页面。
- WebView DTO 只包含 ID、展示名、版本、支持/启用/required、脱敏 load 状态/reason、声明式字段 schema/
  value 和 action metadata；不得包含 entry、插件根/数据路径、Python repr、异常正文、token、credential、
  任意 callable 或插件未声明的私有数据。

## 3. generation 与 worker 生命周期

- 插件 worker、私有 RPC、加载结果、contribution map、调用任务和状态全部绑定当前 Core generation。
  Core 主解释器不得导入 `plugins.*` 实现或执行插件回调。
- Core readiness 不等待插件完整加载；插件域按 `disabled|starting|ready|degraded|stopping|stopped` 投影。
  required 插件失败不得杀死 Core，但插件域必须明确 degraded，并禁止发布其不完整 contributions。
- 私有 RPC 使用可验证的 generation/token/request identity、有界帧、有界 pending/writer/event queue 和逐类
  deadline。阻塞 initialize/call/event/settings/close、stdout pollution、半帧、EOF、重复/未知响应都必须
  fail closed，不能阻塞 `system.health`、`system.shutdown` 或聊天取消。
- 正常关闭先使全部 contribution identity 失效，再逆序 shutdown 并等待 worker/后代；超时由 owner 终止
  当前 worker，Rust 受控进程树在 Core crash/强杀时兜底。退出后 pipe、thread、event handler、timer、
  socket、文件句柄和后代进程必须归零。

## 4. 能力调用边界

- 插件工具 descriptor 经校验后注册到既有 ToolRegistry，`source=plugin`。当前用户驱动助手阶段忽略
  descriptor 的 `requires_confirmation`，所有模型选中的工具在参数校验后直接执行，不创建
  `PendingToolAction` 或原生二次确认；Action ID 只保留为未来 Agent 插件阶段的未启用基础设施。调用前
  仍须重验 generation、plugin/contribution identity 和参数对象，不允许 WebView 发送执行参数。
- 工具调用结果与错误使用 WP-4-02/03 的有界、脱敏 ToolResult；timeout、取消、worker crash、Playwright
  导航/操作失败和其他插件异常产生稳定 reason code，不向模型或 UI 暴露 traceback、路径、stderr、环境
  或异常正文。Provider 返回的工具调用扩展元数据必须原样保留到工具结果续传，避免 Gemini 等兼容端点
  因丢失 thought signature 拒绝第二轮请求。
- Prompt patch 和 context provider 是不可信数据。patch 数量/长度与 context 请求/片段有界；插件不能
  获得 credential、完整内部 prompt 或未批准的聊天历史。返回 context 继续由宿主覆盖 source/trust/
  cache scope 并执行预算、防注入和敏感度规则。
- 事件只开放规范列出的 app/message/tool 生命周期摘要；payload 使用白名单字段、长度和敏感度限制。
  单 handler 失败/超时只隔离对应插件调用。插件不得发起任意 host event 或获取 Rust/WebView IPC。
- 声明式设置只支持获准字段类型、option/数值边界、restart metadata 和有界 JSON value。load/save/action
  均通过 contribution ID 调用；WebView 和 worker 都必须把只读字段排除在 save/action 输入之外，未知字段
  仍须拒绝，插件回调不得接收客户端回传的只读展示值；设置 action 不是聊天工具且不扩大权限，旧
  generation action 必须拒绝。未来自主 Agent 插件需要权限模型时另行冻结契约。
- `mobile_chat` 等尚未迁移的宿主服务权限不得获得空实现门面。插件应显示
  `degraded/HOST_SERVICE_UNAVAILABLE` 和对应 `unavailable` 能力，且不得启动一个只能返回“服务尚未
  就绪”的外部入口；真正的浏览器/移动桥接生命周期仍由 WP-5-05 实现。

## 5. 数据、日志与安全声明

插件不是安全沙箱，只能安装可信来源。Runtime v2 的 worker 隔离保证可终止性和故障边界，不宣称阻止
恶意插件以当前用户权限访问文件或网络。

测试只可写隔离 assistant root 内的 `data/config/plugins.yaml` 和 `data/plugins/<id>/**`。真实仓库
`data/**` 与顶层 `plugins/**` 不得修改。WP-4L-01 日志只记录 generation、稳定 plugin ID、阶段、耗时、
计数和 reason code；插件路径、entry、私有配置/value、工具参数/结果、context/patch 内容、事件正文、
stderr 和异常正文必须为零。

## 6. 验收与回退

自动门须在 Windows x64、macOS arm64 和 Linux x64 覆盖发现/override/API/permission、多个健康/损坏插件、
ToolRegistry 直执行与延期 Action ID 不激活、prompt/context/event、设置启停/保存/action、受控 restart、Core crash、worker
hang/crash、旧 generation、数据 diff、日志 sentinel 和完整资源回收。既有 Tools/MCP、Core control、聊天、
设置与 Shell lifecycle 回归保持通过。

Windows 实机验收在隔离 assistant root 使用 fixture 插件完成加载、工具无确认直接执行、context/event、
插件禁用/重启、声明式设置和 action，随后强杀 Core 并验证恢复；退出后 worker/后代零残留，日志/DTO 无
路径、entry、私有 value、参数或异常正文。项目负责人明确验收前不得标记 `accepted`。

回退按 WP-4-04 产品提交逆序 revert，先关闭插件设置 feature 和 contribution 注册，再正常退出当前
generation；不得删除或改写 `plugins.yaml`、插件安装目录或插件私有数据。
