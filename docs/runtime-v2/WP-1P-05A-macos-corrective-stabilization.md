# WP-1P-05A：macOS Runtime v2 窄范围基础纠正稳定化

> Work Package 状态、启动点和唯一 active/stabilizing 项只见
> `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 第 2 节。
> 开始日期：2026-07-24
> 前置：WP-1P-05 accepted（CI platform foundation）
> 规范来源：ADR-0004、WP-1P-05、Runtime v2 Work Package 总表、产品能力台账

## 1. 结果与边界

本 Work Package 只处理会阻断后续 Runtime v2 架构验证的 macOS 基础问题：默认入口必须定位
平台正确的 Shell 产物，透明 Tauri Window 必须使用 Tauri 要求的 macOS private API 配置，拖动
完成后必须以原生移动事件给出的物理位置更新固定立绘锚点。Windows 已验收的 Win32 region 和
同步 move loop 保持原样。

根因假设及验证路径：Tauri Runtime WRY 的 `start_dragging()` 向 UI loop 投递 `DragWindow` 后即
返回；Tao/macOS 随后从当时的 `NSApp.currentEvent` 构造并运行 `performWindowDragWithEvent:`。
因此把该调用命名为 `start_drag_and_wait`，并在返回后立刻读取 `outer_position`，会读取拖动前的
位置，且重新布局消息会在用户释放后用旧锚点覆盖真实落点。修复必须把 Windows 的同步完成语义
和 macOS/Linux 的原生 `Moved` 事件完成语义明确分开，不能以 sleep、固定延时、禁用重布局或
吞掉错误掩盖竞争。

本 Work Package 不把 Apple Silicon 单显示器证据描述为完整 macOS 验收。Spaces、复杂多屏、
Retina 矩阵、中文/日文 IME、代码签名、公证、DMG/App bundle 和发布继续属于 WP-7-02/WP-7-04。

## 2. 允许目录

只允许修改以下路径：

- `main.py`
- `scripts/start.sh`
- `desktop/src-tauri/Cargo.toml`
- `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/src/main.rs`
- `desktop/src-tauri/src/window_interaction.rs`
- `desktop/src-tauri/src/platform/contracts.rs`
- `desktop/src-tauri/src/platform/window_backend.rs`
- `desktop/frontend/app.js`
- `tests/integration/test_wp_1a_04_entries.py`
- `tests/integration/test_wp_1p_05a_macos_corrective.py`
- `docs/runtime-v2/WP-1P-05A-macos-corrective-stabilization.md`
- `docs/runtime-v2/product-capability-parity.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`

禁止修改 `data/`、`characters/`、`runtime/`、`app/`、`plugins/`、`app/plugins/`、Core、Supervisor、
IPC、Snapshot、角色资源、用户配置、历史、第三方目录和发布资产。不得引入聊天、Assistant、
Memory、Tools、TTS、设置或产品视觉重做。

## 3. 实施契约

- `main.py` 的公共定位逻辑只根据平台选择 Shell 名称：`win32` 使用
  `sakura-runtime-v2-shell.exe`，`darwin` 和 `linux` 使用无扩展名
  `sakura-runtime-v2-shell`；仍按 release 后 debug 的顺序定位。
- `scripts/start.sh` 直接定位并 `exec` 已构建的无扩展名 Debug/Release Shell。它不得先启动
  Python，也不得创建或修改 `runtime/` 缓存、模型或用户数据。
- Tauri 配置同时声明透明、无装饰、无阴影和 `app.macOSPrivateApi: true`，Cargo 显式启用
  `tauri/macos-private-api`，使 `transparent: true` 在 macOS 真正生效。该配置使用私有 API，
  因而不能接受 Mac App Store 分发；它只允许未来按直接签名/公证路径评估，实际签名、公证和
  bundle 验收仍留给 WP-7-04。
- `start_drag_and_wait` 改为表达实际时机的 start-only 接口。Windows 继续在既有 Win32 move
  loop 返回后读取位置并同步提交锚点；macOS/Linux 只启动原生拖动，随后由对应 `Moved` 事件的
  物理坐标提交一次锚点并应用边界约束。锚点提交完成前不得从旧 `outer_position` 重新布局。
- idle、bubble、composer、expanded 均继续复用提交后的同一物理立绘锚点；边界约束仍由共享
  `apply_window_layout` 执行，只能在落点超出工作区时修正，不能回退到默认右侧锚点。

## 4. 测试与真实验收

自动测试先以失败测试锁定：三平台 Shell 名称选择、release/debug 定位、`start.sh` 对 macOS
Debug/Release Shell 的直接交接、private API 配置与 native drag 完成语义。Rust 单元测试必须
证明 Windows 仍选同步完成路径，macOS/Linux 只接受 native `Moved` 后的单次锚点提交；既有
几何测试继续证明状态切换保持物理锚点及边界约束。

真实设备仅使用 Apple Silicon 单显示器，依次验证：默认入口与 `bash scripts/start.sh` 能启动；
背景无白色矩形；向左、向上、向屏幕中央拖动后都停在释放位置；idle/bubble/composer/expanded
往返不跳变；关闭后 Shell/Core/共享锁无残留。验收前后比对 `data/`、`characters/` 和受保护
`runtime/` 内容，确保没有非预期变化。

故障门：未构建 Shell 必须给出明确错误；无效平台名必须安全回退为无扩展名而非 Windows 后缀；
拖动未初始化布局、重复或陈旧移动提交、原生 bounds/region 失败必须返回稳定错误，不得静默
回退为默认锚点或关闭重布局。

## 5. 独立回退

按提交顺序回退本 WP 的 accepted、实现和激活提交。回退后恢复 WP-1P-05 accepted 的状态，
保留既有 Windows backend、共享布局/命中纯模型、WP-1P-06 生命周期和 WP-1C-04 bundled Core
证据；不删除、清理或改写真实 `data/`、`characters/`、`runtime/`、用户配置或缓存。
