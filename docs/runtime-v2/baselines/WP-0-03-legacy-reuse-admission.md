# WP-0-03 旧迁移逐文件复用准入清单

> Phase / Work Package：Phase 0 / WP-0-03
>
> 审查日期：2026-07-15
>
> 工作分支：`refactor/tauri-runtime-v2`
>
> 固定取证分支：`feat/tauri-assistant-migration`
>
> 固定取证 commit：`190dfafd24f5c5226bff8b4347837b6e45d9a331`
>
> 迁移共同基线：`4e8dc7f0a6afbc391149046febeb0c796dd641b8`
>
> 前置：WP-0-01、WP-0-02 均为 `accepted`
>
> 最终状态：`accepted`

## 1. 结论摘要

本 Work Package 把旧迁移从可整体恢复的代码分支转换为固定来源、逐文件或逐模块审查的证据库。本清单不复制任何旧生产实现；所有内容均由 `git cat-file`、`git ls-tree`、`git diff-tree`、`git show` 和 `git diff` 只读取证得到。

67 个候选记录的最终分类如下：

| 分类 | 数量 | 约束 |
|---|---:|---|
| 准入：可逐文件复用 | 6 | 仅限无副作用的窗口几何、前端纯算法和双端帧 codec；仍需在归属 WP 重新验证 |
| 有条件准入：只复用纯逻辑、DTO、fixture 或测试场景 | 34 | 不复制旧生命周期根、状态所有权或共享数据写入；每项已绑定唯一 WP |
| 拒绝：不得进入 Runtime v2 | 17 | 生产结构不得复制；有效故障经验单独保留 |
| 延后：不属于 Phase 1A–3 | 7 | 已绑定 Phase 4 或 Phase 5，不在当前阶段编码 |
| 无归属：删除候选 | 3 | 不保留“以后可能需要”；没有后续复用承诺 |

Phase 1A–3 的“准入”与“有条件准入”共 40 项，全部绑定具体 Work Package。没有悬空复用项。旧迁移中的同步 Supervisor、巨型 `BrainHostApplication`、混合所有权 `DesktopAppState`、secondary window bridge、Python 启动 Tauri 链路、巨型设置/工作室脚本和源码字符串门禁均被拒绝。

## 2. 固定来源与来源未漂移证明

只读取证结果：

```text
git cat-file -t 190dfafd24f5c5226bff8b4347837b6e45d9a331
=> commit

git rev-parse feat/tauri-assistant-migration
=> 190dfafd24f5c5226bff8b4347837b6e45d9a331

git rev-parse origin/feat/tauri-assistant-migration
=> 190dfafd24f5c5226bff8b4347837b6e45d9a331

git merge-base 190dfafd24f5c5226bff8b4347837b6e45d9a331 dev
=> 4e8dc7f0a6afbc391149046febeb0c796dd641b8

git rev-list --left-right --count dev...190dfafd24f5c5226bff8b4347837b6e45d9a331
=> 0  28

git diff --name-only 4e8dc7f0a6afbc391149046febeb0c796dd641b8 190dfafd24f5c5226bff8b4347837b6e45d9a331
=> 151 paths

git diff --stat 4e8dc7f0a6afbc391149046febeb0c796dd641b8 190dfafd24f5c5226bff8b4347837b6e45d9a331
=> 151 files changed, 43309 insertions(+), 1849 deletions(-)
```

共同基线只用于确定旧迁移的差异全集；候选代码、测试和配置内容全部从固定 commit 的 tree 读取，不把共同基线或当前工作树当作旧实现来源。

## 3. 审查字段展开规则

下列每个候选记录的“来源 commit”均为：

```text
C190D = 190dfafd24f5c5226bff8b4347837b6e45d9a331
```

每行的依赖档案是该记录不可省略的一部分，展开后包含 Qt/PySide6、生命周期/进程所有权、全局状态/线程/同步阻塞、持久化/用户数据四类字段。

### 3.1 依赖与风险档案

| 档案 | Qt / PySide6 | 生命周期和进程所有权假设 | 全局状态、线程、同步和阻塞风险 | 持久化和用户数据依赖 |
|---|---|---|---|---|
| D0 纯模块 | 无 | 不拥有进程、窗口或 generation | 无全局可变状态；确定性同步计算 | 无文件、数据库或用户数据访问 |
| D1 WebView 表现 | 无 | 只拥有当前 WebView 的短期表现状态 | DOM、timer、animation callback；晚到回调和重入风险 | 默认无持久化；只能通过受控 Gateway 请求外部状态 |
| D2 Tauri 窗口 | 无 | 假定 Tauri 是窗口根，但不得拥有 Python 业务状态 | 主线程约束、DPI/monitor API、焦点/IME/点击穿透真实平台风险 | 只允许 `desktop.*`/`ui.*` 私有状态；不得写 legacy 共享数据 |
| D3 Rust 生命周期 | 无 | 旧代码由单个 Rust 线程拥有 Python 根进程，但没有受控进程树最终所有权 | `std::sync::mpsc`、`Mutex`、同步 `recv_timeout`、阻塞 `join/sleep/request` | 会创建临时资源；旧实现未遵守 ADR-0003 的应用锁和私有目录边界 |
| D4 Python Host | 目标上无 Qt，但可能经导入图间接加载 | 旧 `BrainHostApplication` 同时拥有 Assistant、TTS、Scheduler、插件、截图和写入 | `RLock`、后台 watcher、单 writer lock、同步请求分派和 join timeout | 可读取配置、角色并写聊天历史、事件和缓存，风险高 |
| D5 legacy Qt / 共享领域 | 直接或间接依赖 PySide6、QObject/QThread/QTimer | Qt 仍是旧生命周期根；不能作为 v2 Core 根 | Qt 信号、线程组、同步关闭、隐式主线程假设 | 直接读取或修改现有 `data/`、角色、插件或配置 |
| D6 安全边界 / DTO | 无或可隔离 | 不拥有进程；只表达 Envelope、错误、Snapshot 或资源描述符 | 解析、校验、大小/路径边界；错误时必须安全失败 | 不得暴露 credential、裸路径、Prompt；资源描述符必须 generation-scoped |
| D7 后续资源能力 | Qt/Rust/Python 混合 | TTS、截图、Scheduler 或主动事件属于 Phase 4+ | 设备线程、子进程、临时文件、timer、取消和资源回收风险 | 音频/截图 cache、TTS bundle、观察状态或后续领域数据 |
| D8 静态门禁 | 无运行依赖 | 不执行真实生命周期 | 只读源码字符串，无法证明竞态、进程树、WebView 或真实入口 | 无直接写入，但会制造错误安全感 |
| D9 共享数据写入 | 可能无 Qt，但操作 legacy 文件 | 假定单写入者或旧 Qt 事务模型 | 同步文件事务、回滚竞态、异常中断风险 | 直接触及共享 config/history/plugin 数据，必须服从 ADR-0003 |

### 3.2 验证档案

| 档案 | 自动测试 | 故障测试 | 真实验收 |
|---|---|---|---|
| V0 纯逻辑 | 边界值、随机/表驱动、双实现对拍 | 非法输入、溢出、空值 | 不需要真实应用；归属 WP 仍需集成验证 |
| V1 窗口 | Rust/JS 单测和真实 WebView E2E | 负坐标、超大窗口、快速切换、晚到 layout | 多屏、100/125/150% DPI、点击穿透、拖动、焦点和中文 IME |
| V2 生命周期 | Rust 状态机 + Fake Core + 后代进程 fixture | spawn/hello/initialize/backoff/shutdown/retry 竞态、忽略退出、Job 失败 | 真实 Tauri 退出/恢复后 5 秒内无后代残留 |
| V3 Core Host | Python pytest、import guard、Rust/Python fixture | 分片/合并/损坏帧、stdout 污染、stdin 关闭、初始化卡死 | bundled Python 的 hello/initialize/snapshot/shutdown |
| V4 IPC | Rust/Python codec、Router、Operation、golden fixture | 乱序、慢 writer、背压、阻塞 I/O/CPU、旧 generation | 真实窗口关闭、取消和控制请求保持响应 |
| V5 聊天/UI | deterministic fake/local Provider、Node 模块测试 | 取消/完成竞态、错误、Core 崩溃、晚到事件 | 真实角色、聊天、打字机、IME、主题和 Core 恢复 |
| V6 数据 | 脱敏 fixture、manifest 和 parser oracle | 备份/temp/replace/中断/未来 schema/双入口锁 | 真实 legacy Qt → Tauri v2 → legacy Qt，真实 `data/` 零变化 |
| V7 后续 Phase | 仅保留场景输入 | 由 Phase 4/5 重新定义 | 当前不执行 |
| V8 拒绝证据 | 可执行测试场景可以移植；源码字符串断言不移植 | 把竞态、泄漏、错误样例写入新 fixture | 禁止用旧实现通过代替 v2 真实验收 |

## 4. 依赖关系与模块所有权

| 旧模块族 | 依赖旧生命周期根 | Qt | 混合 Rust/Python/WebView 所有权 | 修改共享数据 | 可独立纯复用 | 结论 |
|---|---|---|---|---|---|---|
| `desktop/src-tauri/src/app_state.rs` | 是 | 否 | 是：Core、窗口、音频、截图、启动快照同体 | 会创建 cache 并转发设置写入 | 仅启动路由纯函数 | 拒绝整体；条件复用路由真值表 |
| `desktop/src-tauri/src/brain_host.rs` | 是 | 否 | Rust 同时做 Supervisor、同步 Router、状态缓存 | 临时资源集合 | 仅状态 DTO、路径解析和故障场景 | 拒绝 Supervisor/ManagedProcess |
| `app/brain_host/application.py` | 是 | 间接风险 | 是：Assistant/TTS/截图/Scheduler/插件/历史 | 是 | 几乎无 | 拒绝整体 |
| `app/brain_host/secondary_windows.py` | 是 | 间接风险 | 是：窗口 RPC 与 Python 领域写入混合 | 是，大量 config/Studio/plugin 写入 | 少量分页/DTO 思路 | 拒绝整体 |
| 双端 `protocol.py` / `ipc.rs` | 否 | 否 | 否 | 否 | 帧 codec 是 | codec 准入；旧 Envelope 条件重写 |
| `desktop/frontend/pet/*` | 否 | 否 | 部分依赖旧 command/event 名 | 否 | 几何、字幕、气泡可独立 | 纯模块优先准入 |
| `app/core/assistant_service.py` | 部分 | 否 | Python 自有，但把业务 facade 与执行器耦合 | 间接写历史 | Interaction 状态/测试可提取 | 条件准入 |
| TTS / capture / backchannel | 部分 | 混合 | Rust/Python/WebView 跨层 | cache/资源文件 | 部分算法和故障场景 | 延后 Phase 4 |
| settings / Studio | 是 | 间接 | secondary bridge、全局 task manager、巨型脚本 | 大量共享配置/角色资源 | 个别竞态测试 | 拒绝生产结构 |

核心依赖链：

```text
旧 Python main.py
-> 启动 Tauri（错误的生命周期根）
-> DesktopAppState
   -> 同步 BrainHostSupervisor
      -> BrainHostApplication
         -> Assistant + TTS + Scheduler + plugins + history
   -> audio + capture + secondary windows
   -> WebView app.js

可切断并独立复用：
codec / golden framing vector / window geometry / layout algorithm /
subtitle timer / bubble timer / pure theme normalization / executable fault scenarios
```

### 4.1 用户要求能力覆盖索引

| 必查能力 | 记录 | 旧迁移事实 |
|---|---|---|
| Tauri Shell | R01–R03 | 有完整 crate，但组合根提前启动所有能力；只保留最小 crate/config 形态 |
| 透明窗口与透明度 | R02、R13、R18 | 有透明原生窗口和 CSS/图片淡入淡出；没有独立、可复用的原生窗口 opacity 状态机，透明度必须在 WP-1A-02/3-03 重新验证 |
| 拖动、点击穿透、DPI、IME、焦点 | R06、R07、R09、R10 | 有 Tauri API、几何和 JS composition guard；没有真实多 DPI/IME 证据 |
| 应用锁与 legacy Qt 入口 | R03、R24、R25、R64 | 只有 Tauri single-instance plugin 和旧 Qt 文件副本；没有 ADR-0003 shared named mutex |
| Supervisor、进程树、启动/退出/恢复 | R26–R31 | 有同步 Supervisor/Fake Core 测试；没有 Job Object、后代树和完整竞态 |
| IPC codec、Envelope、Router、generation、Snapshot | R32–R43、R47、R57 | codec 可拆；Envelope 需重写；并发 Router、revisioned Snapshot 不存在 |
| Fake Core、Core Host、生命周期协议 | R29、R34、R36–R38、R47 | fixture/codec 测试有价值；旧入口在 transport 前初始化，server 同步串行 |
| Python Assistant Adapter / Facade | R39、R42–R44、R53–R56 | 薄 facade 形态和 import guard 可参考；巨型 Application 与 Qt stub 被拒绝 |
| 聊天垂直链、角色、历史 | R16–R19、R40、R44、R49–R51、R56、R63 | DTO/UI/测试场景可拆；真实 v2 聊天与双向数据门禁不存在 |
| 测试夹具、golden、故障测试 | R29、R30、R34、R42、R47–R50、R57、R63–R65 | 可执行场景优先；源码字符串测试被拒绝 |
| 主题、路径、DTO、纯算法 | R06、R09、R16、R17、R28、R31–R35、R38、R40、R43 | 纯模块/DTO 有准入价值，但不得携带旧所有权或裸路径 |
| TTS、截图、Scheduler | R45、R46、R52、R57–R59、R65 | 只保留 Phase 4 经验和测试输入，不提前实现 |

## 5. 逐文件 / 模块准入记录

所有记录的来源 commit 字段均为 `C190D`。记录中的“保留/删除/重写”是在未来归属 WP 重新实现时的边界，不表示本 Work Package 已复制代码。

### 5.1 Shell、窗口、入口与恢复 UI

| ID | 旧迁移原路径或模块 | 来源 | 用途 / 计划归属 | 复用原因与依赖 | 可保留 | 必须删除 | 必须重写 | Assistant 语义风险 | 测试、故障与真实验收 | 失败替代方案 | 最终结论及理由 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R01 | `desktop/src-tauri/Cargo.toml`、`build.rs`、`src/main.rs` | C190D | 最小 Tauri crate；WP-1A-01 | 目录和最小入口有参考价值；D2 | crate 名、`tauri-build` 基本形态 | `rodio/xcap/single-instance/dialog` 等非 WP-1A-01 依赖 | 依赖版本、feature、release 配置和构建资源 | 无直接语义风险 | V1；必须真实 dev/release 启动且不启动 Python | 从空 crate 按当前工具链创建 | 有条件准入：只复用最小清单形态，不复制依赖集合 |
| R02 | `desktop/src-tauri/tauri.conf.json` 的 main window | C190D | 透明 Shell 初始窗口；WP-1A-01 | 单透明窗口参数是可验证起点；D2 | label、无装饰、透明、初始隐藏的候选值 | secondary window 假设和 Phase 3 资源 scheme | 尺寸、可见策略、DPI/任务栏/阴影和 CSP | 无 Assistant 语义；有窗口行为风险 | V1；真实白闪、退出和透明表面验收 | 使用 Tauri 默认窗口逐项启用参数 | 有条件准入：配置值只能作为技术门起点 |
| R03 | `desktop/src-tauri/src/lib.rs` | C190D | 旧桌面组合根；考察 WP-1A-01 / WP-1A-04 | 证明 Tauri 可成为根，但 setup 立即创建 Brain/TTS/capture/tray，且单实例插件不等于共享数据锁；D3 | `run()` 外壳和 ExitRequested 关闭意图仅作参考 | 所有 Brain、音频、截图、托盘、secondary command 注册；`tauri-plugin-single-instance` 锁假设 | WP-1A-01 空 Shell composition 和 WP-1A-04 named mutex | 高：提前启动真实 Core 和写入者 | V8；保留退出顺序和锁不等价案例 | 在 WP-1A-01 从最小 Builder 重写 | 拒绝：不得进入 Runtime v2 |
| R04 | `app_state.rs::StartupRoute`、`startup_routing_decision` | C190D | Shell 状态路由真值表；WP-1D-01 | 少量纯状态组合可防止恢复期空白和重复开窗；D0 | 输入→路由决策、同 generation 幂等场景 | `BrainHostPhase` 旧命名和窗口副作用 | 使用 SupervisorState/CoreReadiness/ShellRoute 正交模型 | 低；不得把 UI 路由变成真相源 | V0 + V2；旧 generation、restarting、diagnostic 真值表 | 按 ADR 状态模型重写表驱动 reducer | 有条件准入：只复用真值表和测试场景 |
| R05 | `app_state.rs::DesktopAppState` | C190D | 旧全局 AppState；考察 WP-1D-01 | 同时拥有 Core、startup、capture、audio、preferences，是治理明确禁止的混合所有权；D3 | 仅保留已发现的重置顺序经验 | 整个结构体和 command 聚合 | 分离 Shell state、Supervisor、Gateway、资源 registry | 高：Rust 可修改/缓存 Python 业务状态并跨 generation 混用 | V8；保留“Core 不可用时清音频/截图/窗口”故障场景 | 按 ADR-0001/0002 拆分小状态机 | 拒绝：不得进入 Runtime v2 |
| R06 | `windows.rs::PhysicalBounds`、`compute_pet_window_position*` | C190D | 多屏/负坐标/锚点几何；WP-1A-02 | 纯函数、无副作用，覆盖工作区夹取和固定立绘锚点；D0 | 两个位置算法及表驱动样例 | secondary window 常量 | 类型命名可按 v2 调整，算法无需改变业务语义 | 无 | V0 后接 V1；多屏、负坐标、窗口大于工作区 | 重新按公式实现并对拍旧向量 | 准入：可逐文件复用 |
| R07 | `windows.rs::start_dragging`、`set_click_through`、`set_pet_visible`、`apply_pet_window_layout`、`focus_window` | C190D | 拖动、穿透、焦点、DPI；WP-1A-03 | API 调用顺序和 anchor→物理坐标转换有经验价值；D2 | Tauri API 调用候选、物理/逻辑换算思路 | secondary window focus、强制 always-on-top、隐式 set_focus | 命中区域、IME、Alt+Tab、显示/隐藏、错误恢复 | 无 Assistant 语义；高平台风险 | V1，不能以单元测试代替真实鼠标/IME | 用最小 Windows/Tauri 平台模块逐项验证 | 有条件准入：只复用平台调用与换算经验 |
| R08 | `windows.rs::SecondaryWindowSpec`、`open_secondary_window*` | C190D | 旧次级窗口桥；考察 WP-1D-02 | 把设置/Studio/历史/诊断统一塞入窗口模块，提前跨 Phase；D2/D3 | `run_on_main_thread` 的故障经验 | 设置、Studio、历史窗口和强制置顶策略 | WP-1D-02 只重写 diagnostics/runtime repair 最小窗口 | 中：窗口可触发共享数据写入 | V8；保留主线程创建和焦点失败场景 | WP-1D-02 建独立最小 diagnostics 窗口 | 拒绝：不得进入 Runtime v2 |
| R09 | `desktop/frontend/pet/layout.js` | C190D | 桌宠窗口/立绘/气泡/input 纯布局；WP-1A-02 | 纯算法，已覆盖尺寸夹取和 portrait anchor；D0 | `computePetLayout`、常量和输出模型 | DOM 写入 helper 可选择分离 | 与 WP-1A-02 状态名和 DTO 对齐 | 无 | V0 + V1，与 Rust 几何对拍 | 依据现有 Qt layout 模型重写 | 准入：可逐文件复用 |
| R10 | `desktop/frontend/pet/pet_controller.js` 的 composition guard 与 revisioned layout drain | C190D | IME 输入和布局竞态；WP-1A-03 | `compositionstart/end`、旧异步 layout 不覆盖新值的经验有效；D1 | IME guard、revision/last-applied 场景 | chat/capture/settings/Assistant busy 耦合 | 只保留窗口技术门所需 controller | 中：旧 store 字段可能改变聊天表现 | V1；真实 IME 候选框、快速连续布局、关闭中请求 | 小型 input/window controller 重写 | 有条件准入：只复用 IME 和竞态算法 |
| R11 | `desktop/frontend/pet/bubble_controller.js` | C190D | 气泡自动隐藏表现；WP-3-03 | 定时器可注入、状态独立、无后端依赖；D1 | 整个 controller 和 timer 测试向量 | 无 | 仅命名/样式 hook 可调整 | 低；只影响表现 | V5；hover/speaking/settled/disable/late timer | 重新实现等价有限状态机 | 准入：可逐文件复用 |
| R12 | `desktop/frontend/pet/subtitle_controller.js` | C190D | 完整回复打字机和跳过；WP-3-03 | 单文件、timer 可注入、sequence 丢弃晚到回调；D1 | 分段、语言、取消、完成状态机 | 不引入 token streaming | 与 v2 Chat DTO 字段对齐 | 低；不得把跳过映射为 Core cancel | V5；晚到 timer、空段、立即跳过、IME 不受阻塞 | 简单 requestAnimationFrame/timer 状态机重写 | 准入：可逐文件复用 |
| R13 | `desktop/frontend/pet/portrait_controller.js` | C190D | 立绘预加载与淡入淡出；WP-3-03 | 表现独立且有 transition token；D1 | 资源选择、晚到 load 丢弃、fallback 场景 | 旧 asset URL 和固定 300ms | 受控资源 URL、解码失败、reduced-motion | 低；可能错误映射 tone/portrait | V5；真实图片、失败、快速切换和 DPI | 用 CSS class + generation token 重写 | 有条件准入：只复用状态机和测试场景 |
| R14 | `desktop/frontend/core/store.js` | C190D | WebView 短期表现 store；WP-3-03 | 小型 store 比巨型全局 AppState 更接近所有权原则；D1 | subscribe/reset、presentation-only 字段 | audio/observation 等 Phase 4 字段 | 明确草稿、bubble、composer、当前 generation 表现状态 | 中：不得成为 Python 业务真相 | V5；reset、窗口关闭、旧事件 | 使用不可变小 reducer 重写 | 有条件准入：只复用 store 形态 |
| R15 | `desktop/frontend/core/bootstrap_loader.js` | C190D | generation 切换与重新水合加载；WP-3-05 | epoch 可丢弃晚到 bootstrap，且无副作用；D1/D6 | coalesce、reset、late result 丢弃 | 数字 `sessionGeneration` 作为权威身份 | 改用 generationId，并在 revision 不连续时重取 Snapshot | 中：错误身份会水合旧角色状态 | V5；崩溃、连续 ready、旧请求晚到 | 在 Snapshot client 中重写同等 epoch 机制 | 有条件准入：只复用并发控制算法 |
| R16 | `app/config/theme.py` | C190D | 无 Qt 主题 DTO/纯算法；WP-3-01 | 数据模型、颜色校验、mix 为纯逻辑；D0 | 颜色字段、hex normalize、mapping 和 mix | 未批准的 acrylic/blur 模式及隐式导入 character loader | 主题所有权、schema 与当前角色只读映射 | 低；错误默认值可能改变现有主题 | V0 + V5；与 legacy 当前主题 fixture 对拍 | 在 Adapter 内定义最小公开 Theme DTO | 有条件准入：只复用纯 DTO/算法 |
| R17 | `desktop/frontend/core/theme.js` | C190D | Theme DTO→CSS variables；WP-3-03 | 纯映射、易测试；D0/D1 | CSS variable mapping | 默认 `gaussian_blur` 和未批准效果 | v2 theme schema、solid fallback、reduced motion | 低 | V0 + V5；非法/缺失色和真实主题 | 直接在 UI reducer 中重写映射 | 有条件准入：只复用映射函数 |
| R18 | `desktop/frontend/index.html`、`desktop/frontend/styles.css` | C190D | 立绘/气泡/composer 表现稿；WP-3-03 | 现有视觉和锚点选择可作为验收输入；D1 | 语义化区域、设计 token、reduced-motion 样式 | 工具确认、截图、TTS 原型、设置/Studio/历史按钮 | Phase 3 最小 markup、状态和可访问性 | 中：旧 UI 混入后续功能 | V5；真实主题、长文本、DPI、IME、动画不阻塞 | 根据 Phase 3 wireframe 重建小页面 | 有条件准入：只复用样式片段和交互稿 |
| R19 | `desktop/frontend/app.js` | C190D | 旧前端组合根；考察 WP-3-04 | 同时装配聊天、TTS、截图、设置、恢复和原型按钮；D1 | 只保留事件竞态列表 | 整个组合文件和旧 command/event 名 | 按 Shell、chat、presentation client 分模块 | 高：混合跨 Phase 状态和旧 Envelope | V8；提取旧 generation、重连、late event 场景 | WP-3-04 用小 composition root 重写 | 拒绝：不得进入 Runtime v2 |
| R20 | `pet/context_menu.js`、`menu_actions.rs`、`tray.rs` | C190D | 右键菜单/托盘 | Phase 1A–3 Work Package 未批准托盘或这些菜单能力；D1/D2 | 无 | 全部候选 | 不重写；若未来批准需新 Work Package | 可能写设置并扩大范围 | V8 | 删除候选，未来重新取证 | 无归属：删除候选 |
| R21 | `desktop/frontend/runtime-repair/` | C190D | Runtime Repair 页面稿；WP-1D-02 | 页面小、无自动修复，符合早期安全操作方向；D1 | 基础说明、状态/错误区域 | “Brain”旧术语、仅 refresh、缺少版本/目录/退出 | Desktop/Core/Protocol、日志位置、重试/打开位置/退出 | 无 Assistant 语义；有误导风险 | V5；缺 Runtime/协议不兼容/路径脱敏/退出 | 用静态 HTML/CSS 重写 | 有条件准入：只复用页面布局和文案结构 |
| R22 | `desktop/frontend/diagnostics/` | C190D | 最小 diagnostics 表现；WP-1D-02 | card renderer 和 raw snapshot 展示有参考价值；D1/D6 | 只读 card/JSON renderer | `host_call`、插件/MCP/TTS/Scheduler 提前展示、私密字段风险 | 最小诊断 DTO、脱敏、受控动作 | 中：可能泄露路径/配置或制造假 readiness | V5；敏感字段、未知错误、离线页面 | 新建严格 schema 的只读页 | 有条件准入：只复用 renderer/test idea |
| R23 | `capabilities/default.json`、`tauri.conf.json::app.security` | C190D | WebView 权限/CSP；WP-2-04 | 禁止 shell/fs、限制 remote script 的原则有效；D6 | deny-by-default 思路、`object-src 'none'`、`frame-ancestors 'none'` | 所有窗口共享 capability、dialog 对所有窗口开放、旧 IPC host | 按窗口 command allowlist、payload 上限和导航限制 | 中：权限过宽可绕过 Gateway | V4；未知 command、错误窗口、导航和注入 | 从最小 capability/CSP 重新建立 | 有条件准入：只复用安全约束，不复用文件原配置 |
| R24 | `main.py`、`start.bat` | C190D | 旧默认生产入口；考察 WP-1A-04 | Python 常驻启动 Tauri，直接违反生命周期根；D5 | “不自动回退 Qt”的失败提示经验 | Python `subprocess.run(Tauri)`、环境注入和默认链路 | Tauri 直接入口、显式 legacy Qt 脚本 | 高：生命周期反转、退出和应用锁所有权错误 | V8；保留 missing binary 提示场景 | WP-1A-04 从当前入口安全切换 | 拒绝：不得进入 Runtime v2 |
| R25 | `legacy_qt_main.py` | C190D | 旧 Qt 回退副本；考察 WP-1A-04 | 是旧快照复制，缺少 WP-0-01 稳定化修复和 ADR-0003 named mutex；D5/D9 | 仅保留“显式 Qt 回退命令”目标 | 整文件复制、旧 QLockFile 和锁前 data 写入 | 从实施时当前 `main.py` 提取并接入共用 mutex | 高：丢失当前修复或污染共享 data | V1 + V6；真实双入口锁和 current Qt smoke | 由当前分支最新 Qt 入口生成 | 拒绝：不得复用旧文件 |

### 5.2 Supervisor、进程树、Fake Core 与生命周期

| ID | 旧迁移原路径或模块 | 来源 | 用途 / 计划归属 | 复用原因与依赖 | 可保留 | 必须删除 | 必须重写 | Assistant 语义风险 | 测试、故障与真实验收 | 失败替代方案 | 最终结论及理由 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R26 | `brain_host.rs::ManagedProcess` | C190D | 受控进程原语；考察 WP-1B-01 | 有 spawn/stdin/stdout/kill/wait 基本样例，但只杀根进程、无 Job Object、stderr 继承；D3 | 命令环境和句柄分离的测试输入 | `Child::kill` 作为最终回收、`stderr(Stdio::inherit)`、无后代验证 | `ManagedProcessTree`、Job Object、suspended spawn/安全失败、句柄语义 | 无 Assistant 语义；P1 进程泄漏风险 | V2；一层/多层后代、Job 失败、父进程已在 Job、强杀 | 使用 Win32 Job Object crate/FFI 从零实现 | 拒绝：不得进入 Runtime v2 |
| R27 | `brain_host.rs::BrainHostSupervisor`、`supervise`、`request_runtime` | C190D | Supervisor/Router；考察 WP-1B-02 / WP-2-01 | 串行单线程同时处理生命周期和普通请求，长请求阻塞 health/shutdown；D3 | 状态转移和失败分类样例 | 同步 mpsc loop、同步 request、阻塞 backoff/join、自动重启实现 | 串行意图状态机与独立并发 transport/router | 中：请求排序可改变聊天/取消语义 | V2 + V4；长任务、退出、retry、旧 generation | 分别实现 Supervisor actor 和 IPC router | 拒绝：不得进入 Runtime v2 |
| R28 | `brain_host.rs::BrainHostPhase`、`BrainHostStatus`、`BrainHostDiagnostic` | C190D | Supervisor 状态 DTO；WP-1B-02 | 纯 serde DTO 和 diagnostic 字段可作为命名对照；D6 | restart count、PID/generation/forced-stop 诊断需求 | `BrainHost` 术语、把 readiness 合入 phase、数字 generation 权威化 | 对齐 SupervisorState/CoreReadiness 和 generationId | 低；状态合并会误导 UI | V2；状态真值表与旧回调隔离 | 依据 ADR-0001 定义新 DTO | 有条件准入：只复用字段需求和测试 |
| R29 | `tests/fixtures/fake_brain_host.py` | C190D | Fake Core 基础 fixture；WP-1B-03 | 可执行子进程，已有 healthy、ignore hello/shutdown、crash、protocol mismatch 模式；D3 | 帧循环、环境驱动 mode、launch record 结构 | credential 明文记录、旧 Envelope、无后代/initialize/stderr 模式 | v2 Fake Core CLI、脱敏记录、后代树和故障矩阵 | 无业务语义；测试泄密风险 | V2；必须扩展 ADR-0001 全矩阵 | 重新写更小的 fixture 可执行文件 | 有条件准入：只复用 fixture 骨架和场景名 |
| R30 | `brain_host.rs` 内 `#[cfg(test)]` Supervisor 测试 | C190D | 恢复/竞态门禁输入；WP-1B-04 | 真进程覆盖握手、restart limit、missing Python、ignore shutdown、stalled hello；D3 | 故障名称、deadline 断言、session 更新检查 | 只验证根进程、缺少 Job/后代/连续 retry/旧 reader 回调 | 按 ADR-0001 扩充并改为新状态机 | 无业务语义 | V2；重复执行、句柄/计时器/后代零泄漏 | 从 ADR 矩阵重新写测试 | 有条件准入：只复用故障场景 |
| R31 | `brain_host.rs::BrainHostLaunchConfig`、`resolve_python_executable` | C190D | bundled Python 定位；WP-1C-04 | 路径解析是独立小逻辑，覆盖显式 override 和平台候选；D0/D6 | 路径候选、canonicalize 和 missing 错误场景 | `SAKURA_DESKTOP_EXE` 反向注入和 debug 仓库假设 | release resource root、环境 allowlist、路径诊断脱敏 | 无 Assistant 语义 | V3；开发/release/bundled/missing/非可执行路径 | 由 Tauri resource resolver 重写 | 有条件准入：只复用解析测试和错误分类 |

### 5.3 Core Host、IPC、Envelope、Router、Snapshot 与 Gateway

| ID | 旧迁移原路径或模块 | 来源 | 用途 / 计划归属 | 复用原因与依赖 | 可保留 | 必须删除 | 必须重写 | Assistant 语义风险 | 测试、故障与真实验收 | 失败替代方案 | 最终结论及理由 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R32 | `app/brain_host/protocol.py::encode_frame`、`FrameDecoder`、`decode_frame` | C190D | Python 长度前缀 codec；WP-1C-01 | 纯 4-byte big-endian + UTF-8 JSON codec，支持分片/合并/上限；D0/D6 | codec、稳定错误码、incremental decoder | 旧 Envelope 校验调用 | 将 payload 校验注入或换成 v2 Envelope | 无 | V3/V4；跨语言 golden、损坏/超大/EOF | 重新实现同格式 codec 并对拍 | 准入：可逐文件复用 |
| R33 | `desktop/src-tauri/src/ipc.rs::encode_frame`、`FrameDecoder`、`read_frame`、`write_frame` | C190D | Rust 长度前缀 codec；WP-1C-01 | 与 Python 互通、纯 I/O/解析边界；D0/D6 | codec 和错误类型结构 | 旧 Envelope 校验和同步上层假设 | v2 Envelope、异步 reader/writer 接口 | 无 | V3/V4；Python/Rust 同 fixture | 使用小型独立 crate/module 重写 | 准入：可逐文件复用 |
| R34 | `tests/fixtures/brain_host_frame_v1.json` | C190D | 跨语言 golden framing vector；WP-1C-04 | 固定 frame hex 可证明 canonical framing；D0/D6 | 原始 framing vector 作为 legacy compatibility case | 将旧消息当 v2 基础 Envelope | 新增 v2 lifecycle fixture；旧 fixture 标注 legacy | 无 | V3/V4；双端读写 | 由规范生成新的人工审核 fixture | 有条件准入：只作为 legacy framing fixture |
| R35 | 双端 `protocol.py` / `ipc.rs` 的 Envelope、`SessionTracker` | C190D | Envelope 错误案例与会话校验；WP-2-06 | 类型严格、duplicate ID、sequence gap、stable error 有测试价值；D6 | 错误案例和 strict validation 思路 | `protocol` 单整数、`session_id`、强制连续 sequence、stream kinds | protocol major/minor、generationId、kind/name/deadline/priority、可选 sequence | 中：错误协议会改变取消和乱序语义 | V4；乱序、旧 generation、未知 kind、缺 capability | 从 ADR-0002 schema 重写 | 有条件准入：只复用错误案例和校验测试 |
| R36 | `app/brain_host/transport.py`、`server.py` | C190D | Python transport/server；考察 WP-1C-01 / WP-2-01 | 有 stdout 只写帧和 write lock 经验，但 server 同步串行、业务直接在 reader 线程执行；D4 | stdout 纯协议和 clean EOF 场景 | 直接 writer、同步 `handle_request`、`SessionTracker` 串行 | reader/control dispatcher/single writer queue/bounded task registry | 高：长聊天阻塞 health/shutdown | V3/V4；真实阻塞 sleep/I/O/CPU | 新建最小 Host transport/control plane | 拒绝：不得进入 Runtime v2 |
| R37 | `app/brain_host/__main__.py` | C190D | Python Core 入口；考察 WP-1C-01 | 设置 headless env 和 stderr 错误码有经验，但在建立 transport 前调用 `initialize()`；D4 | stdout/stderr discipline、显式退出码场景 | `SAKURA_HEADLESS` 兼容开关、hello 前初始化、导入巨型 application | 最小 import、先 transport/hello/health，再 initialize | 高：启动阻塞和 Qt import 风险 | V3；import guard、无配置、初始化卡死 | 从只依赖 stdlib/codec 的入口重写 | 拒绝：不得进入 Runtime v2 |
| R38 | `application.py::BrainHostConfig`、`app/brain_host/errors.py` | C190D | 启动配置与错误 DTO；WP-1C-01 | env strict parse、机器错误码、details shape 有价值；D6 | error shape、必填字段校验 | session_id 术语、单 protocol version、base_dir 直接 resolve 暴露 | generation credential、major/minor、capabilities、脱敏 diagnostics | 低 | V3；错误 env、credential、版本、路径 | dataclass/TypedDict 重新定义 | 有条件准入：只复用 DTO 形态和错误用例 |
| R39 | `app/brain_host/application.py::BrainHostApplication` | C190D | Assistant/Core 组合根；考察 WP-3-01 | 1,800+ 行聚合 Assistant、TTS、backchannel、Scheduler、插件、截图、历史、设置和 watcher；D4/D9 | 仅保留 readiness、关闭顺序和竞态清单 | 整个类、全局消息表、watcher、同步 request dispatcher | 小 Core Host + Adapter/Facade + 独立领域 service | 高：极易改变现有 Assistant 语义和数据写入 | V8；保留 busy、late watcher、shutdown 场景 | 按 WP-3-01 逐服务适配 | 拒绝：巨型 BrainHostApplication 不得进入 v2 |
| R40 | `app/brain_host/dto.py::chat_reply_dto`、`agent_progress_dto` | C190D | 基础聊天公开 DTO；WP-3-02 | 将 `ChatReply` 转为公开 segment，未暴露 continuation context；D6 | text/translation/tone/portrait/suppressTts 的公开字段候选 | TTS 专属字段在 Phase 3 的强依赖、旧 version 命名 | 对齐冻结 Chat event/Operation DTO 和公开角色 schema | 中：字段遗漏可能改变 UI 表现但不应改变 Core 语义 | V5；完整回复、空段、表达/立绘、敏感字段排除 | 在 Adapter 内手写最小 DTO | 有条件准入：只复用公开字段选择和测试 |
| R41 | `dto.py::startup_state_dto`、`menu_preferences_dto` | C190D | 旧 startup/Snapshot；考察 WP-1C-02 / WP-2-05 | 混入 `base_dir` 裸路径、设置、模型、插件、MCP、TTS 和 UI layout，没有 revision/generation；D4/D6 | 仅保留“公开角色摘要”字段清单 | 整个 DTO 和 runtime 统计 | Core Snapshot schemaVersion/generationId/revision/readiness/components | 高：泄露路径并混合所有权 | V8；保留敏感字段排除和 revision gap 场景 | 按 ADR-0002 从零定义 Snapshot | 拒绝：不得进入 Runtime v2 |
| R42 | `tests/unit/test_assistant_service.py` 的 blocking/cancel/close 场景 | C190D | 阻塞任务隔离测试输入；WP-2-02 | 真线程 Event 覆盖 busy、取消、关闭回调和 scheduler stop；D4 | blocking pipeline fixture、cancel/close 次序 | 把单 worker executor 当最终执行平面 | 加 health/shutdown 并发、阻塞 I/O/CPU/非协作 worker | 无直接语义风险 | V4；控制面必须在故障期间响应 | 重新写独立 blocking fixture | 有条件准入：只复用故障测试 |
| R43 | `app/core/assistant_service.py::InteractionSnapshot`、`InteractionHandle`、`_Interaction` | C190D | Operation/取消状态输入；WP-2-03 | request/interaction ID、唯一终态和 snapshot 有参考价值；D4/D6 | 状态名、cancel/result/snapshot 语义测试 | `Future.result` 同步等待、单 active 全局 busy、无 generation/deadline | generation-scoped Operation registry、幂等终态和 deadline | 中：取消与完成竞态可能改变用户可见终态 | V4；完成/取消同时、重复取消、Core 重启 | 按 ADR-0002 Operation 重写 | 有条件准入：只复用状态语义和测试 |
| R44 | `app/core/assistant_service.py::AssistantApplication` | C190D | 无 Qt Assistant Facade；WP-3-01 | 是旧迁移中最接近 Adapter/Facade 的边界，复用现有 ChatPipeline；D4 | 方法边界、pending action 不泄露 continuation 的思路 | 自建 ThreadPoolExecutor、单 active、shutdown callbacks、直接工具确认 | 由 Core execution plane 注入执行器；基础聊天只暴露 Phase 3 能力 | 高：可能改变并发、取消和工具语义 | V3/V5；等价性、import guard、readiness、legacy Qt 回归 | 在 `app.core_host` 建薄 Adapter | 有条件准入：仅复用 facade 形态和等价测试 |
| R45 | `app/brain_host/pending_actions.py`、`chat_pipeline.py::pending_actions_from_result` | C190D | 工具确认 Action ID | Tools 确认不属于 Phase 1A–3；D4 | 仅保留 session-bound/TTL/late action 故障经验 | 当前生产候选 | Phase 4 建立 Action ID/Operation 契约 | 可能改变工具确认语义 | V7 | Phase 4 重新审查当前代码 | 延后：绑定 Phase 4 Action ID 工具确认 |
| R46 | `app/brain_host/scheduler.py`、`app/backchannel/decision.py`、`headless_service.py` | C190D | Headless Scheduler/主动接话 | 属于主动事件，且旧线程 Timer/Executor 生命周期需重审；D7 | 纯 decision service、token 丢弃晚结果和 scheduler 场景 | 线程式 `PeriodicScheduler` 作为 Core 控制调度器 | Phase 4 Operation、优先级、关闭和受控 worker | 可能改变主动互动时机 | V7 | Phase 4 以当前 dev 领域实现重新取证 | 延后：绑定 Phase 4 主动事件调度 |
| R47 | `tests/unit/test_brain_host_protocol.py`、`test_brain_host_server.py` | C190D | codec/import guard/bundled Python 冒烟输入；WP-1C-04 | 包含分片/合并/非法帧、stdout 纯帧、无 Qt import、真实 `-m` 子进程；D6 | 可执行 fixture 和断言意图 | 对旧 BrainHostApplication/readiness 的断言 | v2 lifecycle fixture、bundled Python、initialize/shutdown | 低 | V3；必须同时由 Rust 端读取 fixture | 从最小 Core Host 测试重写 | 有条件准入：只复用测试场景与 fixture 结构 |
| R48 | `tests/unit/test_tauri_desktop_layout.py` 及各测试中的源码字符串 wiring 断言 | C190D | 旧迁移门禁 | 大量只读源码、检查 token 存在，不执行 Tauri、WebView 或进程链；D8 | 仅保留需要验证的安全意图清单 | 所有字符串存在性门禁 | 编译/Rust test/WebView E2E/真实故障测试 | 高：可能掩盖 P1 | V8 | 为每个意图写可执行测试 | 拒绝：不得作为 Runtime v2 门禁 |
| R49 | `tests/unit/test_tauri_pet_frontend.py` 的 Node 可执行模块测试 | C190D | 前端纯算法与表现测试；WP-3-03 | 真执行 JS 模块，覆盖 layout 对拍、late callback、portrait、bubble 和 context position；D1 | Node runner 模式和纯模块用例 | 静态 markup/source token 断言 | v2 DTO、WebView E2E、IME/物理视觉 | 低 | V5 | 使用 Vitest/Node 或现有 runner 重写 | 有条件准入：只复用可执行用例 |
| R50 | `tests/integration/test_tauri_brain_chat_contract.py` 的可执行 Python/Node 场景 | C190D | Headless 真实聊天链；WP-3-02 | 覆盖 progress/reply/error/cancel、Action ID 不泄露、history append、并发 writer；D4/D9 | deterministic pipeline、唯一终态和敏感字段排除场景 | 巨型 application fixture、Tools 路径和旧 event 名 | 冻结 IPC、fake/local Provider、Operation 和 degraded history | 中：测试本身固定旧业务细节 | V5/V6 | 基于当前 ChatPipeline 建新 contract fixture | 有条件准入：只复用业务场景和 oracle |
| R51 | `desktop/frontend/chat/chat_controller.js` | C190D | Fake Core 聊天表现；WP-3-03 | interaction ID 过滤、cancel/reply/error UI 状态可参考；D1 | presentation state transition 和错误显示 | tool confirm、TTS 调用、旧 invoke/event 名、首个事件自动认领 ID | Chat Operation client、generation 过滤、打字机 skip 分离 | 中：错误认领会显示旧回复 | V5；fake core success/slow/error/cancel/restart | 小 reducer + command client 重写 | 有条件准入：只复用状态场景 |
| R52 | `desktop/frontend/chat/confirmation_view.js` 及确认相关 UI | C190D | 工具确认表现 | Tools 不属于 Phase 1A–3；D1 | Action ID-only 展示和敏感上下文排除经验 | 当前生产候选 | Phase 4 重新定义确认 UI | 可能改变工具授权语义 | V7 | Phase 4 重新设计 | 延后：绑定 Phase 4 Action ID 工具确认 |
| R53 | `app/agent/__init__.py`、`app/agent/mcp/__init__.py`、`app/core/bootstrap.py`、`app_context.py`、`extensions.py` 的 lazy import / 无 Qt import 边界 | C190D | Assistant Adapter import guard；WP-3-01 | 证明读取配置时可避免加载 Agent/MCP/TTS/Qt 重图；D4/D5 | import guard 用例、延迟 import 原则 | 为迁移而全局改公共导入且无必要性证明的部分 | 仅在 import-guard 失败时做最小 lazy import | 中：公共导入行为变化可影响插件/legacy Qt | V3/V5；legacy import API 和 Qt smoke 回归 | Adapter 内局部 import，不改公共包 | 有条件准入：只有真实 import blocker 时复用 |
| R54 | `app/core/resource_manager.py` 的 `SAKURA_HEADLESS` Qt stub | C190D | 无 Qt Core 兼容；考察 WP-3-01 | 用假的 QObject/QThread/QTimer 掩盖模块直接 Qt 依赖，生命周期语义不等价；D5 | 只保留“import guard 必须失败而不是偷偷 stub”经验 | 全部 stub 分支 | 拆出真正无 Qt Resource 接口或 Adapter | 高：关闭、线程和 timer 语义改变 | V8；新增禁止 stub/import 测试 | Core Host 不导入 Qt ResourceManager | 拒绝：不得进入 Runtime v2 |
| R55 | `app/core/runtime_log.py` 的 headless stderr 分流 | C190D | stdout 污染防线；WP-1C-03 | stdout 只传协议帧的原则正确；D6 | headless 日志写 stderr 的测试场景 | 通用 `SAKURA_HEADLESS` 环境开关和未限流日志 | Core Host 专用 logger、有界 stderr、脱敏和 generation/PID | 低 | V3；持续 stderr、过载、敏感字段 | Core Host 直接配置专用 handler | 有条件准入：只复用日志分流原则和测试 |
| R56 | `app/config/character_loader.py`、`settings_service.py`、`app/ui/theme.py` 的主题/旧 layout 兼容改动 | C190D | 角色和主题只读 Adapter；WP-3-01 | 将 Theme DTO 从 Qt UI 抽离、读取时规范旧 `vertical_offset` 有价值；D5/D9 | 只读 theme mapping、旧字段读取 normalization | save 时删除/改写 legacy 字段、UI alias 覆盖、角色 manifest 写回 | Adapter 只读映射；v2 私有 `ui.*` 独立存储 | 高：写回会破坏 Qt 回退语义 | V5/V6；current schema 4 fixture、真实 data 零变化 | Adapter 层临时 DTO，不修改领域文件 | 有条件准入：只复用只读映射 |
| R57 | `audio.rs::AudioResourceRegistry`、`capture.rs::CaptureManager` 的资源校验；`screen_observation.py::build_screen_observation_from_private_resource`；对应 observation 路径测试 | C190D | 受控资源描述符经验；WP-2-05 | 路径 containment、大小/mime、TTL、一次读取/删除和 Windows 扩展路径案例有效；D6/D7 | 资源边界测试、opaque ID 和 cleanup 场景 | 裸路径进入跨进程 DTO、session_id 未真正授权、资源类型专用 registry | generation-scoped opaque token、窗口/command 范围、读取次数 | 中：裸路径泄露和跨 generation 访问 | V4；escape、过期、重复读、旧 generation、错误窗口 | 用测试字节资源先实现通用最小 registry | 有条件准入：只复用算法和故障案例 |

### 5.4 后续 Phase、共享数据和明确删除候选

| ID | 旧迁移原路径或模块 | 来源 | 用途 / 计划归属 | 复用原因与依赖 | 可保留 | 必须删除 | 必须重写 | Assistant 语义风险 | 测试、故障与真实验收 | 失败替代方案 | 最终结论及理由 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R58 | `app/voice/tts_provider.py`、`tts_synthesis_service.py`、`tts_synthesis.py`、`tts_types.py`、`tts.py`；`audio.rs` 播放；`frontend/audio/`；TTS tests | C190D | TTS 合成/播放拆分 | 合成与播放分层、cancel late result、受控 cache 有价值，但 Phase 3 禁止 TTS；D7 | 纯 DTO、合成/播放分界、故障场景 | Qt Provider 协调器、裸路径 DTO、旧线程/设备所有权 | Phase 4 audio ADR、Operation、设备故障和进程树回收 | 高：可能改变 TTS 时序和音频所有权 | V7 | Phase 4 从当前 dev TTS 重新取证 | 延后：绑定 Phase 4 TTS / audio ADR |
| R59 | `desktop/src-tauri/src/capture.rs` 捕获实现；`frontend/capture*`；`screen_observation.py` Qt capture；observation tests | C190D | 截图/观察 | 负坐标、DPI、裁剪、路径隔离经验有效，但 Phase 3 禁止截图；D7 | 纯坐标/resize/containment 测试 | capture window + Brain + proactive state 混合、裸路径跨进程 | Phase 4 手动截图、受控资源和真实多屏验证 | 中：可能采集或持久化用户屏幕内容 | V7 | Phase 4 使用脱敏/合成屏幕 fixture | 延后：绑定 Phase 4 截图与主动观察 |
| R60 | `app/core/settings_resource_tasks.py`、`app/ui/settings/resource_tasks.py` | C190D | 资源下载 Operation 候选 | 全局 `_MANAGERS`、daemon thread、同步下载和设置领域混合，不符合 generation/Operation；D4/D7 | task snapshot 字段和取消故障场景 | 全局 manager、daemon thread、模块别名替换 | 后续设置/资源 Work Package 使用 Phase 2 Operation | 中：全局任务可跨 generation 污染状态 | V8；保留重复启动、取消、关闭场景 | 后续按资源类型建立受控 Operation | 拒绝：不得进入 Runtime v2 |
| R61 | `app/brain_host/secondary_windows.py`；`frontend/settings/`、`frontend/studio/`；相关 settings/studio/secondary tests | C190D | 设置和工作室迁移 | Python bridge 1,400+ 行、settings.js 4,500+ 行、studio.js 2,000+ 行，混合事务/运行态/角色写入；D4/D9 | layout preview revision、rollback failure、workspace path 安全等经验 | 所有生产结构和源码字符串门禁 | Phase 5/6 分域仓库、Workspace/Draft、独立 Operation | 高：直接修改 Assistant、角色和用户配置 | V8；经验写入第 7 节 | Phase 5/6 从需求和当前 dev 重新设计 | 拒绝：巨型设置页、工作室脚本和 secondary bridge 不得进入 v2 |
| R62 | `frontend/history/`、`secondary_windows.py::history_page`、对应分页测试 | C190D | 历史窗口/分页 | 历史 UI 不属于 Phase 1A–3，分页 oracle 可留后续；D4/D9 | cursor paging、排除 `_debug` 场景 | 当前生产候选和 secondary bridge | Phase 5 历史分页 DTO/Gateway | 低到中：排序/游标可能改变用户观察 | V7 | Phase 5 基于当前 `ChatHistoryStore` 重写 | 延后：绑定 Phase 5 历史分页 |
| R63 | `test_tauri_brain_chat_contract.py::test_chat_send_emits_*_and_writes_compatible_history` 等 history fixture | C190D | Qt-compatible history 门禁输入；WP-3-06 | 使用真实 `ChatHistoryStore` 字段并断言兼容追加，场景可复用；D9 | 脱敏 history fixture、字段/文件名 oracle | 巨型 application 驱动、只测 Python parser、没有双入口锁 | 真实 Qt→Tauri→Qt、same mutex、data manifest 零变化 | 高：静态/单进程测试不能证明回退 | V6 | 使用 WP-0-02 fixture 和 deterministic Provider 重写 | 有条件准入：只复用历史兼容 oracle |
| R64 | `tests/integration/test_tauri_production_entry.py` 的 Python launcher、源码字符串和“single instance”断言 | C190D | 入口/兼容门禁；考察 WP-1A-04 / WP-3-06 | 只证明 Python 会启动 Tauri、源码不导入 Qt、插件 token 存在；D8 | import graph 子进程形式可由 R53 采用 | 其余全部断言 | 真实 Tauri 入口、named mutex、legacy Qt 双向数据门禁 | 高：会误判应用锁和回退完成 | V8 | 按 ADR-0003 真实双进程测试 | 拒绝：不得作为 v2 门禁 |
| R65 | `tests/integration/test_tauri_runtime_events.py`、observation proactive/scheduler 场景 | C190D | 主动事件、插件事件、screen awareness | 不属于 Phase 1A–3；旧场景包含 busy lane、关闭资源和迟到事件经验；D7 | 故障场景说明 | 当前生产结构和插件/MCP 提前接入 | Phase 4 主动事件/Operation/资源回收 | 中：主动互动时机和插件事件语义 | V7 | Phase 4 重新审查当前事件系统 | 延后：绑定 Phase 4 主动事件 |
| R66 | `app/plugins/*`、`plugins/sakura_mobile/*`、`app/agent/memory.py`、`memory_curation_task.py`、`memory_curation_worker.py` 的迁移改动 | C190D | 插件/MCP/Memory/headless 兼容候选 | Phase 3 明确禁止重写或接入这些领域；当前计划也没有为这些具体迁移改动分配 Phase 1A–3 WP；D5/D7 | 无 | 全部候选；相关现有业务继续以当前 dev 为真相源 | 不在本清单承诺重写 | 高：会扩大范围并改变领域语义 | V8 | 删除候选；未来有批准 WP 时重新取证当前代码 | 无归属：删除候选 |
| R67 | `app/storage/atomic.py::atomic_write_bytes`、`plugins/discovery.py` 的原子写改动 | C190D | 旧 settings rollback/plugin 写入辅助 | Phase 1A–3 不批准这些共享 whole-file/plugin 写入；`atomic_write_bytes` 被旧 secondary transaction 使用；D9 | 无 | 当前候选和 best-effort backup 误用 | 需要时按 ADR-0003 独立 whole-file writer 门禁 | 高：可能绕过 mandatory backup 和数据权限 | V8 | 删除候选；后续共享写需新 ADR/WP | 无归属：删除候选 |

## 6. 按未来 Work Package 分组的准入清单

“无旧实现准入”表示旧迁移没有满足该 WP 边界的生产代码，未来必须从 ADR/当前 dev/最小实现开始，不得为了填表强行复用。

| Work Package | 准入/条件准入记录 | 拒绝或缺口结论 |
|---|---|---|
| WP-1A-01 | R01、R02 | R03 被拒绝；空 Shell 不得启动 Python、音频、截图或托盘 |
| WP-1A-02 | R06、R09 | 只复用几何和纯布局；真实 DPI/多屏重新验收 |
| WP-1A-03 | R07、R10 | 只复用 IME/平台调用经验；不得用字符串门禁 |
| WP-1A-04 | 无旧实现准入 | R03、R24、R25、R64 被拒绝；named mutex 与 current legacy Qt 入口必须重写 |
| WP-1B-01 | 无旧实现准入 | R26 被拒绝；旧迁移没有 Job Object/受控后代树 |
| WP-1B-02 | R28 | R27 被拒绝；只保留状态字段需求 |
| WP-1B-03 | R29 | Fake Core 必须扩展后代、initialize、stderr 和故障模式 |
| WP-1B-04 | R30 | 旧测试矩阵不完整，必须按 ADR-0001 补齐 |
| WP-1C-01 | R32、R33、R38 | R36、R37 被拒绝；先通信再初始化 |
| WP-1C-02 | 无旧实现准入 | R41 被拒绝；readiness/Snapshot 从 ADR 重写 |
| WP-1C-03 | R55 | 日志、协议协商、credential 和故障 transport 重新实现 |
| WP-1C-04 | R31、R34、R47 | fixture 必须升级为 v2 lifecycle，使用 bundled Python |
| WP-1D-01 | R04 | R05 被拒绝；路由不能拥有 Core 真相 |
| WP-1D-02 | R21、R22 | R08 被拒绝；只建 diagnostics/runtime repair 最小窗口 |
| WP-1D-03 | 无旧实现准入 | 旧迁移没有通过同一串行 Supervisor 的真实手动 retry E2E |
| WP-2-01 | 无旧实现准入 | R27、R36 被拒绝；没有并发 pending request router |
| WP-2-02 | R42 | 执行平面必须补 health/cancel/shutdown 的真实阻塞隔离 |
| WP-2-03 | R43 | R60 被拒绝；Operation 不得复用全局 daemon task manager |
| WP-2-04 | R23 | 旧 `host_call`/混合 AppState 被拒绝；按窗口和 command allowlist 重写 |
| WP-2-05 | R57 | R41 被拒绝；资源必须 opaque、generation-scoped，Snapshot 必须 revisioned |
| WP-2-06 | R35 | R48 被拒绝；背压/慢 writer/控制配额旧迁移没有实现 |
| WP-3-01 | R16、R44、R53、R56 | R39、R54 被拒绝；薄 Adapter，不改 Assistant 业务语义 |
| WP-3-02 | R40、R50 | 只接基础聊天/历史；Tools/TTS/截图均不进入 |
| WP-3-03 | R11、R12、R13、R14、R17、R18、R49、R51 | 只用 Fake Core 驱动表现层 |
| WP-3-04 | 无旧生产文件准入 | R19 被拒绝；使用已冻结 Gateway/Operation 小步接通 |
| WP-3-05 | R15 | 只复用 epoch/coalesce 思路；完整 Snapshot 重取与水合重写 |
| WP-3-06 | R63 | R64 被拒绝；必须执行真实双入口锁和 Qt→Tauri→Qt 数据门禁 |

## 7. 被拒绝实现中保留的经验

以下内容只能进入文档、fixture 或测试输入，不能复制被拒绝的生产结构。

| 来源 | 保留经验 | 绑定位置 |
|---|---|---|
| R26/R27 | 根进程退出不代表后代退出；同步普通请求会饿死 shutdown；stalled hello 必须可被 app shutdown 打断 | WP-1B-01 至 WP-1B-04 Fake Core/进程树故障矩阵 |
| R05/R19 | Core 不可用时需要清理音频/截图/等待者；旧 generation 的 UI load/event 必须失效 | WP-1D-01、WP-3-05 状态竞态测试 |
| R36/R37 | stdout 污染、分片/半帧、stdin EOF、hello 前重型初始化是独立故障 | WP-1C-01、WP-1C-03 transport 测试 |
| R39（拒绝） | watcher 晚到、Scheduler stop、Assistant close、history write 失败、插件关闭顺序 | WP-2-02、WP-3-01/02 故障测试；不复制巨型 application |
| R41（拒绝） | Snapshot 裸路径、跨域状态、缺 revision/generation 会污染 UI | WP-2-05 Snapshot schema 负例 |
| R48/R64 | “源码里存在字符串”不能证明真实链路；单实例插件不等于共享数据锁 | 所有真实验收门禁，尤其 WP-1A-04、WP-3-06 |
| R60/R61 | 设置保存后运行态半更新、回滚失败、旧异步 preview 覆盖新值、全局 daemon task 跨会话 | Phase 5 设置 Work Package 设计输入 |
| R57/R58/R59 | 路径逃逸、过期资源、重复读取、late audio/capture、设备/文件清理 | WP-2-05 与 Phase 4 资源故障矩阵 |
| R63/R67 | 兼容历史只允许 Qt 可读追加；普通 `.bak`/原子写 helper 不能代替 migration-grade backup | WP-3-06、ADR-0003 数据兼容负例 |

## 8. 151 个变更路径的覆盖证明

固定迁移相对共同基线共 151 个变更路径，按顶层范围完整覆盖：

| 路径组 | 数量 | 处置 |
|---|---:|---|
| `app/` | 45 | R16、R32、R35–R47、R53–R67；领域外改动拒绝、延后或无归属删除 |
| `desktop/` | 53 | R01–R23、R26–R35、R49、R51、R52、R57–R62 |
| `tests/` | 28 | R29、R30、R34、R42、R47–R50、R57–R65；现有 Qt 测试窄改不视为旧生产复用 |
| `docs/` | 7 | 仅作为旧迁移自述和故障线索；不准入 Runtime v2 技术真相源 |
| `.github/` | 4 | CI/release 改动不属于 Phase 1A–3 文件复用；最终发布链重新建立 |
| 仓库根文件 | 12 | R24、R25 覆盖入口；requirements/install/update 元数据不作为 Phase 1A–3 代码候选 |
| `plugins/` | 2 | R66 无归属删除，不进入 Phase 3 |
| 合计 | 151 | 无遗漏 |

清单引用路径的机器校验集合如下；目录项表示该目录模块下的记录，校验时同样使用 `git cat-file -e <commit>:<tree-path>`：

```text
SOURCE_PATHS_BEGIN
desktop/src-tauri/Cargo.toml
desktop/src-tauri/build.rs
desktop/src-tauri/src/main.rs
desktop/src-tauri/tauri.conf.json
desktop/src-tauri/src/lib.rs
desktop/src-tauri/src/app_state.rs
desktop/src-tauri/src/windows.rs
desktop/frontend/pet/layout.js
desktop/frontend/pet/pet_controller.js
desktop/frontend/pet/bubble_controller.js
desktop/frontend/pet/subtitle_controller.js
desktop/frontend/pet/portrait_controller.js
desktop/frontend/core/store.js
desktop/frontend/core/bootstrap_loader.js
app/config/theme.py
desktop/frontend/core/theme.js
desktop/frontend/index.html
desktop/frontend/styles.css
desktop/frontend/app.js
desktop/frontend/pet/context_menu.js
desktop/src-tauri/src/menu_actions.rs
desktop/src-tauri/src/tray.rs
desktop/frontend/runtime-repair
desktop/frontend/diagnostics
desktop/src-tauri/capabilities/default.json
main.py
start.bat
legacy_qt_main.py
desktop/src-tauri/src/brain_host.rs
tests/fixtures/fake_brain_host.py
app/brain_host/protocol.py
desktop/src-tauri/src/ipc.rs
tests/fixtures/brain_host_frame_v1.json
app/brain_host/transport.py
app/brain_host/server.py
app/brain_host/__main__.py
app/brain_host/application.py
app/brain_host/errors.py
app/brain_host/dto.py
tests/unit/test_assistant_service.py
app/core/assistant_service.py
app/brain_host/pending_actions.py
app/core/chat_pipeline.py
app/brain_host/scheduler.py
app/backchannel/decision.py
app/backchannel/headless_service.py
tests/unit/test_brain_host_protocol.py
tests/unit/test_brain_host_server.py
tests/unit/test_tauri_desktop_layout.py
tests/unit/test_tauri_pet_frontend.py
tests/integration/test_tauri_brain_chat_contract.py
desktop/frontend/chat/chat_controller.js
desktop/frontend/chat/confirmation_view.js
app/agent/__init__.py
app/agent/mcp/__init__.py
app/core/bootstrap.py
app/core/app_context.py
app/core/extensions.py
app/core/resource_manager.py
app/core/runtime_log.py
app/config/character_loader.py
app/config/settings_service.py
app/ui/theme.py
desktop/src-tauri/src/audio.rs
desktop/src-tauri/src/capture.rs
app/agent/screen_observation.py
tests/integration/test_tauri_observation_contract.py
app/voice/tts_provider.py
app/voice/tts_synthesis_service.py
app/voice/tts_synthesis.py
app/voice/tts_types.py
app/voice/tts.py
desktop/frontend/audio
tests/unit/test_tts_synthesis_service.py
tests/integration/test_tauri_brain_tts_contract.py
desktop/frontend/capture
desktop/frontend/capture.html
app/core/settings_resource_tasks.py
app/ui/settings/resource_tasks.py
app/brain_host/secondary_windows.py
desktop/frontend/settings
desktop/frontend/studio
tests/unit/test_tauri_settings.py
tests/unit/test_tauri_studio.py
tests/integration/test_tauri_secondary_windows.py
desktop/frontend/history
tests/integration/test_tauri_production_entry.py
tests/integration/test_tauri_runtime_events.py
app/plugins/manager.py
app/plugins/discovery.py
app/plugins/services.py
plugins/sakura_mobile
app/agent/memory.py
app/agent/memory_curation_task.py
app/agent/memory_curation_worker.py
app/storage/atomic.py
SOURCE_PATHS_END
```

## 9. 一致性验证与重复审查方式

### 9.1 固定来源与路径存在性

重复审查时执行：

```powershell
$commit = '190dfafd24f5c5226bff8b4347837b6e45d9a331'
git cat-file -e "$commit^{commit}"
git rev-parse feat/tauri-assistant-migration
git rev-parse origin/feat/tauri-assistant-migration

$doc = Get-Content 'docs/runtime-v2/baselines/WP-0-03-legacy-reuse-admission.md'
$start = [Array]::IndexOf($doc, 'SOURCE_PATHS_BEGIN') + 1
$end = [Array]::IndexOf($doc, 'SOURCE_PATHS_END')
$paths = $doc[$start..($end - 1)] | Where-Object { $_.Trim() }
$missing = foreach ($path in $paths) {
    git cat-file -e "$commit`:$path" 2>$null
    if ($LASTEXITCODE -ne 0) { $path }
}
if ($missing) { throw "fixed commit missing paths: $($missing -join ', ')" }
```

### 9.2 结论和 Work Package 归属

人工与脚本复核规则：

1. 记录 ID 必须连续为 R01–R67。
2. 分类计数必须为 6 / 34 / 17 / 7 / 3。
3. 所有“准入”或“有条件准入”记录必须在第 6 节出现且只绑定一个具体 WP。
4. “延后”只绑定 Phase 4 或 Phase 5，不得进入 Phase 1A–3 实现。
5. “无归属”必须明确删除候选，不能出现在未来 WP 准入列。
6. WP-1A-01 至 WP-3-06 每个条目必须存在；没有旧实现时明确写“无旧实现准入”。
7. 第 7 节必须覆盖被拒绝结构中的故障、竞态、进程泄漏、IPC 错误和数据兼容经验。

### 9.3 范围和数据安全

本 WP 的最终变更范围只能包含：

- `docs/runtime-v2/baselines/WP-0-03-legacy-reuse-admission.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 中 WP-0-03 状态和验收记录

禁止出现 `main.py`、`app/`、`desktop/`、`plugins/`、`data/`、`runtime/`、`characters/`、`third_party/` 或 `tools/mcp/` 的工作树修改。验证不读取真实 API Key、Token、聊天、Memory、notes 或插件私有内容；Git 取证只读取提交对象中的源码、测试和脱敏 fixture。

## 10. 已知限制与后续约束

1. 本清单证明的是“旧实现是否允许进入未来审查”，不证明任何候选已满足对应 Work Package 的技术门。
2. 准入不等于直接复制。未来 WP 仍必须从固定 commit 单文件读取、比较当前代码、执行对应测试和真实验收。
3. 旧迁移没有可准入的 Windows Job Object、并发 Router、readiness/Snapshot、手动 retry E2E 或真实 shared named mutex 实现；这些部分必须重写。
4. TTS、截图、主动事件和历史 UI 只绑定后续 Phase，不得借本清单提前进入 Phase 1A–3。
5. 设置/Studio/secondary bridge 即使包含有效页面或事务代码，也只保留故障经验，不允许恢复其生产结构。
6. 固定分支引用将来若移动，不改变本清单来源；唯一权威来源仍是完整 commit hash。
7. WP-0-04 只能进行架构审查收口，不得把本清单误用为 WP-1A-01 已激活或生产代码已批准。

## 11. 独立回退

本 Work Package 只包含审查文档和状态记录，可独立回退：

```powershell
git revert <WP-0-03-commit>
```

回退不得 checkout、restore、merge、cherry-pick 或修改 `feat/tauri-assistant-migration`；不得删除或改写真实 `data/`、角色资源、Runtime、插件数据、Memory/Qdrant 或任何用户文件。
