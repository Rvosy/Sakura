---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-19
---

# WP-3-04：真实聊天接入已冻结桌宠 UI

## 目标与依赖

本 Work Package 把 WP-3-02 已验收的真实 `chat.send`/`chat.cancel` Core 链接入 WP-3-03、WP-3U-01 和
WP-3U-02 已冻结的产品桌宠 UI，形成第一条用户可操作的真实产品聊天纵向链。当前执行状态只见
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。

依赖为 WP-H-01 accepted；协议、真实 Core、固定 UI、同应用设置宿主、角色表现和 Provider/模型设置均
已由前置 Work Package 提供。本 WP 只接消费者，不改 Python Assistant、Provider、history 或基础
Envelope 语义。

## 唯一产品数据流

```text
main WebView composer
-> Tauri main-window-only chat command
-> generation-scoped CoreHostGateway
-> WP-3-02 RealChatBoundary / AssistantSession / Provider
-> chat.started
-> exactly one chat.completed | chat.failed | chat.cancelled
-> Tauri validates identity and generation, then emits to main WebView
-> frozen chat presentation reducer / typewriter / portrait controller
```

Fake Core 只保留为确定性前端测试和独立回退演示，不得继续作为正常产品数据源，也不得把 `/slow`、
`/error`、`/long`、`/multi`、`/restart` 等演示命令发送给真实 Core。

## Tauri chat bridge 契约

- 只有 `main` WebView 可以发送或取消聊天；settings、未来窗口和未知 label 必须在 Core 写入前拒绝。
- send payload 只能包含非空 `message`。Tauri 生成 operation/cancel identity，调用既有
  `CoreHostGateway`，向 WebView 返回 opaque `operationId`、`cancelHandle` 和当前 generation identity；
  不返回 credential、Provider、history、路径或 Core 私有字段。
- 同一主窗口同时只允许一个 active interaction。等待终态时用户可以编辑下一条草稿，但不能排队或发送
  第二个请求。send 在提交失败时保留输入；被 Gateway 接受后才清除本次输入。
- lifecycle worker 不再静默丢弃已由 Gateway 验证的 chat event。它必须把 allowlisted 事件投影给当前
  main WebView，同时保持 control、Snapshot 和 shutdown 不被 UI 订阅阻塞。
- send response 与 `chat.started` 可以竞态到达；bridge 必须在 Core dispatch 前登记 UI identity，WebView
  也必须按 operation/generation 安全接收，不得依赖“response 总先到”。
- generation 变化、Core 关闭、窗口销毁或 app 退出会立即使旧 cancel handle 和旧事件失效。晚到旧
  generation、未知 operation、重复 started 或第二终态不得改变 UI。
- 取消调用既有 `chat.cancel` 并保持幂等 UI。取消已胜出的 completed/failed 不得伪造 cancelled；关闭与
  restart 继续由既有有界 lifecycle 负责，不在 WebView 建第二个进程所有者。
- Rust 只做窗口授权、identity、generation、事件投影和设置协调，不解析业务 reply 含义，也不保存
  history。CoreHostGateway 的 exact payload 验证与唯一终态仲裁继续是信任边界。

## 冻结 UI 映射

- `chat.started` 映射为 thinking；主按钮在原有尺寸和命中区域内切换为可点击取消的环形旋转条，输入框
  保持可编辑并把 placeholder 切换为“`{角色显示名}正在思考中…`”。气泡只按旧 Qt 节奏每 360ms 循环
  `. → .. → ... → .... → ..... → ...... → .....`，不得显示“正在组织完整回复”或其他解释性文案。
  等待终态期间必须保持已经提交的当前立绘，不得切换固定 thinking 立绘。系统要求减少动态效果时，
  圆环保持静态，气泡固定显示 `...`，取消语义不变。
- `chat.completed` 的完整 `segments` 交给现有 presentation reducer。WebView 只在完整回复到达后运行
  typewriter；每段清空上一段后独立显示，逐段同步对应 portrait，最后一段留在 settled 状态；不得把
  多段拼接为单一气泡文本，也不引入 token streaming、delta 或进度协议。产品界面不提供“立即显示”
  按钮；逐字播放只能由完成、取消、新请求、语言切换或生命周期失效推进。
- segment 的 `text`/`translation`/`tone`/`portrait`/`suppressTts` 只按已冻结 DTO 消费；本 WP 允许
  portrait/tone 驱动现有角色表现，不允许执行 action、Tool 或 TTS。
- Runtime v2 默认显示中文字幕：`zh` 优先使用 `translation`，空值回退 `text`；`ja` 使用 `text`。
  右键菜单复选项可以原子切换该偏好。设置变更事件到达时，当前可见字幕必须在同一前端任务内刷新：
  输入中的当前段清空后按新语言从头重播；settled 或正在回看的段立即完整替换，不等待下一次回复、不回放
  已完成段，也不改变当前立绘。
- 启动问候在字体、初始立绘和窗口 reveal 完成后通过同一可取消 typewriter 播放一次；reveal 前气泡为空，
  reload/focus 不重播，用户发送消息会取消未完成问候。reduced motion 下 reveal 后立即显示完整问候。
- 立绘切换使用解码优先的双层交叉淡入：旧层约 250ms 淡出，新层延迟约 50ms 后以约 250ms 淡入，
  总过渡约 300ms。相同 key 不动画，失败保持旧层，A→B→C 竞态只能提交 C；命中区域只在最终提交后更新。
- `chat.failed` 显示可诊断且脱敏的稳定错误并允许下一次发送。Provider HTTP 失败至少显示状态码，并在
  响应为受支持 JSON 结构时附带经过长度限制与敏感信息过滤的 `error.message`、`error.code`、
  `error.type` 或 `error.status`；不得再只显示 `Provider response was invalid`/`Provider request failed`。
  原始响应体、URL、请求头、凭据、prompt 和无法确认安全的字段不得进入 WebView；无法安全提取时回退为
  含状态码的稳定文案。`retryable` 只影响提示，不在 UI 自动重试。
- `chat.cancelled` 回到 settled/ready 表现，不伪造回复。产品界面不暴露 typewriter skip 控件，Core
  cancel 只用于终止仍在生成的请求。
- 气泡右上角不提供关闭按钮；退出与隐藏继续由右键菜单和托盘负责。气泡右侧恢复上下回复导航：WebView
  在当前窗口会话中按收到顺序保存真实 assistant reply segments，允许跨多轮回看；上一/下一操作立即完整
  显示目标段并同步切换该段 portrait，边界按钮禁用。thinking、typing、reconnecting 或不足两段时导航
  禁用，导航不重放 TTS、不修改持久 history。跨 WebView 重载/应用重启的历史仍由后续 history 能力负责，
  本 WP 不新增 Core history API，也不直接读取 JSONL。回复导航不得参与字幕内容高度计算；相同角色、字体、
  面板宽度和字幕文本在加入导航前后必须得到相同气泡高度。
- 长文本只在气泡内部滚动。正常、thinking、typing、settled、error、cancelled 和 lifecycle 提示必须持续
  使用 WP-3-03 的同一 DOM、样式、命中区域与固定原生窗口几何。
- 输入栏默认是 52px 单行胶囊。出现第二个视觉行后进入锁存展开态，显示文字区和底部工具栏；第三个视觉行
  再向下增加一行，更多文字只在 textarea 内滚动。展开后删回一个视觉行时，必须缩到“一行文字区＋一行
  工具栏”的 100px `expanded-1`，不得直接回到胶囊。空输入时 Shift+Enter 产生的手动换行同样必须触发
  展开；只有 textarea value 完全清空或成功发送后清空时才解除锁存并收回 52px。
- 输入栏顶部固定在气泡下方，所有内容增高只向下发生；输入变化不得移动气泡。子内容、面板矩形和原生
  命中/可见区域仍作为同一次 revision 提交。从 52px 胶囊进入至少两行文字时，输入事件必须同步把可见
  textarea 和外框抬高到“仅容纳当前文字、尚未加入工具栏”的
  staging 高度完成同步布局，并在下一次绘制前直接以 260ms 插值打开到最终工具栏布局，不得驻留静止帧；
  文字、外框高度和工具按钮必须使用同一归一化进度，不得让文字提前到位或在 staging 外闪现。收回不经过
  staging，直接平滑收至 52px 胶囊。
  WebView 内容和平台输入玻璃必须使用相同 staging 和曲线，结束时不得留下 CSS 几何尾帧。输入事件任务
  必须先触发轻量原生扩展：Windows 在 WebView 动画前放宽旧裁剪，平台玻璃直接从 staging 开始；完整
  `apply_pet_layout` 只提交最终精确区域且不得重启动画。轻量启动失败时允许完整提交直接启动扩展作为回退；
  只有收缩在确认后由 WebView 显式启动对应 revision。禁止使用固定延迟推测 ack 耗时。收缩时精确窗口裁剪必须覆盖完整位移动画，
  动画结束后一次收敛到目标区域；不得先裁掉 WebView 外框，也不得让原生玻璃越过外框或在外框到位后继续
  形成光晕。快速变化只允许最新 revision 胜出，发送/取消与附件按钮在全过程中必须完整可见、命中与视觉
  位置一致。
- Enter 发送、Shift+Enter 换行、IME composition、焦点恢复、reduced motion 和拖动行为沿用已验收
  语义；本 WP 不借真实聊天重新设计视觉或交互。
- 鼠标右键打开产品菜单不得自动聚焦第一项或留下持续深色底；只有键盘触发打开时聚焦首个可用项并启用
  `focus-visible` 背景。checked 状态只由复选指示器表达，不把普通“隐藏至托盘”渲染成选中项。

## 设置切片

本 WP 新开放 `chat.presentation_timing` 和 `chat.subtitle_language`。前者只含
`subtitle_typing_interval_ms` 和 `reply_segment_pause_ms`；后者只含 `subtitle_language: "zh" | "ja"`，
由主窗口右键菜单切换并持久化。精确持久化、失败原子性、重新打开和回退契约见
[`settings-incremental-migration.md`](settings-incremental-migration.md) 第 7 节。

已迁移的 `appearance.character` 继续提供字体与主题。自动隐藏、气泡高度、输入栏偏移、自由布局、发送
键行为和其他旧控件不得开放；未迁移 feature 继续失败安全禁用并显示稳定原因。

## 数据、安全与隐私

- 正常聊天唯一业务写入仍是 WP-3-02 的角色级 append-only history；本 WP 不改变 schema、窗口大小、
  rotate/repair 或失败降级语义，也不清理、恢复、截断或改写既有 history。
- 两个聊天设置 feature 只写 Runtime v2 `ui.json` 的批准字段。测试和故障注入必须使用隔离临时
  app root；仓库真实 `data/**` 不得被测试、脚本或验收改写。
- WebView 事件不得包含 credential、Authorization、Provider URL、原始 Provider 响应体、history、绝对
  路径、prompt、日志正文或环境变量。错误只显示 Core 已投影的稳定 code/message/retryable；Provider
  message/code/type/status 必须先经过 allowlist、长度上限与敏感模式过滤。
- 不新增网络、文件、shell 或窗口权限，不放宽 CSP，不新增依赖或 dependency manifest/lock 变化。

## 实施白名单与禁止范围

精确机器可读范围见 `harness/tasks/WP-3-04.json`。允许修改仅限：

- `desktop/frontend/app.js`、`desktop/frontend/chat/**`、`desktop/frontend/pet/**`：真实 chat client、
  reducer/typewriter/portrait 接线及其窄回归。
- `desktop/frontend/index.html`、`desktop/frontend/styles.css`：只启用既有中文字幕菜单项，并把既有双层
  立绘过渡改为确定性交叉淡入，以及移除关闭按钮、加入右侧回复导航和修正菜单焦点样式；固定窗口几何
  与角色主题视觉语言不变。
- `desktop/frontend/settings/**`：只开放 `chat.presentation_timing` 及保存/回读/失败恢复。
- `desktop/src-tauri/src/` 中列名允许的 chat bridge、lifecycle、Gateway、product shell、`ui.json` 共享
  repository 与聊天设置模块；不扩大通用 IPC 或窗口系统。
- 相关 frontend/Rust/真实桌面 acceptance、隔离 fixture、Runtime v2 platform workflow、规范、记录、
  userdoc 和 changelog。

明确禁止：

- 除 `app/core_host/real_chat.py` 中窄 Provider 错误公开投影外，禁止 Python Assistant/Core/Provider/
  history 业务改动、legacy Qt UI 与两个入口；该例外不得改变请求、重试、Provider 选择或 history 语义。
- TTS、Tools、Action 确认、Memory、MCP、插件、截图/视觉输入、主动互动、提醒/任务、历史窗口、角色
  切换、Studio、导入导出、通用 Operation/priority/resource token、streaming/progress/delta。
- 修改固定 DOM 层级、布局 contract、窗口几何、角色包、真实数据、runtime、第三方或 `tools/mcp`。
- 新增依赖，修改 Cargo/npm/Python manifest 或 lockfile，删除/重命名既有测试，降低既有三平台门禁。

## 自动验收矩阵

| 门类 | 必测情形 | 核心断言 |
|---|---|---|
| send | 正常、空白、超限、重复发送、提交失败 | main-only；accepted 后清输入；失败保留；单 active interaction |
| event race | started 早于/晚于 send response、未知 operation、重复 started/terminal | 不丢首事件；按 identity 接受；唯一 UI 终态 |
| terminal | 多段/长文本、Provider 400/429/5xx、坏响应、取消、完成/取消竞态 | exact 投影；HTTP 状态与安全诊断可见；私有字段脱敏；内部滚动 |
| generation | restart、旧 event、旧 cancel handle、窗口 reload/close | 旧代不改变 UI；新代重新绑定；资源与 timer 归零 |
| portrait | 有效/缺失 portrait、tone、快速终态与 late load | 复用安全资源映射；失败 fallback；旧代资源丢弃 |
| 聊天设置 | timing 与字幕语言 get/save/reopen、输入/settled/回看切换、坏 JSON、写失败 | 分 feature 原子；当前可见字幕立即刷新；失败保留旧值/勾选态 |
| 分段/启动 | 启动 reveal、多段回复、切换语言、reduced motion | 问候只播一次；逐段清屏；无 skip 控件；不混合语言 |
| 立绘过渡 | 解码失败、同 key、A→B→C、旧 generation、冷热缓存 | 旧层不断帧；只提交最新请求；无二次淡出；命中延后提交 |
| 回复导航 | 单段/多段、跨多轮、上下边界、生成中、语言切换、portrait 失败、加入前后高度 | 当前会话有序；目标文字与立绘原子联动；不播 TTS、不写 history；不改变同文本气泡高度 |
| 自适应布局 | 胶囊→两行展开→三行→expanded-1→清空、软折行、快速交替、native ack 延迟 | 展开锁存；仅 value 为空收回胶囊；气泡零位移；输入顶部固定；收缩外框/玻璃/裁剪同点结束且无光晕 |
| 冻结 UI | ready/thinking/typing/error/cancel、IME、长文本、reduced motion | 无关闭/“立即显示”；右侧导航不改变固定几何；鼠标菜单首项无持续深色 |
| 安全 | 非 main 窗口、额外 payload、secret-shaped terminal/error | Core 写前拒绝；无 secret/path/history 泄漏；不放宽权限 |

任务级 required profiles 固定为 `docs`、`smoke`、`core-host`、`runtime-v2-shell`、`python-full`。实现还须
执行 locked Rust 全量测试、fmt/diff check，以及同一候选 SHA 的 Windows/macOS/Linux 公共 workflow；
自动测试使用确定性 local Provider 和隔离根，不访问公网或真实用户 Provider。

## 人工验收与退出条件

Windows 真实 Tauri/WebView2 使用已有开发配置完成正常回复、错误与取消；验证启动问候、中文字幕开关
即时刷新、多段逐段显示、当前会话上下回看及立绘联动、点号等待动效、角色名思考 placeholder、等待立绘
保持、HTTP 400/429 诊断、中文 IME、Enter/Shift+Enter、长文本、无关闭/“立即显示”控件、鼠标右键菜单
首项不持续深色、portrait/tone、快速
立绘竞态、连续第二轮、气泡高度基线、输入栏单行→两行展开→三行→清空时气泡零位移且按钮无裁切/撕裂，以及窗口
关闭。Sakura 与 N.A.V.I.、100%/
150% DPI 下固定窗口包络、气泡、输入和立绘锚点不得漂移，正常产品界面不得出现 Fake Core 命令或测试
控件。公共候选须取得同一 SHA 三平台门，无 P0/P1、凭据泄漏、重复终态或资源残留。

自动门通过只允许进入 `stabilizing` 并等待负责人验收；Agent 不代填人工结果，也不自行标记
`accepted`。

## 回退

回退前停止新 send，取消并排水活动 operation，等待 Core/Router/event bridge/typewriter/portrait timer
按既有 deadline 归零。随后禁用真实 chat bridge、`chat.presentation_timing` 与
`chat.subtitle_language`，让主 UI 切回 WP-3-03
Fake Core 演示路径，保留 WP-3-02 headless chat、WP-3U-02 角色表现和 WP-3S-01 Provider 设置。

回退不得删除、恢复、截断或重写 chat history、`api.yaml` 或 `ui.json`；旧版本只需忽略新增聊天设置字段。
