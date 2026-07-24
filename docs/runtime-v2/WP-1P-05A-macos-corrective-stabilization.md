# WP-1P-05A：macOS Runtime v2 窄范围基础纠正稳定化

> Work Package 状态、启动点和唯一 active/stabilizing 项只见
> `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 第 2 节。
> 开始日期：2026-07-24
> 前置：WP-1P-05 accepted（CI platform foundation）
> 规范来源：ADR-0004、WP-1P-05、Runtime v2 Work Package 总表、产品能力台账

## 1. 结果与边界

本 Work Package 只处理会阻断后续 Runtime v2 架构验证的 macOS 基础问题：默认入口必须定位
平台正确的 Shell 产物，透明 Tauri Window 必须使用 Tauri 要求的 macOS private API 配置，拖动
完成后必须以状态切换前读取的最终物理窗口位置更新固定立绘锚点。Windows 已验收的 Win32 region
和同步 move loop 保持原样。

根因假设及验证路径：Tauri Runtime WRY 的 `start_dragging()` 向 UI loop 投递 `DragWindow` 后即
返回；Tao/macOS 随后从当时的 `NSApp.currentEvent` 构造并运行 `performWindowDragWithEvent:`。
因此把该调用命名为 `start_drag_and_wait`，并在返回后立刻读取 `outer_position`，会读取拖动前的
位置，且重新布局消息会在用户释放后用旧锚点覆盖真实落点。macOS 的一次 live drag 还会产生多个
`Moved`，首个事件只是中间位置而不是完成信号。修复必须把 Windows 的同步完成语义和
macOS/Linux 的 deferred start 语义明确分开，不能以 sleep、固定延时、禁用重布局或吞掉错误
掩盖竞争。

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
- visibility 技术探针必须由 Rust 在隐藏后向原生主线程事件循环排队恢复任务；恢复所有权不得留在
  已隐藏并可能被 macOS WebKit 暂停的页面 timer 中，也不得依赖用户点击桌面重新激活应用。
- `start_drag_and_wait` 改为表达实际时机的 start-only 接口。Windows 继续在既有 Win32 move
  loop 返回后读取位置并同步提交锚点；macOS/Linux 只启动原生拖动并保留 deferred pending，原生
  `Moved` 不提交中间锚点。下一次被接受的状态布局必须先读取当前 `outer_position` 并推导最终物理
  锚点，在任何程序化 bounds 之前清除 pending，再经共享布局应用边界约束。
- idle、bubble、composer、expanded 均继续复用提交后的同一物理立绘锚点；边界约束仍由共享
  `apply_window_layout` 执行，只能在落点超出工作区时修正，不能回退到默认右侧锚点。

## 4. 测试与真实验收

自动测试先以失败测试锁定：三平台 Shell 名称选择、release/debug 定位、`start.sh` 对 macOS
Debug/Release Shell 的直接交接、private API 配置与 native drag 完成语义。Rust 单元测试必须
证明 Windows 仍选同步完成路径，macOS/Linux 的 deferred pending 只由下一次布局完成；既有几何
测试继续证明状态切换保持物理锚点及边界约束。

真实设备仅使用 Apple Silicon 单显示器，依次验证：默认入口与 `bash scripts/start.sh` 能启动；
背景无白色矩形；visibility 探针无需点击桌面即可恢复且首个输入立即响应；向左、向上、向屏幕
中央拖动后都停在释放位置；idle/bubble/composer/expanded 往返不跳变；关闭后 Shell/Core/共享锁
无残留。验收前后比对 `data/`、`characters/` 和受保护 `runtime/` 内容，确保没有非预期变化。

故障门：未构建 Shell 必须给出明确错误；无效平台名必须安全回退为无扩展名而非 Windows 后缀；
拖动未初始化布局、重复或陈旧移动提交、原生 bounds/region 失败必须返回稳定错误，不得静默
回退为默认锚点或关闭重布局。

### 2026-07-24 实现证据（仍为 active，非 accepted）

本节记录本次窄范围实现和截至当前可重复的验证结果，不构成 WP-1P-05A accepted 记录。

- TDD 红绿证据：先后以失败测试固定了 Darwin/Linux 错误查找 `.exe`、`start.sh` 错误交给 Python、
  缺少 macOS private API/初始可见窗口配置、WebView 异步事件注册丢失 native drag 提交、
  阻塞事件注册使初始 idle 布局和关闭监听永远不安装、只关闭窗口而不退出 macOS Shell，以及首个
  native `Moved` 过早固化拖动中间锚点、隐藏 WebView 持有自身恢复 timer。
  修复后 `tests/integration/test_wp_1p_05a_macos_corrective.py` 和
  `tests/integration/test_wp_1a_04_entries.py` 共 22 项通过。
- 自动检查：定向 pytest 为 `22 passed`；`tests/unit` 为 `981 passed, 3 skipped`；
  `npm test --prefix desktop/frontend` 为 `18 passed`；`cargo fmt --check` 通过；
  `cargo build --locked` 通过（仅既有 dead-code 警告）。
- 原始 `cargo test --locked` 在本机为 `93 passed, 3 failed, 3 ignored`：三个 POSIX 跨语言锁
  测试固定调用 PATH 中的 `python`，而这台新 Mac 只提供仓库 `runtime/bin/python3`。临时将该
  解释器以仅本次命令 PATH shim 暴露为 `python` 后为 `96 passed, 3 ignored`；shim 已删除。本
  WP 不修改与窗口修正无关的既有 shared-lock 测试前提。
- 全量 `./runtime/bin/python3 -m pytest` 曾在约 14% 的既有 legacy Qt 路径
  `app/ui/pet_window.py:_set_macos_window_topmost` 段错误；该路径被本 WP 的允许目录排除，未作
  修改。
- 真实 Apple Silicon（M4）单显示器 UI 冒烟已确认：`bash scripts/start.sh` 能交接并启动最新
  Debug Shell、取得共享锁并在受控中断后释放；同一 Debug Mach-O 仅以 `/private/tmp` 临时 `.app`
  包装供 macOS 无障碍 UI 验证，未触碰发布资产。Tauri WebView 已加载；透明窗口没有额外白色
  矩形；初始 idle、bubble、composer、expanded 均能切换；使用 Shell 内关闭按钮后共享锁立即
  释放。当前技术验证 Shell 没有接入 Core，因此该项仅覆盖 Shell 和锁，不声称 Core 已启动或
  已验收。
- 未通过的原生拖动门禁：本自动化环境的 Computer Use `drag` 没有进入 AppKit 的
  `performWindowDragWithEvent:` 原生 move loop，因而不能以合成指针伪造“向左、向上、屏幕中央
  释放后停在落点”及其后的状态切换稳定性。必须由真实 macOS 用户输入完成该三项手工验收后，才
  能写 accepted。
- 后续用户真机复现曾显示：拖动本身正常，但状态切换会回到右下锚点。第一轮修正消除了 WebView
  监听和 pending 建立晚于 native `Moved` 的竞态；第二轮真机证据进一步证明一次 AppKit live drag
  会产生多个 `Moved`，首个事件提交的是中间锚点。现已禁止移动事件消费 pending 或调用 bounds；
  下一次状态切换以当时的 `outer_position` 推导释放锚点，并在程序化布局移动前清除 pending。该
  修正未引入延时且保持 Windows 同步 move loop 不变，仍须用物理鼠标完成四种状态切换复验。
- 同轮真机复验还发现 visibility 探针在 macOS 隐藏后必须点击桌面才恢复，恢复后的 WebView 首次
  输入也被冻结。根因是恢复动作由已隐藏页面的 `window.setTimeout` 持有；现改为 Rust 隐藏后向
  Tauri/WRY 原生主线程排队 show/focus，前端不再持有 timer。该修正等待本轮真机复验，不作为
  accepted 证据。
- 受保护目录摘要门禁也未通过：开始前的 `data/`、`characters/`、`runtime/` 合并摘要为
  `5fe97f2b21a1870dcf723e4387990efa1ce366ae5752df8af5aa23761de43043`，当前为
  `c8401222a9aefe01d8a22c2c76cf42921b99df0a`，且
  `characters/` 在此 checkout 中不存在。可见 `data/logs/sakura-runtime.log` 及大量
  `runtime/lib/**/__pycache__/*.pyc` 的时间戳发生变化；为遵守禁止清理用户数据的范围，本 WP
  没有删除、回退或改写这些受保护内容。需要由项目负责人确认基线或授权恢复后，才能完成该门禁。

因此 WP-1P-05A 保持 `active`，WP-3-01 继续不得激活；不得把本节当作 WP-7-02 的 Spaces、多屏、
Retina、IME、签名或发布证据。

## 5. 独立回退

按提交顺序回退本 WP 的 accepted、实现和激活提交。回退后恢复 WP-1P-05 accepted 的状态，
保留既有 Windows backend、共享布局/命中纯模型、WP-1P-06 生命周期和 WP-1C-04 bundled Core
证据；不删除、清理或改写真实 `data/`、`characters/`、`runtime/`、用户配置或缓存。
