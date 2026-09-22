---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# WP-3U-01：同一 Tauri App 的右键菜单与设置窗口宿主

设置窗口由 Sakura 主 Tauri App 创建和管理，重复打开时聚焦同一个 `settings` 窗口。桌宠右键菜单、
系统托盘和设置窗口共用主程序生命周期，关闭设置不退出桌宠或 Core。

同一 App 宿主、Rust 窗口生命周期所有权、独立设置进程取舍和 canonical frontend 选择的架构原因见
[`ADR-0006`](../../adr/0006-same-app-settings-host.md)。本文保留窗口、菜单、capability、关闭和验收契约。

## 界面说明

### 设置说明与悬停提示

- 参数补充说明使用标签旁无圆圈的小 `?`，保持设置行的紧凑布局。隐私范围、数据移动、保存后果和错误
  保留可见说明，分类遵循[界面文案规范](../../devdocs/UI_COPY_GUIDELINES.md)。
- 主界面不显示悬浮提示，包括附件、截图、语音输入和发送按钮；保留无障碍名称和必要状态。
- 设置和角色工坊共用主题提示浮层。静态和动态提示使用 `data-tooltip`，不使用原生 `title`；
  自绘下拉框透传原控件和选项的提示。提示随主题更新，靠近窗口边缘时调整位置，避免被滚动容器裁切。
- 提示支持悬停和键盘聚焦，问号另支持点击展开与收起；Esc、点击外部、页面滚动和窗口失焦时收起。
  浮层使用纯文本和 `role="tooltip"`，显示期间通过 `aria-describedby` 关联来源，保留已有描述引用。

## 窗口架构

```text
一个 Sakura Tauri App
├─ main WebViewWindow
│  └─ 自绘右键菜单 -> Rust ProductMenuAction
├─ settings WebViewWindow（最多一个）
└─ Python Core（仍由同一 Supervisor 管理）
```

Rust 是窗口和菜单生命周期的唯一所有者：

- 桌宠右键菜单由主 WebView 按主题渲染，动作和可用状态来自 Rust；系统托盘使用原生菜单。
- Rust 按 `ProductMenuAction` 处理动作 ID，拒绝未知动作；WebView 不自行构造业务参数。
- `settings` 不存在时创建；已存在时 unminimize、show、focus，不创建第二个实例。
- 设置窗口使用有装饰的普通窗口，可最小化、有任务栏入口、默认不置顶。
- 设置 WebView 必须始终填满原生窗口内容区；最大化、恢复和拖动缩放不得保留旧 viewport 或露出原生背景。
- 关闭设置只销毁或隐藏 `settings`，不得退出 App、关闭桌宠或触发 Core shutdown。
- App 退出时先请求设置关闭/丢弃确认，再按现有受控路径关闭 Core 和全部窗口。
- Core 崩溃时设置窗口继续存在，并只按能力清单显示可用/不可用状态。

菜单动作与可用状态由 [`product_shell.rs`](../../../desktop/src-tauri/src/product_shell.rs) 的
`ProductMenuAction` 和 `ProductMenuCapabilityManifest` 维护；前端入口为
[`PetContextMenu`](../../../desktop/frontend/pet/context_menu.js)。

自绘菜单打开期间 Rust 临时恢复整窗命中区域，关闭后按最新布局、DPI、缩放和透明区域恢复精确命中。
菜单打开时仍允许气泡和输入栏提交布局；基础布局快照随之更新，菜单覆盖层继续可交互。

## 设置前端与能力

设置前端唯一来源是 [`desktop/frontend/settings/`](../../../desktop/frontend/settings/)。
页面依据 `product_shell.rs` 中的 `SettingsCapabilityManifest` 显示当前可用区块，动态插件页面按插件声明加载。
密钥和完整私密配置不进入 capability manifest、日志或通用 Snapshot。页面的真实数据链不可用时，显示对应原因。

## 关闭与未保存语义

- 没有未保存草稿时，关闭设置窗口立即完成。
- 有未保存草稿时，由设置前端显示确认；Rust `CloseRequested` 必须 prevent close，直到收到明确结果。
- Collection 只有可编辑字段偏离原值时才算未保存草稿，打开编辑器或改回原值不阻止关闭。
  “应用”可保留 global 草稿并提交普通设置与角色选择；角色草稿仍须先处理。“保存并关闭”及关闭确认中的保存
  在任何页面写入前检查全部集合草稿，要求先保存或还原记录，不把 Collection 写入混入普通设置保存。
- 用户取消关闭时窗口保持可见并恢复焦点。
- 用户确认放弃时只关闭 settings 窗口。
- 主应用退出时，先恢复并聚焦处理确认的窗口（包括最小化的设置或角色工坊），重复退出请求也要前置
  已有确认。设置收到请求后通过 `acknowledge_settings_exit` 回应；5 秒超时只取消未获页面回应的请求，
  不取消正在等待用户选择或保存的确认，也不绕过 Core 清理。
- `sakura://settings-exit-requested` 携带进程内递增的请求序号；回应和 `resolve_settings_exit` 均携带
  `revision`，过期回应和旧定时器不得影响新请求。保存失败时保留草稿并取消本次退出，允许再次尝试。
- 退出确认使用“继续编辑”“直接退出”“保存并退出”；“直接退出”放弃未保存设置并退出整个应用。
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

Windows 真实应用至少验证右键菜单位置、100%/150% DPI、设置窗口创建/最小化/聚焦、中文 IME、
未保存关闭确认、重复打开和主程序退出。公共 Rust/frontend 构建必须在 Windows/macOS/Linux 通过；
macOS 还必须验证设置窗口默认、最大化、恢复和拖动缩放时 WebView bounds 始终等于原生内容区；Linux
真实菜单和 compositor 门保留 WP-7-02。

## 验证与回退

变更按实际影响验证窗口生命周期、焦点/IME 和关闭行为，公共代码由 CI 验证三平台构建。
人工与自动结果分别记录。[2026-07-27 验收记录](../../records/baselines/runtime-v2/2026-07-27-settings-window-acceptance.md)保留当时的测试结果和设备覆盖范围。

回退窗口实现时保留用户配置与主程序生命周期；旧 Qt 入口和未迁移页面只属于历史实现。

## 桌宠保持置顶

- “保持置顶”成为主题自绘桌宠菜单中的真实复选动作，默认关闭；勾选状态只取自 Rust 已提交状态。
- Rust 只对 `main` 桌宠窗口调用原生置顶 API。设置、历史窗口保持普通窗口层级；截图选择窗口继续使用
  自己的临时置顶语义，不与该偏好联动。
- 偏好保存到共享 `config/ui.json` 的 `settings.always_on_top`，启动时在桌宠首次显示前恢复，并保留文档
  中其他已知和未知字段。
- 切换时先应用原生窗口状态，再原子保存偏好。保存失败时只执行一次恢复旧原生状态并明确失败，不重试；
  原生应用失败时不写配置、不改变勾选状态。

## 开机启动

- 系统设置页开放 `system.launch_at_login`。开关保存时由 Rust 调用跨平台原生服务，不把旧 Python
  `startup.launch_at_login` 字段当作运行状态。
- 设置窗口每次打开都读取操作系统里的真实启动项；启用或关闭后必须回读确认。失败时保留前端草稿，
  用户可以重试或放弃。
- Windows、macOS 和 Linux 使用同一公开 Snapshot 与保存命令，平台差异留在原生服务内。便携版移动后
  不自动修复旧路径，用户可关闭再重新开启此项。
