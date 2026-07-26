# WP-3U-01：同一 Tauri App 的右键菜单与设置窗口宿主

```text
状态：stabilizing
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

## 激活与实现记录（2026-07-27）

- 前置 WP-3-03 已由项目负责人验收并标记 `accepted`，本 WP 随即激活。
- 单一规范源冻结为方案 1：`desktop/frontend/settings/**` 是 canonical source；legacy
  `tools/settings-tauri` 直接从该目录构建，不保留第二份完整资产。
- Rust 已拥有原生产品菜单、封闭 menu item allowlist、唯一 `settings` 窗口、重复打开聚焦、
  CloseRequested/放弃确认和 5 秒有界主应用退出协调。
- capability manifest 当前不开放任何可写 section；未迁移页面稳定禁用，前端不读取或保存用户配置。
- 本地 frontend 与 Rust 全量自动测试通过，进入 `stabilizing`；Windows 真实菜单位置、100%/150% DPI、
  最小化/聚焦、中文 IME、重复打开和退出仍需形成候选验收证据后才能 `accepted`。

本 WP 只迁移窗口宿主、菜单入口、设置前端外壳和能力门控。角色/外观配置的真实读取与保存属于
WP-3U-02；其他设置功能仍跟随对应能力 WP。

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

旧独立设置工具在 legacy Qt 回退期间保留，不因本 WP 删除；Runtime v2 与 legacy Qt 应共享前端和纯配置
契约的规范源，但保留各自窗口宿主。

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
- 关闭设置只销毁或隐藏 `settings`，不得退出 App、关闭桌宠或触发 Core shutdown。
- App 退出时先请求设置关闭/丢弃确认，再按现有受控路径关闭 Core 和全部窗口。
- Core 崩溃时设置窗口继续存在，并只按能力清单显示可用/不可用状态。

第一版右键菜单至少包含：

```text
显示/隐藏桌宠（按当前状态二选一）
设置…
退出
```

“保持置顶”、历史、运行日志、权限等条目只在相应所有者和产品语义已经迁移时加入；不得放置点击后
无真实效果的菜单项。菜单和托盘以后可以共享 action model，但本 WP 不提前实现完整托盘能力。

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

## 实施白名单

允许修改：

- `desktop/src-tauri/src/**` 中菜单、secondary window、settings capability 和关闭协调的窄模块。
- `desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/capabilities/**` 中 settings 窗口的最小权限。
- `desktop/frontend/**` 中产品右键入口、settings canonical frontend 和共享 UI 模块。
- `tools/settings-tauri/frontend/**` 与其构建配置中指向 canonical source 的兼容改造。
- 与上述范围直接相关的 Rust/frontend/Python 静态边界测试和规范文档。

明确禁止：

- 修改 Python Assistant、Provider、Memory、Tools、MCP、插件、TTS、截图或角色业务语义。
- 从 Runtime v2 启动 `sakura-settings` 子进程或复用其 stdio RPC。
- 在本 WP 写入 `data/**`、角色配置、API 配置或用户凭据。
- 打开尚未迁移设置页面，或为页面方便建设跨 Python/Rust 分布式设置事务。
- 迁移 Studio、历史窗口、完整托盘、开机启动和全局快捷键。

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
macOS/Linux 真实菜单和 compositor 门保留 WP-7-02。

## 状态与回退

只有 WP-3-03 accepted 后才能激活。本 WP 完成实现后进入 `stabilizing`；无 P0/P1、窗口生命周期、
焦点/IME、关闭和三平台构建门通过后才能 accepted。

回退时移除 Runtime v2 菜单 action、settings window 注册、capability shell 和资源暂存接线，保留
WP-3-03 固定产品 UI。旧独立设置工具和 legacy Qt 入口保持可用；不得删除或恢复用户配置。
