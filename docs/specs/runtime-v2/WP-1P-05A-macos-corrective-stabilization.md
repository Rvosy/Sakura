---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# WP-1P-05A：macOS Runtime v2 窄范围基础纠正稳定化

## 1. 结果与边界

macOS 开发启动和窗口交互遵循以下约定：默认入口必须定位
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

开发入口使用最小 `.app` wrapper。正式发行布局、签名和安装包要求见[发行契约](release-distribution-and-storage.md)。
原生交互的验证记录注明操作系统、显示器与缩放条件。

## 2. 验证边界

窗口故障通过对应平台的入口与真实交互复现；跨平台公共代码还需覆盖受影响的平台语义。故障测试使用隔离根，
不改写真实角色包、配置、历史或 Runtime。

## 3. 实施契约

- `main.py` 的公共定位逻辑只根据平台选择 Shell 名称：`win32` 使用
  `sakura.exe`，`darwin` 和 `linux` 使用无扩展名 debug
  `sakura`；不得把 release packaged Shell 当成开发入口。Darwin 验证产物存在后必须以
  `os.execv` 交接给 `/bin/bash scripts/start.sh`，使所有 macOS 入口共享同一 app identity 逻辑；
  Windows/Linux 继续直接交接已解析 Shell。
- `scripts/start.sh` 增量构建并启动 debug Shell；release 已属于 packaged 模式，必须从完整发行布局启动。Linux 和其他非 Darwin
  Unix 直接 `exec` raw Shell；Darwin 在所选 profile 的
  `target/<profile>/.sakura-dev/Sakura Runtime v2.app` 中原子刷新最小 `Info.plist` 和指向同 profile
  Mach-O 的相对 symlink，并将开发图标原子刷新到 `Contents/Resources/Sakura.icns`；`Info.plist`
  必须以 `CFBundleIconFile=Sakura.icns` 声明该图标，再直接 `exec` bundle 内入口。wrapper 失败必须明确报错并安全关闭，
  不得回退 raw Mach-O；脚本不得使用 `open`、先启动 Python，或创建/修改 `runtime/` 缓存、模型
  和用户数据。
- Tauri 配置同时声明透明、无装饰、无阴影和 `app.macOSPrivateApi: true`，Cargo 显式启用
  `tauri/macos-private-api`，使 `transparent: true` 在 macOS 真正生效。该配置使用私有 API，
  因而不适用于 Mac App Store 分发；签名、公证和 bundle 验证遵循发行契约。
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

原生交互按受影响设备条件验证：默认入口与 `bash scripts/start.sh` 能启动；
背景无白色矩形；visibility 探针无需点击桌面即可恢复且首个输入立即响应；向左、向上、向屏幕
中央拖动后都停在释放位置；idle/bubble/composer/expanded 往返不跳变；关闭后 Shell/Core/共享锁
无残留。故障注入使用隔离数据，核对相关配置和角色内容；正常启动产生的日志与缓存保留。

故障门：未构建 Shell 必须给出明确错误；无效平台名必须安全回退为无扩展名而非 Windows 后缀；
拖动未初始化布局、重复或陈旧移动提交、原生 bounds/region 失败必须返回稳定错误，不得静默
回退为默认锚点或关闭重布局。

## 历史验证

实现迭代、失败与最终验收结果见[2026-07-24 至 25 日记录](../../records/baselines/runtime-v2/2026-07-24-25-macos-stabilization.md)。

## 5. 独立回退

回退涉及的启动或窗口实现时，保留 Windows backend、共享布局与命中模型、生命周期和 Core 监管能力。
用户配置、角色、Runtime 与已有日志按数据兼容契约保留；历史任务状态不作为实现回退步骤。
