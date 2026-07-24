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

visibility 复核进一步证明：同一 Mach-O 以 raw 路径启动时没有 LaunchServices app identity，
`lsappinfo` 显示 `bundleID=NULL`、`fileType=????`，窗口可能不可见或在 hide/show 后冻结；同字节
Mach-O 从最小 `.app` 内直接执行时拥有稳定 `APPL` identity，显示、恢复和首次输入均正常。向 raw
进程注入 `__CFBundleIdentifier` 无法替代真实 bundle 结构，因此该问题属于 macOS 开发生命周期，
不能继续用 show/focus、页面 timer 或固定延时修补。

本 Work Package 不把 Apple Silicon 单显示器证据描述为完整 macOS 验收。Spaces、复杂多屏、
Retina 矩阵、中文/日文 IME、代码签名、公证、DMG、正式发布 `.app` 和发布流程继续属于
WP-7-02/WP-7-04；本 WP 的忽略目录开发 app identity wrapper 不属于发布工件。

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
  `sakura-runtime-v2-shell`；仍按 release 后 debug 的顺序定位。Darwin 验证产物存在后必须以
  `os.execv` 交接给 `/bin/bash scripts/start.sh`，使所有 macOS 入口共享同一 app identity 逻辑；
  Windows/Linux 继续直接交接已解析 Shell。
- `scripts/start.sh` 保持 release 后 debug 的无扩展名 Shell 查找顺序。Linux 和其他非 Darwin
  Unix 直接 `exec` raw Shell；Darwin 在所选 profile 的
  `target/<profile>/.sakura-dev/Sakura Runtime v2.app` 中原子刷新最小 `Info.plist` 和指向同 profile
  Mach-O 的相对 symlink，再直接 `exec` bundle 内入口。wrapper 失败必须明确报错并安全关闭，
  不得回退 raw Mach-O；脚本不得使用 `open`、先启动 Python，或创建/修改 `runtime/` 缓存、模型
  和用户数据。
- Tauri 配置同时声明透明、无装饰、无阴影和 `app.macOSPrivateApi: true`，Cargo 显式启用
  `tauri/macos-private-api`，使 `transparent: true` 在 macOS 真正生效。该配置使用私有 API，
  因而不能接受 Mac App Store 分发；它只允许未来按直接签名/公证路径评估，实际签名、公证和
  bundle 验收仍留给 WP-7-04。
- visibility 技术探针的恢复所有权不得留在已隐藏并可能被 macOS WebKit 暂停的页面 timer 中，
  也不得依赖用户点击桌面重新激活应用。`run_on_main_thread` 在调用方已位于 Tauri 主线程时会立即
  执行，不能作为跨越独立事件循环 turn 的证据；真实显示恢复只由具备 `.app` identity 的 Apple
  Silicon 真机门证明，源码字符串测试不得宣称可见性已经修复。
- `start_drag_and_wait` 改为表达实际时机的 start-only 接口。Windows 继续在既有 Win32 move
  loop 返回后读取位置并同步提交锚点；macOS/Linux 只启动原生拖动并保留 deferred pending，原生
  `Moved` 不提交中间锚点。下一次被接受的状态布局必须先读取当前 `outer_position` 并推导最终物理
  锚点，在任何程序化 bounds 之前清除 pending，再经共享布局应用边界约束。
- idle、bubble、composer、expanded 均继续复用提交后的同一物理立绘锚点；边界约束仍由共享
  `apply_window_layout` 执行，只能在落点超出工作区时修正，不能回退到默认右侧锚点。

## 4. 测试与真实验收

自动测试先以失败测试锁定：三平台 Shell 名称选择、release/debug 定位、`start.sh` 对 macOS
Debug/Release Shell 的开发 `.app` 交接、Linux raw 交接、plist/symlink 原子刷新、并发生成、失败
关闭、private API 配置与 native drag 完成语义。Rust 单元测试必须
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
- 真实 Apple Silicon（M4）单显示器复核显示：`bash scripts/start.sh` 直接执行 raw Debug Mach-O
  的两轮启动均取得共享锁并创建 WebKit 子进程，但 `lsappinfo` 均为 `bundleID=NULL`、
  `fileType=????`，窗口不可见；注入 `__CFBundleIdentifier` 也没有改变该身份。相同 SHA-256 的
  Mach-O 通过 `/private/tmp` 最小 `.app` 的 LaunchServices 启动、bundle 内直接 exec 以及 symlink
  入口三种方式均稳定，`lsappinfo` 显示正式 bundle ID 和 `fileType=APPL`，连续 hide/show 与恢复
  后首次输入通过。该临时验证未触碰发布资产，也不构成发布 bundle 验收。
- 未通过的原生拖动门禁：本自动化环境的 Computer Use `drag` 没有进入 AppKit 的
  `performWindowDragWithEvent:` 原生 move loop，因而不能以合成指针伪造“向左、向上、屏幕中央
  释放后停在落点”及其后的状态切换稳定性。必须由真实 macOS 用户输入完成该三项手工验收后，才
  能写 accepted。
- 后续用户真机复现曾显示：拖动本身正常，但状态切换会回到右下锚点。第一轮修正消除了 WebView
  监听和 pending 建立晚于 native `Moved` 的竞态；第二轮真机证据进一步证明一次 AppKit live drag
  会产生多个 `Moved`，首个事件提交的是中间锚点。现已禁止移动事件消费 pending 或调用 bounds；
  下一次状态切换以当时的 `outer_position` 推导释放锚点，并在程序化布局移动前清除 pending。该
  修正未引入延时且保持 Windows 同步 move loop 不变，仍须用物理鼠标完成四种状态切换复验。
- 同轮真机复验还发现 visibility 探针在 raw Mach-O 下隐藏后必须点击桌面才恢复，恢复后的 WebView
  首次输入也被冻结。此前将根因仅归为隐藏页面 `window.setTimeout`，并声称 Rust
  `run_on_main_thread` 已排队到独立事件循环 turn；该结论现已撤回。诊断日志证明 hide/show 前后的
  NSWindow/Tauri visible/focused 状态完整正常，而 Tauri/WRY 在调用方已位于主线程时会立即执行
  `run_on_main_thread`。页面不得拥有恢复 timer 仍是有效边界，但真实修复条件是 macOS `.app`
  identity，只能由真机显示和首次输入门禁证明。
- 受保护目录摘要门禁也未通过：开始前的 `data/`、`characters/`、`runtime/` 合并摘要为
  `5fe97f2b21a1870dcf723e4387990efa1ce366ae5752df8af5aa23761de43043`，当前为
  `c8401222a9aefe01d8a22c2c76cf42921b99df0a`，且
  `characters/` 在此 checkout 中不存在。可见 `data/logs/sakura-runtime.log` 及大量
  `runtime/lib/**/__pycache__/*.pyc` 的时间戳发生变化；为遵守禁止清理用户数据的范围，本 WP
  没有删除、回退或改写这些受保护内容。需要由项目负责人确认基线或授权恢复后，才能完成该门禁。

### 2026-07-25 开发 app identity 修正证据（仍为 active，非 accepted）

- 新增行为测试先在 raw 启动实现上得到 `5 failed, 23 passed`，失败精确覆盖 Darwin 未从 `.app`
  入口执行、stale wrapper 未刷新、wrapper 失败仍执行 raw Shell，以及 `main.py` 未交接统一脚本；
  最小实现后，Darwin Debug/Release、release 优先、参数与退出码传播、带空格路径、可解析 plist、
  相对 symlink、四进程 barrier 下持续观察的原子刷新、运行时同 PID 信号传播、plist 提交后的
  symlink 失败关闭、Linux raw 路径和非 Darwin 直接 exec 共 `33 passed`。独立复审曾在 72 轮压力
  中复现一次观察端 `readlink` 的 `EINVAL`；诊断确认调用前后均为 symlink，但 inode 已变化，即
  读取跨越了原子替换。稳定快照读取现在只在前后 inode 确认变化时重试该错误，非 symlink、路径
  缺失、稳定 inode 错误和非法 target 仍失败；确定性 RED/GREEN 用例及修正后连续 200 轮并发压力
  均通过。
- Darwin wrapper 位于所选 profile 的
  `target/<profile>/.sakura-dev/Sakura Runtime v2.app`；`Info.plist` 的
  `CFBundlePackageType=APPL`、`CFBundleExecutable=sakura-runtime-v2-shell`、
  `CFBundleIdentifier=com.rvosy.sakura.runtimev2.shell` 与 Tauri 配置一致。Mach-O 入口是相对
  symlink，不复制二进制；plist 和 symlink 均以 PID 唯一临时项加同目录 `mv` 原子替换，任一步
  失败均不回退 raw Mach-O。
- 自动门禁：定向 pytest `33 passed`；`tests/unit` 为 `982 passed, 2 skipped`；frontend 为
  `18 passed`；`bash -n`、`cargo fmt --check`、`cargo build --locked` 和 `git diff --check` 通过。
  仅本次 `/private/tmp` PATH shim 下完整 `cargo test --locked` 为 `96 passed, 3 ignored`，shim 已
  精确删除；Rust 输出仍只有既有 dead-code warning。
- Apple Silicon 单显示器通过 `bash scripts/start.sh` 和
  `PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 main.py` 启动时均无需点击桌面即可显示完整 idle
  窗口。`lsappinfo` 显示 `bundleID=com.rvosy.sakura.runtimev2.shell`、`fileType=APPL`；bundle path
  结束于 profile 的 `.sakura-dev/Sakura Runtime v2.app`，executable path 位于该 bundle 的
  `Contents/MacOS`。Computer Use 连续三轮
  visibility 探针均自动恢复，恢复后的首次点击分别立即进入 composer、expanded、bubble；默认
  Python 入口另复验一轮 hide/show 后首次点击立即进入 bubble。全过程没有固定 sleep、页面 timer
  或桌面点击；应用内关闭后 Shell、Core 和共享锁持有进程均为零，随后两个入口可重新取得锁。
- 本轮初始只读保护摘要为：`characters/` 为 `0` 字节、`0` 文件，`data/` 为 `658678` 字节、
  `1` 文件，`runtime/` 为 `2602961234` 字节、`49937` 文件。首次自动门禁后
  `data/logs/sakura-runtime.log` 增至 `752126` 字节；收尾重跑完整门禁后又增长 `93448` 字节，
  最终为 `845574` 字节（较本轮开始共新增 `186896` 字节）。收尾 Rust 测试还新增
  `runtime/lib/python3.12/encodings/__pycache__/ascii.cpython-312.pyc`（`2559` 字节），因此最终
  `runtime/` 为 `2602963793` 字节、`49938` 文件；`characters/` 保持不变。按“相对路径 + NUL +
  文件内容 + NUL”的排序 SHA-256 树摘要，开始/最终分别为：`characters/`
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` / 同值，`data/`
  `59c26614d80653bf51d10ee80817b69ec881e80cf72145f268514f986ee75073` /
  `09fc8d2310a3fc0b1d7bb417358263ef27d1370ec1d0a0fbe1e389b860761f69`，`runtime/`
  `0191bb27fc2c93867c7899a4e8b99337584696359e29252a602bcd3234b0f4f2` /
  `aee23379b9e1eb2b83a694fe1580f57c3331ea0a954884e7da4af78aa1d7f173`。本 WP 只报告新增差异，
  没有清理、截断或回退日志/缓存；保护目录门禁仍未关闭。

因此 WP-1P-05A 保持 `active`，WP-3-01 状态保持 `planned` 且继续不得激活；不得把本节当作
WP-7-02 的 Spaces、多屏、Retina、IME、签名或发布证据。

## 5. 独立回退

按提交顺序回退本 WP 的 accepted、实现和激活提交。回退后恢复 WP-1P-05 accepted 的状态，
保留既有 Windows backend、共享布局/命中纯模型、WP-1P-06 生命周期和 WP-1C-04 bundled Core
证据；不删除、清理或改写真实 `data/`、`characters/`、`runtime/`、用户配置或缓存。
