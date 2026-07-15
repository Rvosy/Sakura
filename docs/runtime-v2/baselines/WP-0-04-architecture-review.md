# WP-0-04 Runtime v2 架构审查收口

> Phase / Work Package：Phase 0 / WP-0-04
>
> 审查日期：2026-07-16
>
> 工作分支：`refactor/tauri-runtime-v2`
>
> 审查结论：架构缺口已收口，可以批准 WP-1A-01 作为下一项可激活 Work Package；实际状态只在 Work Package 清单维护
>
> ADR 结论：ADR-0001、ADR-0002、ADR-0003 仅认可为 `Proposed` 技术基线，尚未 `Technically Validated` 或 `Accepted`

## 1. 审查范围与真相源

本 Work Package 对以下材料进行了完整交叉审查：

- `docs/superpowers/plans/2026-07-14-tauri-python-core-v2.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`
- `docs/adr/0001-runtime-v2-process-supervision.md`
- `docs/adr/0002-runtime-v2-ipc.md`
- `docs/adr/0003-runtime-v2-data-compatibility.md`
- `docs/runtime-v2/baselines/WP-0-01-legacy-qt-baseline.md`
- `docs/runtime-v2/baselines/WP-0-02-data-lock-baseline.md`
- `docs/runtime-v2/baselines/WP-0-03-legacy-reuse-admission.md`

真相源规则：

- Phase 0–3 的 Work Package 顺序、范围和状态只由 `2026-07-15-runtime-v2-work-packages.md` 维护。
- 主计划描述产品目标、硬边界、阶段结果和最终审查结论，不重复维护 Work Package 状态。
- 治理文件约束允许列表、证据、稳定化、Bug Budget、提交和停止条件。
- ADR 描述当前推荐技术方案及状态门禁。三份 ADR 当前状态均为 `Proposed`。
- Phase 0 基线保存历史取证和未来门禁输入，不替代真实 Tauri、进程、IPC 或双入口验证。

固定旧迁移取证源已复核：

```text
branch: feat/tauri-assistant-migration
commit: 190dfafd24f5c5226bff8b4347837b6e45d9a331
local branch ref: 190dfafd24f5c5226bff8b4347837b6e45d9a331
origin branch ref: 190dfafd24f5c5226bff8b4347837b6e45d9a331
```

## 2. 前置 Work Package 与提交复核

| Work Package | 清单状态 | 基线证据 | 关联提交 | 结论 |
|---|---|---|---|---|
| WP-0-01 | `accepted` | legacy Qt、工具链、真实启动/退出和数据零变化基线 | `c555e1b95` | 前置满足 |
| WP-0-02 | `accepted` | 数据权限、shared named mutex、兼容 fixture 和故障矩阵 | `5e6cf364e` | 前置满足 |
| WP-0-03 | `accepted` | 固定旧迁移 67 项准入、151 路径覆盖、无悬空归属 | `239f495ad4c0b324c6b6e340bc155ab23997f7e9` | 前置满足 |

审查发现 Work Package 清单曾把 WP-0-03 的 accepted 证据误放在 WP-0-02 下，形成 WP-0-02 重复验收、WP-0-03 缺少 accepted 记录的状态漂移。本 Work Package 已把该记录移回 WP-0-03，并为三个前置 WP 填入实际提交。

## 3. 架构审查决策表

| 决策项 | 当前计划或 ADR 的陈述 | 支撑证据 | 尚未验证的技术假设 | Phase 1A–3 的约束 | 验证失败时的处理 | 最终审查结论 | 后续负责的具体 Work Package |
|---|---|---|---|---|---|---|---|
| 单透明窗口的约束级别 | 主计划原先把“一个透明桌宠窗口”放在不可妥协边界；Work Package 又把它作为 Phase 1A 技术门 | WP-0-01 只有 legacy Qt 单屏/100% DPI 基线；WP-0-03 的 R02/R06/R07/R09/R10 只提供候选参数、纯几何和平台经验 | Tauri/WebView2 能否同时满足透明命中、拖动、IME、焦点、多 DPI 和无白闪 | 硬约束是单一 Tauri 生命周期根、统一桌宠组合体验和固定立绘锚点；单个原生透明窗口是首选技术方案 | 停在 WP-1A-03，不进入 WP-1A-04；删除失败实现，记录真实证据，重新批准替代窗口架构 | 单窗口不是不可变产品硬约束，是 Phase 1A 首选技术方案 | WP-1A-02、WP-1A-03；失败时新建窗口架构审查 WP |
| 单窗口失败停止与替代路径 | WP-1A-03 原有文字要求按 WP-0-04 路径停止或更新架构 | 治理 G-009/G-012 禁止用兼容层掩盖 P1；WP-0-03 拒绝 secondary bridge 和巨型 AppState | 受控多原生窗口组合、Windows hit-test 平台层或收窄交互模型是否可行 | 不得引入隐藏 Qt、第二生命周期根、管理员权限依赖或未批准兼容层 | WP-1A-03 保持 stabilizing/未 accepted；形成故障报告、候选方案和范围差异；项目负责人批准主计划、决策记录和新 WP 后才能继续 | 停止条件、替代路径和批准流程明确 | WP-1A-03；替代方案由新建 WP 负责 |
| Phase 1A–3 dogfooding 成本 | 主计划要求 v2 分支在 Phase 1A 后默认 Tauri，Qt 保留回退；WP-1A-04 才切入口 | WP-0-01 证明当前 Qt 可启动/退出；WP-0-02 冻结共用锁；WP-0-03 拒绝旧 Python 启动 Tauri 链 | 开发者能否接受 Phase 3 前需要显式启动 legacy Qt 使用完整产品 | 默认入口只在 WP-1A-04 accepted 后切换；只影响 v2 分支，不影响 `dev`、正式安装包和发布入口；两入口不得并行 | 若显式回退不可用、锁不安全或产生数据差异，WP-1A-04 不得 accepted，恢复当前 Qt 默认入口 | 成本可接受，前提是回退命令、shared mutex 和分支边界全部成立 | WP-1A-04；真实双向数据门禁由 WP-3-06 |
| legacy Qt 回退与数据责任 | ADR-0003 要求同一 mutex、无破坏性迁移和真实 Qt→Tauri→Qt | WP-0-02 的 lock identity、schema epoch、数据分类、故障矩阵和脱敏 fixture | 当前 Qt 锁能否前移到所有 data/log 写入前；Tauri 能否最后释放锁 | 回退命令规划为 `.\runtime\python.exe .\legacy_qt_main.py`；必须先退出 Tauri；用户不删锁、不并发运行、不手工合并数据 | 锁失败或 schema 不安全时进入 already_running/fatal/diagnostics-read-only，不继续启动写入者 | 命令、适用范围、责任和代码回退均明确 | WP-1A-04、WP-3-06 |
| lifecycle deadline | ADR-0001/0002 原先只要求确定 deadline，未给初值 | WP-0-01 启动 p95 约 1.538 秒；主计划要求退出后 5 秒内无后代；架构要求 hello 早于重型初始化 | bundled Python、Job Object、stderr 排水和初始化在真实环境中的尾延迟 | hello 3 秒；initialize 接受 5 秒；readiness watchdog 30 秒；shutdown 协议优雅期 3 秒；完整树退出 5 秒 | 超时按结构化失败进入重试预算或强杀；超过 5 秒树退出为 P1；调整必须同步 ADR/fixture | 初值足以进入技术验证，不视为已验证性能承诺 | WP-1B-03、WP-1B-04、WP-1C-01 至 WP-1C-04 |
| 不可自动重试分类 | ADR-0001 已列 major/capability/setup_required 示例；ADR-0002 定义版本协商 | readiness 模型、ADR-0003 安全状态、WP-0-03 的故障经验 | 具体错误码和 diagnostics UX 尚未实现 | major 不兼容、缺必要 capability、setup_required、确定性配置/数据错误、Runtime/打包错误、credential 错误、应用锁结果均不自动重启；Provider 网络错误不重启 Core | 进入 diagnostics/setup_required；只有外部状态变化后用户手动 retry，仍走同一 Supervisor | 分类可进入 Fake Core/真实 Host 技术验证 | WP-1B-04、WP-1C-03、WP-1D-01 至 WP-1D-03 |
| Runtime v2 工具链 | WP-0-01 记录 legacy 环境；现有 Tauri 工具使用 Tauri 2，但未冻结 v2 版本 | 当前锁文件为 Tauri 2.11.3、tauri-build 2.6.3；当前 Rust/Cargo 1.96.0；Tauri CLI 缺失 | 新最小 Shell 在固定版本上能否 dev/release 构建并真实显示 | WP-1A-01 在 `desktop/` 内固定 Rust 1.96.0、Tauri/Cargo.lock；静态页面不要求 Node；CLI 非前置 | 任一版本需要变化时先更新准备记录和证据，不以临时全局安装掩盖 | 版本和非前置工具已明确 | WP-1A-01；后续升级由对应激活 WP |
| bundled Python 来源 | 主计划要求 bundled Python end-to-end；旧文档只写 `runtime/python.exe` | release workflow 固定 CPython 3.12.8 官方 Windows embeddable amd64 URL；本机 runtime 为 3.12.8 x64 | 打包后的路径、依赖、校验和 Core Host import 边界尚未验证 | Phase 1A 不启动 Python；Phase 1C 不得回退系统 Python；release 与开发路径使用同一来源 | 缺失/不兼容进入不可自动重试 diagnostics；工件完整性校验未完成前不得冻结发布链 | 来源明确；供应链完整性验证保留给 WP-1C-04 | WP-1C-04 |
| Windows-only 平台边界 | 主计划和 ADR-0001 已规定 Phase 1A–3 Windows 正式目标 | 当前参考环境 Windows 11 23H2 x64、WebView2 150；WP-0-01 保留 legacy Windows 环境 | Windows 10、ARM64、Server、其他 GPU/DPI 组合尚未验证 | 正式范围仅 Windows x64；非 Windows/ARM64/32 位/Wine/Server 不作为 Phase 1A–3 门禁 | 其他平台需求进入独立 ADR、窗口/进程树验证和新 WP | 边界明确，无跨平台投机实现 | WP-1A-01 至 WP-3-06；扩平台另立 ADR/WP |
| Supervisor 与 IPC 所有权 | 主计划要求 Tauri 唯一根；ADR-0001 串行 Supervisor；ADR-0002 control/domain plane | WP-0-03 拒绝同步 Supervisor、根进程 kill、巨型 Host 和混合 AppState | Job Object、并发 Router、bounded executor、backpressure 参数未验证 | 生命周期意图串行；transport/control 不运行领域代码；generation 隔离；Rust 只读 Snapshot | Fake Core/真实阻塞门失败则停在当前 WP，不用全局状态或额外兼容层绕过 | 文档一致，参数仍保留技术验证空间 | WP-1B-01 至 WP-2-06 |
| Assistant Adapter 与 Phase 3 范围 | 主计划要求 Adapter/Facade 复用现有服务；Phase 3 只做基础聊天 | WP-0-01 记录当前能力；WP-0-03 拒绝 BrainHostApplication，条件准入薄 Facade/DTO/测试 | 无 Qt import、readiness、真实 Chat Pipeline 等价性未验证 | 不重写 Assistant；基础聊天只含角色、Provider、聊天、历史、取消和表现层；TTS、截图、设置、Studio 延后 | 需要改业务语义时停止并申请独立范围；可选组件失败只 degraded | 范围一致，无 Phase 4/5/6 偷跑 | WP-3-01 至 WP-3-06 |
| ADR 和 Work Package 状态 | 主计划定义 Proposed→Technically Validated→Accepted；清单是 WP 状态真相源 | 三份 ADR 标题均为 Proposed；WP-0-01/02/03 基线和提交存在 | 所有技术方案仍待实现门禁 | WP-0-04 只能认可 Proposed；WP-1A-01 继续 planned | 若验证失败，更新或 Supersede ADR；不为符合文档强行实现 | 状态漂移已修复，ADR 没有越级 | ADR-0001：WP-1B；ADR-0002：WP-1C/2；ADR-0003：WP-1A-04/3-06 |

## 4. 单透明窗口失败路径

### 4.1 失败判定

WP-1A-03 出现以下任一结果，首选单窗口方案即失败：

1. 真实 WebView 中透明区域点击穿透和输入/按钮命中不能稳定共存。
2. 中文 IME 候选框、composition、焦点恢复、Alt+Tab 或显示隐藏存在可重复 P1。
3. 拖动、点击穿透和交互模式切换会留下无法输入、无法点击或无法退出的状态。
4. 目标 DPI/多屏条件下只能通过移动立绘锚点、无限扩大原生窗口或明显白闪维持功能。
5. 方案需要隐藏 Qt、常驻 Python 窗口根、第二 Tauri App、管理员权限、全局 hook 或未批准兼容层。
6. 自动测试通过但真实物理输入持续失败。

### 4.2 停止与批准流程

```text
WP-1A-03 发现失败
-> 状态保持 active/stabilizing，不标记 accepted
-> 停止 WP-1A-04 和默认入口切换
-> 保存最小复现、环境、截图/日志和失败矩阵
-> 删除或隔离不能独立回退的失败实现
-> 比较候选替代：同一 Tauri App 的受控多窗口组合 / Windows hit-test 层 / 收窄交互模型
-> 更新主计划、窗口架构决策记录和 Work Package 拆分
-> 项目负责人明确批准
-> 才允许激活新的替代实现 WP
```

任何替代都必须保留：一个 Tauri 生命周期根、固定立绘桌面锚点、统一用户体验、可靠退出、无 Qt UI、shared mutex 和既有数据权限。不得把“多原生窗口”自动等同于“多个生命周期根”，但也不得在没有架构批准时把多窗口作为临时修补。

## 5. Dogfooding、legacy Qt 回退与数据安全

### 5.1 成本结论

WP-1A-04 到 Phase 3 之间，v2 分支默认 Tauri 还不具备完整聊天、设置、Studio、TTS、插件等现有功能。开发者需要显式退出 Tauri 并启动 legacy Qt 才能使用完整产品，存在命令切换和不能并行调试两个入口的成本。

该成本可接受，限制条件是：

- 只发生在 `refactor/tauri-runtime-v2`；不提前改变 `dev`、正式安装包和发布入口。
- WP-1A-01 至 WP-1A-03 不切默认入口；只有 WP-1A-04 的 shared mutex、回退命令和真实 Shell 门通过后才切换。
- legacy Qt 保持当前完整能力，不因 Runtime v2 修改 Assistant 业务语义或共享 schema。
- 两入口共用 `Local\\SakuraDesktop.SharedUserData.v1`，不能并行运行或成为两个 writer。

### 5.2 回退命令和适用范围

WP-1A-04 计划建立：

```powershell
.\runtime\python.exe .\legacy_qt_main.py
```

可增加 `start-legacy-qt.bat` 作为便利入口。命令适用于：

- Phase 3 前需要完整现有产品能力。
- Tauri Shell、窗口、Core 启动、IPC 或基础聊天仍在开发/故障状态。
- v2 发生可独立回退的实现问题，且共享 schema 仍在 ADR-0003 安全范围内。

命令不适用于：

- Tauri 仍在运行时并发启动 Qt。
- 绕过未来/损坏 schema 的 diagnostics/read-only。
- 要求用户删除 `data/sakura.lock`、Qdrant `.lock` 或命名 mutex。
- 在 Phase 1–3 之外执行未批准的共享数据迁移。

### 5.3 责任边界

- 持锁桌面根负责在任何 data/log/config/migration/Core 动作前获取 mutex，并在全部写入者和后代退出后最后释放。
- 未持锁入口负责无副作用退出并显示 already_running/fatal diagnostics。
- Python Core 不竞争桌面锁；写权限来自当前持锁 Tauri 根。
- 用户只需先退出当前入口再启动另一个入口，不承担 stale 判断、删锁、恢复备份或合并数据责任。
- WP-1A-04 负责实现和验证双入口锁；WP-3-06 负责真实 Qt→Tauri→Qt 数据兼容；ADR-0003 在对应门禁前保持 Proposed。

## 6. Lifecycle deadline 与失败策略

| 动作 | 初始值 | 计时边界 | 成功条件 | 超时处理 | 验证 WP |
|---|---:|---|---|---|---|
| `system.hello` | 3,000 ms | Rust 写入当前 generation 的 hello 后 | 收到合法版本/capability/credential 响应 | generation 启动失败；清理后在 budget 内重试 | WP-1B-03、WP-1C-01/03 |
| `core.initialize` 接受响应 | 5,000 ms | Rust 写入 initialize 后 | Core 快速返回 accepted 或确定性拒绝 | generation 初始化协议失败；不得在 control plane 同步等待重型初始化 | WP-1C-02/03 |
| readiness watchdog | 30,000 ms | initialize accepted 后 | setup_required/ready/degraded/failed 中任一稳定状态 | 进入 diagnostics/restarting；health/shutdown 必须仍可用 | WP-1C-02/04 |
| `system.shutdown` 协议优雅期 | 3,000 ms | Rust 写入 shutdown 后 | Core 完成响应并开始/完成退出 | 到期立即 terminate_tree | WP-1B-03/04、WP-1C-01/04 |
| 完整停止 | 5,000 ms | 从 shutdown 意图开始 | 进程树退出、管道 EOF、句柄释放和 verify_tree_exited 完成 | 记录 P1，禁止新 generation，保留诊断证据 | WP-1B-01/04、WP-1C-04 |

以上值只批准进入技术验证。若真实分布显示需要调整，必须说明环境、p50/p95/max、失败类型和用户影响，并同步修改 ADR、Fake Core/真实 Host fixture 和 diagnostics 文案。不得因为调试器、依赖下载或人工断点放宽正式门禁。

## 7. 不可自动重试分类

| 类别 | 例子 | 自动重试 | 用户手动重试 | UI/状态 | 负责 WP |
|---|---|---|---|---|---|
| 协议不兼容 | `protocol_major_incompatible` | 禁止 | 更新/修复 Runtime 后允许 | diagnostics | WP-1C-03、WP-1D-02 |
| 必要能力缺失 | `missing_required_capability` | 禁止 | 安装兼容 Core 后允许 | diagnostics | WP-1C-03、WP-1D-02 |
| 等待设置 | `setup_required` | 禁止；不是错误 | 完成配置后允许 | setup_required | WP-1C-02、WP-3-01 |
| 确定性配置/数据错误 | 必需字段缺失、类型无效、未知 Provider、损坏必要数据、未来 schema | 禁止 | 修复或回到兼容数据后允许 | diagnostics/read-only 或 failed | WP-1C-02/03、WP-1D-02、WP-3-01 |
| Runtime/打包错误 | bundled Python 缺失、架构/版本不兼容、入口缺失、import guard 发现 Qt | 禁止 | 修复安装后允许 | runtime_repair/diagnostics | WP-1C-01/04、WP-1D-02 |
| 安全边界错误 | generation credential 不匹配、握手认证失败 | 禁止 | 仅在实现/安装修复后允许 | fatal diagnostics | WP-1C-03 |
| 桌面锁结果 | `already_running`、mutex API access/fatal | 不创建 Core 重试 | 退出另一入口或修复权限后重新启动 | already_running/fatal | WP-1A-04 |
| 领域请求错误 | Provider 网络不可达、模型认证失败、单次聊天格式错误 | 不重启 Core | 用户重试请求 | request error；Core 仍 ready/degraded | WP-3-02/04 |
| 暂时性生命周期错误 | OS spawn/pipe 暂时失败、hello timeout、unexpected exit、broken pipe | 在有限 budget/backoff 内允许 | 允许，仍走同一 Supervisor | restarting/diagnostics | WP-1B-04、WP-1D-03 |

不可自动重试错误必须稳定显示原因和安全动作，不能通过无限 restart budget 把空白窗口或错误循环伪装成恢复。

## 8. Runtime 工具链、参考环境和 Python 来源

### 8.1 Phase 1A 参考环境

| 项目 | 2026-07-16 审查值 | 结论 |
|---|---|---|
| OS | Windows 11 23H2，build `22631.4890`，x64 | Phase 1A–3 参考环境 |
| Rust / Cargo | `1.96.0` / `1.96.0`，`x86_64-pc-windows-msvc` | WP-1A-01 固定精确版本，不使用漂移的 stable 真相 |
| Tauri / tauri-build | `2.11.3` / `2.6.3` | 来自现有 Settings/Studio `Cargo.lock` 的仓库内证据；新 Shell 独立锁定 |
| Tauri CLI | 未安装 | WP-1A-01 非前置，使用 Cargo；以后 bundle 前精确固定 |
| Visual Studio | Professional 2026 `18.4.1`，C++ x64 组件存在 | 参考 native build 工具 |
| Windows SDK | `10.0.26100.0` | 参考 SDK |
| WebView2 | `150.0.4078.65` | 真实 Shell 验收记录版本 |
| Node / npm | `v22.14.0` / `11.18.0` | 静态 startup 页面非前置；引入前端构建链后再固定 |
| bundled Python | CPython `3.12.8` x64，`runtime/python.exe` | Phase 1A 不使用；Phase 1C 使用同一 release 来源 |

WP-0-01 是 legacy Qt 的历史验收快照，记录了当时不同的 Windows/Rust/Node/WebView2 值。它继续用于比较 legacy 行为，不应被改写为当前 Tauri 参考环境。两次审查中 `stable` 指向的 Rust 版本不同，正是 WP-1A-01 必须固定 toolchain 的证据，不是 P0/P1 或数据安全冲突。

### 8.2 bundled Python 来源

Windows Runtime 来源冻结为：

```text
CPython: 3.12.8, Windows embeddable amd64
upstream: https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip
repository workflow: .github/workflows/release.yml
published form: runtime-windows-x64.zip or full Sakura Windows package
runtime entry: runtime/python.exe
```

Phase 1C/发布链要求：

- 不回退到系统 Python、Conda 或 PATH 中任意解释器。
- 开发和 release 使用同一来源及依赖集合完成 hello/initialize/Snapshot/shutdown。
- WP-1C-04 在冻结 release 路径前记录下载工件完整性校验、依赖清单和诊断版本。
- bundled Python 缺失或不兼容是不可自动重试错误，Shell 仍必须可见并可退出。

## 9. 跨文档一致性矩阵

| 检查项 | 主计划 | 治理 | ADR | Phase 0 基线 / Work Package | 审查结论与后续责任 |
|---|---|---|---|---|---|
| 生命周期唯一根 | Tauri 是唯一桌面根；legacy Qt 是独立回退路径 | G-002/G-005/G-011 禁止反向启动链和投机平台 | ADR-0001：Rust 最终停止权 | WP-0-03 R24 拒绝 Python 启动 Tauri；WP-1A-01 不启动 Python | 一致；WP-1A-01、WP-1B-01/02 验证 |
| Python Core 无 Qt | Python 不创建 Qt UI；Adapter/Facade 复用领域服务 | G-006 领域冻结和等价性测试 | ADR-0002 hello 前不导入重型模块 | WP-0-03 R37/R54 拒绝 hello 前初始化和 Qt stub | 一致；WP-1C-01、WP-3-01 验证 |
| 共享应用锁 | Qt/Tauri 同一锁、不能双 writer | G-002/G-010 数据门 | ADR-0003 固定 `Local\\SakuraDesktop.SharedUserData.v1` | WP-0-02 冻结 identity/失败矩阵；WP-1A-04 归属 | 一致；真实实现未验证，ADR 保持 Proposed |
| 用户数据权限和回退 | Phase 1–3 无破坏迁移；Qt→Tauri→Qt | G-008/G-010 要求备份、异常和回退 | ADR-0003 分类、schema epoch、read-only | WP-0-02 fixture/oracle；WP-3-06 真实门 | 一致；WP-1A-04/3-06 负责 |
| Supervisor 与进程树所有权 | Rust 监管 Core 和后代 | G-002/G-009 强制故障和零 P1 | ADR-0001 Job Object/串行状态机 | WP-0-03 R26/R27 拒绝旧根进程 kill/同步 Supervisor | 一致；WP-1B-01 至 WP-1B-04 |
| IPC control/domain plane | 生命周期请求不受长任务阻塞 | G-002/G-011 冻结点与真实阻塞门 | ADR-0002 reader/control/writer 与领域执行面分离 | WP-0-03 R36/R42 保留阻塞测试、拒绝同步 server | 一致；WP-1C/2 验证 |
| generation、Snapshot 和资源描述符 | Rust 只读缓存；旧 generation 失效；无裸路径 | G-005 允许安全边界必要抽象 | ADR-0002 UUID generation、revision、opaque token | WP-0-03 R15/R41/R57 提供正负例 | 一致；WP-1C-02、WP-2-05 |
| Assistant Adapter/Facade | 适配现有服务，不重写领域 | G-006 修改必须证明等价 | ADR-0002 只规定 Host/IPC，不拥有业务 | WP-0-03 R39 拒绝巨型 Host，R44 条件准入薄 Facade | 一致；WP-3-01 |
| Phase 3 基础聊天范围 | 角色、Provider、聊天、历史、取消、表现和恢复 | G-002 Phase 3 明确允许/禁止 | ADR-0002 Chat event 与完整回复；ADR-0003 仅 history 兼容写 | WP-0-03 R40/R50/R51/R63 只复用 DTO/场景 | 一致；WP-3-01 至 WP-3-06 |
| TTS、截图、设置和 Studio 延期边界 | TTS/截图到 Phase 4，设置/历史到 Phase 5，Studio 到 Phase 6 | G-002/G-005 禁止跨 Phase | ADR-0002 资源仅用测试 token；ADR-0003 禁写相关数据 | WP-0-03 R45/46/52/58/59/61/62/65 延后或拒绝 | 一致，无提前实现 |
| Windows 正式平台范围 | Phase 1A–3 仅 Windows x64 | G-005 禁止提前完整跨平台；G-002 固定环境 | ADR-0001 非 Windows 仅 trait/安全失败 | WP-0-01 是 legacy 历史环境；WP-1A 准备记录固定当前参考环境 | 一致；其他平台另立 ADR/WP |
| Work Package 状态真相源 | 主计划不维护 WP 状态 | 治理明确清单为唯一状态源 | ADR 只维护自身状态 | 清单状态漂移已修复；WP-1A-01 planned | 一致；后续只改 Work Package 清单 |

所有矩阵项目均有明确结论和后续 Work Package。没有发现需要修改生产代码才能绕过的架构根冲突。

## 10. 冲突、悬空引用与状态漂移关闭记录

| ID | 问题 | 风险 | 处理 | 结果 |
|---|---|---|---|---|
| C-01 | WP-0-03 accepted 证据误放到 WP-0-02 | 状态真相源漂移 | 移回 WP-0-03，填入三个实际提交 | 已关闭 |
| C-02 | 主计划、治理和 Work Package 顶部仍写 Draft/等待最终审查 | 批准范围与文字不一致 | 主计划和治理更新为 Phase 0 最终审查通过；清单在 WP-0-04 accepted 时更新 | 已关闭 |
| C-03 | 主计划 Phase 0 数据锁/复用清单仍未勾选 | 已完成结果显示为未完成 | 按 WP-0-02/03 accepted 证据勾选 | 已关闭 |
| C-04 | 单窗口同时被写成硬边界和可技术调整方案 | 失败时可能强行实现 | 区分产品硬约束与首选技术方案，定义停止/替代/批准流程 | 已关闭 |
| C-05 | 主计划 ADR-0001 状态门写成 Phase 1B 直接 Accepted | ADR 状态越级 | 统一为 Proposed→Technically Validated→Accepted | 已关闭 |
| C-06 | “Phase 1A 后切默认入口”没有精确到 WP | 可能在 WP-1A-01/02/03 提前切换 | 明确只有 WP-1A-04 accepted 后切换 | 已关闭 |
| C-07 | legacy Qt 回退命令、适用范围和数据责任未明确 | dogfooding/数据安全不可执行 | 冻结计划命令、mutex 前置、用户/生命周期根责任和代码 revert | 已关闭 |
| C-08 | lifecycle deadline 和不可重试分类只有原则 | Fake Core/Host 无初始测试参数 | 在 ADR-0001/0002 和本审查中冻结首轮输入 | 已关闭 |
| C-09 | v2 工具链、参考 Windows 和 Python 来源未统一 | 构建不可重复、可能回退系统 Python | 固定精确初值、非前置工具和官方 embeddable 来源 | 已关闭 |
| C-10 | 指定主计划、治理、ADR、基线之间可能有悬空路径 | 审查不可重复 | 所有当前引用路径均纳入存在性检查；未来文件明确标为计划产物 | 已关闭 |

没有确认 P0、P1、数据污染、凭据泄露、范围扩张或生命周期根冲突。没有阻塞 WP-0-04 accepted 的悬空决策。

## 11. WP-1A-01 未来激活准备复核

Work Package 清单已补充不构成激活的准备记录，覆盖：

- 允许目录：仅新建 `desktop/` 最小 Shell、静态 startup、专用测试、`desktop/rust-toolchain.toml` 和精确 `.gitignore` 规则。
- 明确禁止：`main.py`、`app/`、`plugins/`、`data/`、`runtime/`、`characters/`、第三方工具、现有 Settings/Studio、legacy Qt/default 入口和 WP-1A-02+。
- 验收环境：Windows 11 23H2 x64、固定 Rust/Tauri、WebView2、MSVC/SDK；Node 和 Tauri CLI 均非前置。
- 关联 ADR：三份 ADR 继续 Proposed；本 WP 只证明 Tauri 根和无数据写入，不验证进程/IPC/锁实现。
- 自动测试：fmt、cargo test、debug/release build、真实可执行可见/退出冒烟。
- 真实 Shell：debug/release startup 可见；关闭后无残留；隔离目录无 Python/runtime/data 仍可运行。
- 故障测试：Python/runtime/环境/data 缺失不阻止 Shell；静态资源缺失应构建失败，不产生运行时空白页。
- 独立回退：整体 revert WP-1A-01，只移除新增 `desktop/` Shell，不影响 Qt 入口。
- 计划提交：`feat(runtime): 建立不启动 Python 的最小 Tauri Shell`。

WP-1A-01 的最小结果上限只有：

1. 不启动 Python 的最小 Tauri Shell。
2. startup 页面可见。
3. Python 缺失时仍可显示并退出。
4. 不包含 Supervisor、IPC、聊天、设置、托盘、角色加载或默认入口切换。

以下均不属于 WP-1A-01：透明窗口完整技术门、共享应用锁、legacy/default 入口切换、Python Core、bundled Python、Supervisor、IPC、聊天和产品功能。

WP-1A-01 在本 Work Package 完成后仍必须保持 `planned`。只有项目负责人决定开始下一项工作时，才在 Work Package 清单把它更新为 `active`；激活动作不得与 WP-0-04 提交混在一起。

## 12. 最终审查结论

- WP-0-01、WP-0-02、WP-0-03 的 accepted 状态和提交已确认。
- 主计划与治理文字已反映 Phase 0 最终审查范围。
- ADR-0001、ADR-0002、ADR-0003 均保持 `Proposed`，没有误标为已技术验证或已接受。
- 单透明窗口的约束级别、失败停止条件、替代路径和批准流程明确。
- v2 分支 dogfooding 成本可接受，legacy Qt 回退命令、适用范围和数据责任明确。
- lifecycle deadline 初值和不可自动重试分类可以进入 Fake Core/真实 Host 技术验证。
- Runtime v2 工具链、Windows x64 参考环境和 bundled Python 来源明确。
- 跨文档冲突、状态漂移和当前悬空引用均已关闭。
- WP-1A-01 的允许范围、验收、故障测试、回退和计划提交准备完整，状态仍为 `planned`。
- 本 Work Package 没有创建、编译或运行 Tauri，没有生产代码或真实 `data/` 变更。
- 未确认 P0/P1、数据污染、凭据泄露或范围扩张。

因此，WP-0-04 可以进入 `accepted`；提交完成后，下一步可以单独激活 WP-1A-01，但本次任务不得激活或实现它。

## 13. 独立回退

本 Work Package 只包含架构审查文档、必要的计划/治理/ADR 收口和 Work Package 状态记录。独立回退：

```powershell
git revert <WP-0-04-commit>
```

回退不修改 `main.py`、`app/`、`desktop/`、`plugins/`、`data/`、`runtime/`、`characters/`、`third_party/`、`tools/mcp/` 或旧迁移分支，不恢复 stash，不删除或改写任何真实用户数据。
