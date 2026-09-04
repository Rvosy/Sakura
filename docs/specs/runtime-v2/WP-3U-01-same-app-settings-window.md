---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-3U-01：同一 Tauri App 的右键菜单与设置窗口宿主

```text
执行状态：见 `docs/plans/runtime-v2/work-packages.md` 第 2 节唯一状态源
激活日期：2026-07-27
进入稳定化日期：2026-07-27
前置依赖：WP-3-03 accepted
主要结果：用桌宠右键菜单打开同一 Tauri App 内唯一的普通设置窗口
数据边界：本 WP 不保存业务或用户配置
回退边界：移除菜单和 settings 窗口宿主，保留固定桌宠 UI、真实角色只读表现和旧独立设置工具
```

## 目标

在真实聊天接入产品 UI 前先冻结最终桌面交互入口：用户右键桌宠得到与 legacy Qt 语义相近的菜单，
选择“设置”后打开新版主程序拥有的 `settings` WebViewWindow。设置不再以独立 `sakura-settings`
Tauri App 作为 Runtime v2 产品路径，也不创建第二生命周期根。

同一 App 宿主、Rust 窗口生命周期所有权、独立设置进程取舍和 canonical frontend 选择的架构原因见
[`ADR-0006`](../../adr/0006-same-app-settings-host.md)。本文保留窗口、菜单、capability、关闭和验收契约。

## 激活与实现记录（2026-07-27）

- 前置 WP-3-03 已由项目负责人验收并标记 `accepted`，本 WP 随即激活。
- 单一规范源冻结为方案 1：`desktop/frontend/settings/**` 是 canonical source；legacy
  `tools/settings-tauri` 直接从该目录构建，不保留第二份完整资产。
- Rust 已拥有原生产品菜单、封闭 menu item allowlist、唯一 `settings` 窗口、重复打开聚焦、
  CloseRequested/放弃确认和 5 秒有界主应用退出协调。
- Windows 原生命中区域在立绘解码后按 `object-fit: contain` 的真实外框与 PNG alpha 蒙版收紧；
  图片外侧留白及 PNG 画布内部全透明像素均不再拦截鼠标，气泡、输入框、按钮与实际可见立绘仍保持
  可交互和可右键。
- capability manifest 当前不开放任何可写 section；未迁移页面稳定禁用，前端不读取或保存用户配置。
- 本地 frontend 与 Rust 全量自动测试通过，进入 `stabilizing`；Windows 真实菜单位置、100%/150% DPI、
  最小化/聚焦、中文 IME、重复打开和退出仍需形成候选验收证据后才能 `accepted`。

本 WP 只迁移窗口宿主、菜单入口、设置前端外壳和能力门控。角色/外观配置的真实读取与保存属于
WP-3U-02；其他设置功能按 `docs/specs/runtime-v2/settings-incremental-migration.md` 的 feature 级顺序跟随
对应能力 WP，不能集中恢复旧 HostRpc 或无后端开放页面。

## 现有实现的复用裁定

允许复用：

- `tools/settings-tauri/frontend/index.html` 的页面结构与可访问语义。
- `tools/settings-tauri/frontend/styles.css` 的视觉样式和设计 token。
- `tools/settings-tauri/frontend/settings.js` 的页面路由、表单交互、dirty tracking、未保存确认和通知 UI。
- 已有设置窗口焦点、最小化、关闭确认和重复打开测试场景。

不得直接复用为 Runtime v2 宿主：

- `tools/settings-tauri/src-tauri/src/lib.rs` 的 stdin/stdout HostRpc、进程级 `AppState` 和 `app.exit(0)` 关窗语义。
- `app/ui/tauri_settings.py` 中依赖 PySide6 的 QProcess、QObject、QThread、QTimer、Signal/Slot 和 QWidget 适配层。
- legacy Qt 对桌宠置顶压低、外部设置进程检测和 stdout marker 的生命周期所有权。

旧独立设置工具在 Legacy Qt 迁移参考期保留，不因本 WP 删除，也不构成用户回退；Runtime v2 与参考
实现应共享前端和纯配置契约的规范源，但保留各自窗口宿主。

## 窗口架构

```text
一个 Sakura Tauri App
├─ pet WebViewWindow
│  └─ 右键事件 -> Rust ProductMenuController
├─ settings WebViewWindow（最多一个）
└─ Python Core（仍由同一 Supervisor 管理）
```

Rust 是窗口和菜单生命周期的唯一所有者：

- `pet` 右键只提交“请求显示产品菜单”和必要的屏幕位置，不提交菜单业务参数。
- Rust 构建产品菜单并处理 menu item ID；WebView 不实现伪原生浮层菜单。
- `settings` 不存在时创建；已存在时 unminimize、show、focus，不创建第二个实例。
- 设置窗口使用有装饰的普通窗口，可最小化、有任务栏入口、默认不置顶。
- 设置 WebView 必须始终填满原生窗口内容区；最大化、恢复和拖动缩放不得保留旧 viewport 或露出原生背景。
- 关闭设置只销毁或隐藏 `settings`，不得退出 App、关闭桌宠或触发 Core shutdown。
- App 退出时先请求设置关闭/丢弃确认，再按现有受控路径关闭 Core 和全部窗口。
- Core 崩溃时设置窗口继续存在，并只按能力清单显示可用/不可用状态。

第一版右键菜单至少包含：

```text
显示/隐藏桌宠（按当前状态二选一）
设置…
退出
```

历史和运行日志等条目只在相应所有者和产品语义已经迁移时加入；不得放置点击后无真实效果的菜单项。
“保持置顶”按本文 2026-08-29 后续决定实现。“完整访问权限”不属于本版本产品能力，右键菜单不显示也
不保留禁用占位。
菜单和托盘以后可以共享 action model，但本 WP 不提前实现完整托盘能力。

## 设置前端单一规范源

设置前端必须迁移为一份 canonical source。允许选择以下任一最小方案并在激活记录中冻结：

1. 以 `desktop/frontend/settings/**` 为规范源，legacy 独立设置构建只机械暂存同一资源。
2. 抽出不依赖具体 Tauri App 的共享 `frontend/settings/**`，两个宿主均从该目录构建。

禁止手工维护两份完整 `index.html/settings.js/styles.css`。机械暂存必须可重复、不会改写源文件，并有
hash/source freshness 测试。

设置前端在启动时读取 `SettingsCapabilityManifest`：

```text
schemaVersion
windowGeneration
availableSections
readOnlySections
unavailableReasons
```

本 WP 只要求设置外壳成功显示。未迁移页面必须隐藏，或禁用并显示稳定原因；不能让用户提交最终必然
失败的表单。密钥和完整私密配置不得出现在 capability manifest、日志或通用 Snapshot。

## 关闭与未保存语义

- 没有未保存草稿时，关闭设置窗口立即完成。
- 有未保存草稿时，由设置前端显示确认；Rust `CloseRequested` 必须 prevent close，直到收到明确结果。
- 用户取消关闭时窗口保持可见并恢复焦点。
- 用户确认放弃时只关闭 settings 窗口。
- 主应用退出不能无限等待设置确认；应使用明确、可测试的有界退出策略，不得绕过 Core 清理。
- WebView 崩溃或设置窗口创建失败时，桌宠和 Core 继续运行，右键菜单显示可恢复错误并允许重试。

## 宿主与数据边界

设置窗口属于主 Tauri App，不启动 `sakura-settings` 子进程或复用旧 stdio RPC。宿主负责窗口和调用协调，
业务配置由对应 Core 领域处理；WebView 不直接写用户数据。尚未完成真实数据链的页面不能以占位控件冒充可用。

## 验收门禁

自动测试：

- 右键在桌宠全部可见命中区域触发，包括立绘、气泡、输入框和按钮区域；透明空白继续穿透，且不破坏左键拖动和输入框选择。
- 菜单 item ID allowlist，未知/伪造 ID 无效果。
- settings create/focus/close 状态机；快速重复打开只产生一个窗口。
- CloseRequested、取消关闭、确认放弃、WebView 创建失败和崩溃恢复。
- settings 关闭不触发 App exit 或 Core shutdown；App exit 仍完成 Core/窗口清理。
- capability manifest 隐藏/禁用未迁移页面，不包含凭据。
- canonical source freshness，两个设置宿主不出现手工资产漂移。

Windows 真实应用至少验证右键菜单位置、100%/150% DPI、设置窗口创建/最小化/聚焦、中文 IME、
未保存关闭确认、重复打开和主程序退出。公共 Rust/frontend 构建必须在 Windows/macOS/Linux 通过；
macOS 还必须验证设置窗口默认、最大化、恢复和拖动缩放时 WebView bounds 始终等于原生内容区；Linux
真实菜单和 compositor 门保留 WP-7-02。

## 状态与回退

变更按实际影响验证窗口生命周期、焦点/IME 和关闭行为，公共代码由 CI 验证三平台构建。
人工与自动结果分别记录，旧工作包依赖状态不构成开发前置审批。

回退时移除 Runtime v2 菜单 action、settings window 注册、capability shell 和资源暂存接线，保留
WP-3-03 固定产品 UI。旧独立设置工具和 legacy Qt 入口保持可用；不得删除或恢复用户配置。

## 接受记录（2026-07-27）

> 本节保留当时的验证事实、风险接受与回退方案，不是当前开发步骤。

- 自动测试：实现与稳定化提交依次通过 frontend 全量测试（最终 70 passed）、Runtime Rust 全量测试
  （最终 195 passed、23 ignored）、Harness smoke（2/2）、locked debug build、`cargo fmt --check`、
  `git diff --check`；legacy settings 宿主 `cargo check --locked` 通过，canonical frontend freshness 和
  capability/secret 边界测试通过。最终候选 `7a58bdc0a` 的 Runtime v2 platform foundation run
  `30236383550` 在 Windows x64、macOS arm64、Linux x64 全绿，三平台均执行 frontend 测试和 native
  Tauri build；Test run `30236383605` 的 Unit/UI jobs 全绿。
- Windows 手动验收：项目负责人已确认真实右键菜单位置和操作、设置窗口创建/最小化/重新聚焦、
  中文 IME、未保存关闭确认、快速重复打开保持单实例、关闭设置不退出桌宠或 Core，以及主程序正常
  退出和资源清理全部通过。
- DPI 设备证据：本轮明确未执行 100%/150% DPI 人工验收，不记录为通过，也没有观察到可复现产品
  缺陷。项目负责人按 G-008 明确接受这项非失败型设备证据风险；真实 Tauri WebView/菜单复验点已
  登记到 WP-7-02。若后续发现可复现且可归因于本候选实现的 DPI 缺陷，必须重新打开 WP-3U-01。
- 数据与安全：本 WP 未开放任何可写设置 section，未读取或保存角色、Provider、TTS、Memory、MCP、
  插件、凭据或其他用户配置；设置 WebView 没有直接写入 `data/**`。未发现 P0/P1、退出条件缺陷、
  凭据泄露或第二生命周期根。
- 已知风险：除上述 Windows DPI 设备证据外，macOS/Linux 真实菜单、焦点、IME 和 compositor 设备
  体验仍按既定范围留在 WP-7-02；公共 Rust/frontend 构建证据已经由同一候选三平台 CI 通过。
- 回退：依次 revert 本 WP 的 Windows 稳定化修复和 `6058bac06`，移除 Runtime v2 product menu、
  settings window、capability shell 与 canonical frontend 接线，恢复 WP-3-03 固定产品 UI；保留旧
  独立设置工具和 legacy Qt 入口，不删除、恢复或改写用户配置。

## 2026-07-29 桌宠右键菜单表现层后续决定

- 桌宠右键菜单改由主 WebView 渲染角色主题自绘样式；Rust 仍是菜单能力清单、动作白名单、窗口生命周期
  和原生命中区域的唯一所有者。此调整取代上文“WebView 不实现伪原生浮层菜单”的表现层限制，不改变
  已接受的设置单实例、托盘恢复和受控退出语义。
- 系统托盘菜单继续使用 Tauri 原生菜单。桌宠菜单只允许执行显示/隐藏、设置和退出；中文字幕、历史与
  运行日志仅以灰色禁用项表达迁移状态，不发送命令，也不作为对应能力已完成的证据。“完整访问
  权限”自 2026-08-29 起退出本版本右键菜单，不保留占位。
- 自绘菜单打开期间 Rust 临时恢复整窗命中区域，关闭后按最新布局、DPI、立绘缩放和 alpha mask 恢复
  精确区域；恢复失败继续采用整窗可交互的安全回退，不能留下不可点击窗口。
- 菜单打开后，气泡逐字、气泡或输入栏显隐、输入栏高度变化仍可提交布局。Rust 必须保留菜单覆盖层，
  同时更新用于关闭菜单的基础布局快照；普通布局动画不得把原生 region 收紧到菜单下方的桌宠区域。
- CAP-022 保持 `planned`，后续仍由 WP-5-04 补齐快捷键、开机启动和三平台其余桌面能力。

## 2026-08-29 桌宠保持置顶后续决定

- “保持置顶”成为主题自绘桌宠菜单中的真实复选动作，默认关闭；勾选状态只取自 Rust 已提交状态。
- Rust 只对 `main` 桌宠窗口调用原生置顶 API。设置、历史窗口保持普通窗口层级；截图选择窗口继续使用
  自己的临时置顶语义，不与该偏好联动。
- 偏好保存到共享 `config/ui.json` 的 `settings.always_on_top`，启动时在桌宠首次显示前恢复，并保留文档
  中其他已知和未知字段。
- 切换时先应用原生窗口状态，再原子保存偏好。保存失败时只执行一次恢复旧原生状态并明确失败，不重试；
  原生应用失败时不写配置、不改变勾选状态。
- CAP-022 仍保持 `planned`，因为快捷键、开机启动和三平台完整桌面能力尚未完成；置顶子能力由本决定先行
  交付。

## 2026-09-02 开机启动后续决定

- 系统设置页开放 `system.launch_at_login`。开关保存时由 Rust 调用跨平台原生服务，不把旧 Python
  `startup.launch_at_login` 字段当作运行状态。
- 设置窗口每次打开都读取操作系统里的真实启动项；启用或关闭后必须回读确认。失败时保留前端草稿，
  用户可以重试或放弃。
- Windows、macOS 和 Linux 使用同一公开 Snapshot 与保存命令，平台差异留在原生服务内。便携版移动后
  不自动修复旧路径，用户可关闭再重新开启此项。
- CAP-022 继续保持 `planned`，因为全局快捷键等剩余桌面能力尚未交付。
