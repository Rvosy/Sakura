# WP-3-03：固定产品 UI 与真实角色表现基线

```text
状态：active
首次激活日期：2026-07-26
产品方向纠正日期：2026-07-26
旧版视觉基线恢复日期：2026-07-27
前置依赖：WP-3-02 accepted
唯一产品边界：最终桌宠 UI 外壳 + 当前真实角色的只读表现资源 + 确定性进程内 Fake Core
回退边界：回退本 WP 的产品 UI 修正、角色表现资源接入和测试，保留 Phase 1A 窗口技术门、Core lifecycle 与 WP-3-02 headless chat
```

## 产品决策

Runtime v2 从本 WP 起执行“UI 先固定、功能后迁移”。桌宠的立绘、气泡、输入框和状态提示先形成
最终产品外壳，后续 WP 只能向该外壳接入真实数据与功能，不能在迁移聊天、TTS、Tools 等能力时顺便
重新设计主界面。

本 WP 的正常画面必须使用当前真实角色包。Fake Core 只负责确定性地驱动聊天表现状态，不拥有角色名、
初始消息、主题或立绘资源。自绘测试 SVG 只能作为资源加载失败 fallback，不得成为默认画面、截图基线
或 accepted 证据。

## 历史候选与重新激活原因

2026-07-26 的历史候选 `27bad4d5` 已通过 frontend、Python、Rust 自动矩阵和 Windows Tauri WebView
冒烟，但包含以下与最终产品要求冲突的设计：

- 默认使用 WebView 自带测试 SVG，不读取真实角色包。
- composer 默认收起，通过“聊”按钮打开。
- 气泡会自动隐藏，并在 idle/bubble/composer/expanded 之间改变窗口几何。
- 产品界面常驻主题、Fake restart、visibility probe、关闭等功能栏和 geometry readout。

这些不是普通 P2/P3 视觉缺陷，而是产品交互方向变化。历史技术证据继续保留，但不能用于接受修订后的
WP-3-03；状态由 `stabilizing` 退回 `active`。

## 冻结后的桌宠组合

桌宠使用一个固定透明原生窗口包络：

```text
固定透明桌宠窗口
├─ 常驻对话气泡
│  ├─ 角色名/状态
│  ├─ 回复内容与内部有界滚动
│  └─ 打字机“立即显示”动作（仅在需要时出现）
├─ 常驻输入框
│  └─ 发送 / 取消共用主按钮
└─ 当前真实角色立绘
   └─ 固定桌面锚点与表情交叉淡入
```

硬约束：

- 气泡和输入框在桌宠可见期间始终存在，不提供产品级打开、收起或自动隐藏。
- 组合关系沿用重构前桌宠：立绘水平居中，气泡与输入栏同宽并居中覆盖在角色中下方；不得改成
  立绘与聊天区左右分栏。默认气泡宽 640、高 128，输入栏高 52，间距 10。
- 气泡沿用角色主题的浅色半透明卡片、20px 圆角、左上角色名与正文层级；输入栏沿用独立胶囊外框、
  内层半透明输入胶囊和主题色发送按钮。输入背景模糊不属于本 WP 的接受门禁，可先使用普通半透明背景。
- 所有聊天与生命周期状态使用同一窗口大小、立绘锚点、气泡矩形和输入框矩形。
- 长文本只在气泡内部滚动，不能扩大原生窗口、移动立绘或切换布局状态。
- 输入框可在等待回复期间继续编辑；主按钮在发送与取消之间切换。
- 正常产品 DOM 不存在 composer toggle、常驻功能栏、`FAKE CORE` 标签或 geometry readout。
- Fake restart、visibility probe、场景选择等只能位于默认隐藏且 release 不可见的验收入口。
- 视觉保持克制：只允许立绘交叉淡入、轻量状态反馈和打字机；`prefers-reduced-motion` 下关闭非必要动画。
- 原生窗口必须隐藏创建，由 `WindowInteractionBackend` 依次建立无边框样式、bounds 和 hit region 后才能
  首次显示；Windows 必须在 `SetWindowRgn` 前回读确认 caption/frame 位已清除，不得用清空标题掩盖非客户区。
- 正文按实际脚本选择字体：中文沿用微软雅黑优先，日文沿用旧版 Meiryo 圆润字体优先，拉丁文使用
  Segoe UI；正文只使用真实 regular 字重，角色名、输入与按钮只使用真实 bold 字重，不使用 500/650
  等会在系统字体间产生不一致映射的中间值。

布局契约必须为后续外观设置保留 `controlPanelWidth`、`bubbleHeight`、`verticalOffset` 和
`inputBarOffset` 四个经范围归一化的参数。WP-3-03 只消费契约默认值，不提前读写设置；WP-3U-02 接线时
复用这些参数，不得再次改变气泡、输入栏与立绘的居中覆盖关系。

## 真实角色表现契约

本 WP 只读取当前角色的公开表现数据，不保存角色选择或配置。允许为当前真实消费者增加一个窄、无 Qt
的 `CharacterPresentation` DTO；不得扩展为通用文件资源平台。

最小字段：

```text
schemaVersion
generationId
characterId
displayName
initialMessage
themeTokens
defaultPortraitKey
portraitKeys
portraitResourceIds
```

资源约束：

- Python Core 是当前角色身份与公开表现摘要的真相源。
- WebView 只获得受控逻辑资源 ID 或同源资源 URL，不能获得裸绝对路径。
- 资源解析必须校验角色根、逻辑键、路径穿越、符号链接逃逸、文件类型、大小和当前 generation。
- `characters/**` 在本 WP 只读；不得复制后手工修改第二份角色立绘。
- 允许构建时机械暂存真实资源，但源文件必须保持单一真相源且有一致性测试。
- 至少使用 Sakura 与 N.A.V.I 两个真实角色包验证不同宽高比；正常运行只展示当前角色，不提前实现角色切换。
- 资源加载、decode 或 generation 校验失败时使用稳定 fallback，并在 UI 中保留可恢复错误信息；不得崩溃或显示旧角色资源。

## 所有权与状态机

- `fake-chat-core.js` 是测试 generation、operation identity、唯一终态和确定性场景计时器的唯一所有者。
  支持 normal、slow、error、cancel 和 restart；dispose/restart 必须清除计时器并丢弃旧 generation 回调。
- `chat-presentation.js` 只接受当前 generation/operation 的 started/completed/failed/cancelled 事件，
  投影为 ready、thinking、typing、settled、error、reconnecting 表现；不保存业务 history。
- `typewriter.js` 只消费完整 `segments`，不消费 token/delta。skip 立即展示完整回复并只结束动画，
  不调用 Fake Core cancel；新回复、cancel、restart 和 dispose 使旧 timer callback 失效。
- `portrait-controller.js` 只负责当前角色受控资源的预加载、generation token、decode/load failure fallback
  和简单交叉淡入；portrait 只能映射到当前角色 manifest 中的逻辑键。
- Enter 发送与 Shift+Enter 换行继续复用既有 IME composition 门禁。等待 Fake Core 终态时主按钮是
  “取消”；完整终态进入打字机后，“立即显示”只控制动画。
- 表现状态与布局状态彻底分离；本 WP 不再拥有 idle/bubble/composer/expanded 几何状态机。

## 确定性场景

| 输入 | Fake Core 行为 | 必须可见的结果 |
|---|---|---|
| 普通文本 | 短延迟 completed | thinking → typing → settled；窗口几何不变 |
| `/slow` | 长延迟 completed | 等待期间输入框仍可编辑；可取消、拖动和关闭 |
| `/error` | retryable failed | 气泡内稳定错误文案；可重新发送；几何不变 |
| `/long` | completed 长段 | 气泡内部有界滚动；窗口、立绘和输入框位置不变 |
| `/multi` | completed 多段 | 完整回复逐字显示，并使用真实角色 portrait key 切换立绘 |
| `/restart` | 当前 operation cancelled，Core crashed → restarting → ready | 旧 generation 晚回调和旧角色资源丢弃；重连后可再次发送 |

这些命令只存在于 WP-3-03 Fake Core，不进入 WP-3-04 的真实产品协议。

## 实施白名单与明确禁止

允许修改：

- `desktop/frontend/index.html`、`desktop/frontend/app.js`、`desktop/frontend/styles.css`。
- `desktop/frontend/pet/**`、`desktop/frontend/chat/**`、`desktop/frontend/core/**`、`desktop/frontend/tests/**`。
- `desktop/frontend/assets/**`，但只允许删除/隔离默认测试资产、添加 fallback 或构建期机械产物规则。
- `desktop/src-tauri/src/**` 中当前角色表现资源解析、固定窗口几何和命中区域所需的窄模块。
- `desktop/src-tauri/tauri.conf.json` 中受控图片来源和固定窗口所需的最小变更。
- `app/core_host/**` 中当前角色公开表现 DTO 所需的无 Qt 窄扩展。
- 与上述范围直接相关的 frontend、Python、Rust、platform workflow 测试和规范文档。
- `characters/**` 只读取证和测试输入。

明确禁止：

- 真实 `chat.send/chat.cancel` Gateway 接线、真实 Provider 请求或 Python Assistant 语义修改。
- 右键菜单、设置窗口和配置保存；它们属于 WP-3U-01/02。
- 修改、重编码、删除或覆盖 `characters/**` 角色资源。
- WebView 直接读取任意本地路径或接收裸绝对路径。
- 通用 Operation、通用 resource token 平台、streaming、TTS、Tools/确认、Memory、MCP、插件、截图、
  主动互动、Studio、Live2D/Canvas、高级动画引擎和新增前端框架依赖。
- `data/**`、`runtime/**`、第三方目录或用户数据写入。

## 自动验收

自动测试必须覆盖：

- 固定布局契约只有一个产品窗口包络，所有表现状态返回相同窗口/锚点/气泡/输入矩形。
- 默认布局逐像素验证居中覆盖关系，并验证预留宽度、高度和偏移参数的归一化、同宽同轴与窗口边界。
- markup/source 不包含 composer toggle、state rail、bubble auto-hide 产品接线、`FAKE CORE` 或 geometry readout。
- 气泡和输入框在 ready/thinking/typing/settled/error/reconnecting 下始终存在且可命中。
- 长文本只改变内部 scroll state，不触发原生 resize。
- Fake Core 五类场景、唯一终态、旧 generation/operation 丢弃、取消竞态和 restart。
- skip 与 Core cancel 分离、打字机晚 callback、portrait 快速切换/失败和 reduced motion。
- Sakura 与 N.A.V.I 真实 manifest、默认立绘、全部逻辑键、资源 allowlist、非法 key、路径穿越、
  symlink escape、错误 MIME/尺寸和旧 generation 资源拒绝。
- theme 非法值 fallback、IME、hit-region、drag、focus、close 和 lifecycle 回归。
- CSP/source boundary 不开放网络图片、任意文件或媒体来源。

## 真实应用与视觉门禁

Windows 候选必须在真实 Tauri WebView 中验证：

- 当前角色真实默认立绘、初始消息和主题；Sakura 与 N.A.V.I 各留一组验收截图。
- 默认、thinking、typing、长文本、error、cancel、reconnecting 和多段立绘切换。
- 气泡/输入框常驻，窗口包络与立绘桌面锚点在全部场景中不变。
- 中文 IME、连续输入、Enter/Shift+Enter、焦点恢复和候选框位置。
- 100%/150% DPI、真实角色无过度缩小、裁切、白闪、遮挡或尺寸跳变。
- reduced motion、动画期间取消/关闭、拖动和 Fake Core restart。
- 正常画面没有测试标签、测试按钮、功能切换栏或几何读数。

macOS 与 Linux 的真实 WebView、IME、多屏和 compositor 体验仍是 WP-7-02 的发布硬门禁，不因本 WP
提前宣称完成；公共布局、资源和测试契约必须持续在三平台构建通过。

## 当前候选证据（2026-07-26）

当前候选已完成生产实现和本地自动矩阵，但尚未完成全部真实候选门，因此状态保持 `active`：

- 正常产品冷启动已从真实 Core Snapshot 读取当前 N.A.V.I. 的 `display_name`、日文
  `initial_message`、绿色主题、默认立绘和全部 expression mapping；WebView 只接收
  `sakura-character.localhost` 同源 URL 与 opaque resource ID。一次正常模式降级暴露并关闭了两个真实缺陷：
  protocol minor 2 最小 Snapshot 漏传 `characterPresentation`，以及敏感字段扫描器误拒绝公开
  `themeTokens`。
- debug-only 验收入口分别加载 Sakura 与 N.A.V.I. 的真实包；两者默认 PNG 的不同宽高比、主题、
  初始消息和表情切换均在同一 816×680 透明窗口中可见。正常模式没有角色选择入口，只展示当前角色。
- Windows 100% DPI 候选已观察 normal、long、multi、error、restart、slow cancel、中文 Unicode 输入、
  reduced motion 和关闭；长文本只出现气泡内部滚动条，气泡、输入框、立绘锚点和窗口包络未改变；
  正常产品 accessibility tree 未暴露隐藏验收控件。
- frontend 全量以 `node --test tests/*.test.js` 执行，62 passed；本机 `npm test` 启动器因用户级
  `%APPDATA%/npm` 缺少 `npm-cli.js` 无法启动，但 package script 对应的同一 Node test 命令已完整通过。
- Core Host 扩大 Python 矩阵 214 passed；`cargo test --locked -- --test-threads=1` 为
  185 passed、23 ignored；`cargo fmt --check`、`cargo build --locked`、`git diff --check` 均通过。
  Rust 资源矩阵覆盖两个真实 manifest、全部 portrait key、非法 key、路径穿越、symlink escape、错误扩展/
  MIME、过大文件、PNG decode/header、资源变化和旧 generation 拒绝；固定布局、DPI 缩放、命中、拖动、
  lifecycle、关闭与进程树回归通过。

尚未满足的真实候选门：

- 未在 Windows 系统级 150% DPI 下完成真实 Tauri 候选验收；仅给 WebView 强制 1.5 device scale 会使
  浏览器内容与原生窗口缩放脱节，不能作为有效 150% DPI 证据。
- 自动测试已覆盖 IME composition/Enter/Shift+Enter，真实 WebView 已输入中文 Unicode 文本，但尚未完成
  带候选框的真实中文输入法组合过程与候选框位置验收。
- Computer Use 的合成拖动没有产生可确认的窗口位移；Rust 原生拖动和 DPI/命中回归虽通过，仍需人工
  鼠标拖动候选验证。
- 项目负责人尚未完成 Sakura/N.A.V.I. 实机视觉确认；因此本候选不得进入 `stabilizing`，更不得标记
  `accepted`。

## 状态迁移与回退

实现和自动矩阵完成后迁为 `stabilizing`。只有真实角色资源、固定组合布局、自动矩阵和 Windows 实机
门禁通过，无 P0/P1，且改动/非目标/回退证据完整时才能迁为 `accepted`。

回退时先关闭窗口并清除 Fake Core/typewriter/portrait/resource timer 与 pending load，再逆序回退本次
产品 UI 修正。允许回到历史 Fake Core 候选用于诊断，但它不能被重新标记 accepted；不得删除、恢复或
改写角色资源、history 或任何用户数据。
