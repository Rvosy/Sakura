---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# 2026-07-24 至 25 日 macOS 稳定化记录

以下保留对应日期的实现、验证和失败记录。当前契约见[WP-1P-05A-macos-corrective-stabilization](../../../specs/runtime-v2/WP-1P-05A-macos-corrective-stabilization.md)。

> 以下带日期的段落是历史证据；状态、审批和后续任务安排仅描述当时情况，不约束当前开发。

### 2026-07-24 实现证据（当时为 active）

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

### 2026-07-25 开发 app identity 修正证据（当时为 active）

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
  `CFBundlePackageType=APPL`、`CFBundleExecutable=sakura`、
  `CFBundleIdentifier=com.rvosy.sakura.runtimev2.shell`、`CFBundleIconFile=Sakura.icns` 与 Tauri 配置一致，
  `Contents/Resources/Sakura.icns` 来自受版本管理的 macOS 图标。Mach-O 入口是相对
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

因此当时 WP-1P-05A 保持 `active`，WP-3-01 记为 `planned` 并暂缓实施；本节不能作为
WP-7-02 的 Spaces、多屏、Retina、IME、签名或发布证据。

### 2026-07-25 稳定化转换记录（历史）

本记录取代以上带日期的 `active` 阶段结论，作为当前稳定化转换的证据；历史记录仍准确描述其各自
发生时的状态。实现边界 HEAD 为 `f499e327943794b40c822386efef59084f7b6f6b`。

- 项目负责人已以物理鼠标验收：向左、向上和向屏幕中央拖动均在释放位置停止；idle、bubble、composer、
  expanded 往返保持稳定。
- 已有真实可见性证据覆盖 `bash scripts/start.sh` 与
  `PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 main.py`：无需桌面点击即可可见，visibility probe
  自动恢复，首次点击响应；`lsappinfo` 报告 bundle ID
  `com.rvosy.sakura.runtimev2.shell`，类型为 `APPL`。
- 项目负责人已批准受保护目录验收基线：已记录的日志增长以及
  `runtime/lib/python3.12/encodings/__pycache__/ascii.cpython-312.pyc` 均为基线，不能删除；后续门禁
  只报告新的差异。
- 最近已记录的自动门禁为：33 项定向 pytest 通过；unit 为 `982 passed, 2 skipped`；frontend 为
  `18 passed`；`cargo fmt --check`、`cargo build --locked` 与 `git diff --check` 通过；完整
  `cargo test --locked` 在精确的临时 PATH shim 下为 `96 passed, 3 ignored`，该 shim 已清理。最终
  稳定化重跑和当前 SHA 的 CI 仍待完成。
- 已记录 UI 轮次结束后，没有 Shell、Core 或 shared-lock holder 残留。
- 当时未宣称 P0/P1 已为验收清零；WP-3-01 仍记为 `planned`，尚未开始实施。

独立回退：revert 本次仅文档提交即可恢复 `active`。实现回退仍按既有 WP 的逐提交序列进行，且不得
触碰 `data/`、`characters/` 或 `runtime/`。

### 2026-07-25 最终 accepted 记录（历史）

本记录将 WP-1P-05A 的当前状态结论更新为 `accepted`，并且只取代以上历史 `active` 与
`stabilizing` 记录中的旧当前状态结论；各历史记录仍作为其发生时的实施、验证和稳定化证据。实现边界
为 `f499e327943794b40c822386efef59084f7b6f6b`；稳定化文档提交及当前已验证 SHA 为
`beea2ea10d513dc3d3cdca7804f80af45fbb518c`。

- 独立任务复审判定稳定化转换符合规范、任务质量获批准，且没有 Critical/Important 或 P0/P1 发现。
- 在 `beea2ea` 的新鲜本地门禁：定向 Python `33 passed`；`tests/unit` 为 `982 passed, 2 skipped`；
  frontend 为 `18 passed`；`cargo fmt --check` 通过；debug 与 release `cargo build --locked` 均通过；
  `cargo test --locked -- --test-threads=1` 为 `96 passed, 3 ignored`；`git diff --check` 通过。
- Python 与 Cargo 命令均使用 `PYTHONDONTWRITEBYTECODE=1`。Cargo test 使用唯一 `/private/tmp`
  `python` shim；精确 `unlink`/`rmdir` 清理成功，未遗留 shim。既有 Rust dead-code warnings 未变化。
- macOS Computer Use 覆盖 `PYTHONDONTWRITEBYTECODE=1 bash scripts/start.sh` 与
  `PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 main.py`：两者均无需桌面点击即可出现；visibility
  probe 隐藏并自动恢复窗口，恢复后首次点击立即改变状态；`lsappinfo` 报告 bundle ID
  `com.rvosy.sakura.runtimev2.shell`、`fileType=APPL`，以及 release
  `.sakura-dev/Sakura Runtime v2.app` 路径。`start.sh` 轮次还覆盖 idle → bubble → composer →
  expanded → idle 和本地文本输入。两次启动均以 exit code 0 关闭，最终 Shell/Core/shared-lock-holder
  扫描为空。
- 项目负责人关于物理鼠标的证据明确获接受：向左、向上、向中央拖动，以及全部四种状态往返均通过。
- 精确 SHA `beea2ea10d513dc3d3cdca7804f80af45fbb518c` 的 CI：push platform matrix run
  `30117418138` 成功，Windows x64/macOS arm64/Linux x64 全部通过：
  https://github.com/Rvosy/Sakura/actions/runs/30117418138；既有 Draft PR platform matrix run
  `30117421223` 成功，三平台全部通过：
  https://github.com/Rvosy/Sakura/actions/runs/30117421223；Unit/UI run `30117421237` 成功，两个
  job 均通过：
  https://github.com/Rvosy/Sakura/actions/runs/30117421237。既有 Draft PR #147 早于本任务，未创建或修改。
- 已批准的受保护目录基线保持不触碰；本 docs 任务前的新鲜只读摘要为：`characters/` 不存在 / 0 files /
  0 bytes；`data/` 为 1 file / 939022 bytes；`runtime/` 为 49938 files / 2602963793 bytes；获批准的
  既有 pyc 为 2559 bytes，SHA-256
  `4f8dfdcb014ea40daa8ae7f0e0e1125841d20f2aee825f43f15fc65eaa6edcfd`。获批准的起始基线之后唯一新
  差异是 `data/logs/sakura-runtime.log` 增长 93448 bytes，现为 939022 bytes，SHA-256
  `2f450db0fdb911f97da107f47245705a7a4aae8793e4955f89ce12fca668830a`；`characters/` 和 Runtime
  文件数/bytes 均未变化。
- P0/P1 acceptance audit 为零。此 accepted 记录不宣称 WP-7-02 对 Spaces、多显示器、Retina、IME、
  签名、公证或发布打包的覆盖。
- 独立回退：revert 本 accepted docs commit 即可将 WP-1P-05A 恢复为 `stabilizing`；实现回退仍按既有
  逐提交路径进行；绝不触碰受保护目录。
