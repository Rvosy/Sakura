# Sakura Runtime v2 Work Package 拆分与执行清单

> 状态：Draft / 等待最终审查
> 工作分支：`refactor/tauri-runtime-v2`
> 主计划：`docs/superpowers/plans/2026-07-14-tauri-python-core-v2.md`
> 治理约束：`docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
> 旧迁移取证源：`feat/tauri-assistant-migration` / `190dfafd24f5c5226bff8b4347837b6e45d9a331`

本文把 Runtime v2 Phase 0–3 拆成可独立验证、稳定化和回退的 Work Package。主计划继续描述产品目标、架构边界和阶段结果；本文是 Work Package 顺序、状态和范围的执行真相源；技术选择及其状态仍以 ADR 为准。

Phase 4–7 只记录开始前必须采用的拆分主题，不在当前阶段冻结具体接口或提前实现。

## 1. 执行规则

- 状态只使用 `planned`、`active`、`stabilizing`、`accepted`。
- 同一时间最多一个 Work Package 为 `active` 或 `stabilizing`。
- 前置 Work Package 未 `accepted` 时，不得开始依赖它的生产实现。
- Work Package 从 `planned` 进入 `active` 前，必须补充实际允许目录、验收环境和回退命令。
- 每个 Work Package 完成生产实现后必须进入 `stabilizing`，清零 P0、P1 和退出条件相关缺陷后才能标记 `accepted`。
- 文档调研、只读验证和不进入生产分支的实验记录可以提前进行，但不得提前提交后续 Work Package 的生产代码。
- Work Package 状态只在本文登记，避免在主计划、提交正文和多个清单中维护互相冲突的状态。

每个 Work Package 的激活记录至少包含：

```text
状态：active
开始日期：
允许目录：
明确禁止目录：
验收环境：
关联 ADR：
计划提交：
```

完成记录至少包含：

```text
状态：stabilizing / accepted
自动测试：
故障测试：
真实应用验收：
已知问题：
回退步骤：
关联提交：
```

## 2. Work Package 总览

| Work Package | 主要结果 | 依赖 | 当前状态 |
|---|---|---|---|
| WP-0-01 | legacy Qt、工具链和验收环境基线 | 无 | accepted |
| WP-0-02 | 用户数据与共享应用锁契约基线 | WP-0-01 | planned |
| WP-0-03 | 旧迁移逐文件复用准入清单 | WP-0-01 | planned |
| WP-0-04 | 架构审查收口并批准首个实现 WP | WP-0-02、WP-0-03 | planned |
| WP-1A-01 | 不启动 Python 的最小 Tauri Shell | WP-0-04 | planned |
| WP-1A-02 | 透明窗口几何、锚点和表现状态 | WP-1A-01 | planned |
| WP-1A-03 | 点击穿透、拖动、焦点和 IME 技术门 | WP-1A-02 | planned |
| WP-1A-04 | 共享应用锁、legacy Qt 入口和 v2 开发入口 | WP-1A-03 | planned |
| WP-1B-01 | Windows 受控进程树原语 | WP-1A-04 | planned |
| WP-1B-02 | 串行 Supervisor 与 generation 生命周期 | WP-1B-01 | planned |
| WP-1B-03 | Fake Core 正常启动和关闭链 | WP-1B-02 | planned |
| WP-1B-04 | Supervisor 恢复、竞态和进程泄漏门禁 | WP-1B-03 | planned |
| WP-1C-01 | 最小无 Qt Python Core Host 与基础握手 | WP-1B-04 | planned |
| WP-1C-02 | initialize、readiness 和最小 Snapshot | WP-1C-01 | planned |
| WP-1C-03 | 协议协商、stderr 排水和故障 transport | WP-1C-02 | planned |
| WP-1C-04 | bundled Python 端到端与 lifecycle 接口冻结 | WP-1C-03 | planned |
| WP-1D-01 | Shell 启动、初始化和失败状态路由 | WP-1C-04 | planned |
| WP-1D-02 | diagnostics 与最小 Runtime Repair 页面 | WP-1D-01 | planned |
| WP-1D-03 | 手动重试和恢复路径端到端验收 | WP-1D-02 | planned |
| WP-2-01 | 并发 request/response/event Router | WP-1D-03 | planned |
| WP-2-02 | 控制面优先级与阻塞任务隔离 | WP-2-01 | planned |
| WP-2-03 | Operation、deadline 和取消语义 | WP-2-02 | planned |
| WP-2-04 | WebView 到 Rust 的受控 Gateway | WP-2-03 | planned |
| WP-2-05 | Snapshot revision、generation 和资源描述符 | WP-2-04 | planned |
| WP-2-06 | 背压、协议故障矩阵与基础 Envelope 冻结 | WP-2-05 | planned |
| WP-3-01 | 无 Qt Assistant Adapter 与真实 readiness | WP-2-06 | planned |
| WP-3-02 | 无 UI 的真实聊天 Core 垂直链 | WP-3-01 | planned |
| WP-3-03 | 使用 Fake Core 的桌宠聊天表现层 | WP-3-02 | planned |
| WP-3-04 | 真实聊天与桌宠 UI 端到端接通 | WP-3-03 | planned |
| WP-3-05 | Core 崩溃恢复与 UI 重新水合 | WP-3-04 | planned |
| WP-3-06 | legacy Qt → Tauri v2 → legacy Qt 兼容门禁 | WP-3-05 | planned |

## 3. Phase 0：冻结与基线

### WP-0-01：legacy Qt、工具链和验收环境基线

激活记录：

```text
状态：active
开始日期：2026-07-15
初始允许目录：docs/runtime-v2/baselines/；仅允许在本文更新 WP-0-01 状态与验收记录
稳定化例外：门禁确认 legacy Qt 退出/启动 P1 后，允许窄改 main.py、app/core/resource_manager.py、app/agent/memory.py、tests/unit/test_resource_manager.py、tests/unit/test_memory_store_resources.py；不得改变 Assistant 业务语义
明确禁止目录：除上述例外外的 app/；desktop/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；用户数据 schema；旧迁移分支代码
验收环境：当前 Windows 开发机；项目 .\runtime\python.exe；本机已存在的 Rust/Cargo、Node/npm、Tauri CLI、WebView2；不安装新依赖；物理 UI 能力以实际可用项为准
关联 ADR：ADR-0001（进程退出与残留基线输入）；ADR-0003（legacy Qt 回退与数据安全边界输入）
计划提交：test(runtime): 收口 legacy Qt 基线验收
```

稳定化记录：

```text
状态：stabilizing
自动测试：.\runtime\python.exe -m pytest；1459 collected，1438 passed，6 failed，3 skipped，12 errors；pytest 49.60s，进程墙钟 51.5s
故障测试：失败集合复跑为 33 passed、6 failed、12 errors；backchannel 改用全新系统临时 basetemp 后 6 passed in 0.32s
真实应用验收：10 次有界 legacy Qt 启动均检测到 PetWindow visible，request_quit 返回 True，main 返回 0；批次结束无新增 Python、Node、浏览器、Settings、Studio 进程
已知问题：固定 basetemp 悬空符号链接；D:\ 根目录 PermissionError；Tauri CLI 缺失；单屏/100% DPI/单角色/TTS disabled；启动代理 p95 1236.437ms；pytest 与 GUI 取证存在真实配置、日志和运行事件数据污染风险
回退步骤：仅回退本 WP 文档和状态记录；不得自动改写或删除真实 data/ 中同期日志、配置或运行事件
关联提交：未提交；数据隔离与重复执行门禁未满足
```

验收记录：

```text
状态：accepted
自动测试：提交态隔离源码树完整 pytest；1463 collected，1460 passed，3 skipped，52.93s，退出码 0
真实应用验收：10/10 PetWindow visible；request_quit=True；main/进程退出码 0；stderr 为空；无记录后代进程残留
启动代理：min 1066.849ms；median 1099.539ms；mean 1145.304ms；p95/max 1537.577ms
数据门禁：accepted 批次前后真实 data/ 全文件相对路径、长度、UTC mtime、SHA-256 清单完全一致
稳定化修复：关闭后等待 lingering QThread；asyncio loop 幂等 stop 与 pending task 清理；Memory preload 在启动后台线程前完成 anyio 首次导入
已知限制：Tauri CLI 缺失；单屏/100% DPI；多 DPI、IME、音频、干净机和真实业务交互仍受限；p95 高于 1 秒目标
回退步骤：整体 revert 本 WP 提交；不自动改写或删除真实 data/ 中既有日志、配置或运行事件
关联提交：本 WP accepted 提交
```

主要结果：形成可以重复执行的迁移前基线，后续任何“等价”“改善”或“没有回归”都有可比较证据。

允许范围：

- Runtime v2 基线文档。
- 只读诊断脚本和测试辅助代码。
- 不改变生产行为的基线测量工具。
- 仅在稳定化门禁确认 P1 后，为满足本 WP 退出条件所需的最小 legacy Qt 启动/退出修复和回归测试。

必须记录：

- 当前 `main.py` 启动、退出、首次设置、聊天、取消、角色切换、历史、Memory、Tools、MCP、插件、TTS、截图、主动互动、设置和工作室入口。
- 已知问题、现有失败测试和受限人工验收项。
- Python、PySide6、Rust、Cargo、Node、Tauri CLI、WebView2 和 Windows 版本。
- 当前自动测试命令、通过数量、耗时和不稳定测试。
- 冷启动可见时间的定义、参考机器、采样次数和统计方式。
- 单屏、多屏、DPI、IME、音频设备和干净机验收能力。

明确非目标：

- 不创建 Runtime v2 Tauri 工程。
- 不改变当前 Qt 产品功能、Assistant 业务语义或用户数据格式。
- 不修复与基线记录无关的既有缺陷。

退出证据：

- Qt 真实冒烟清单可以由另一轮本地执行重复。
- 自动测试与人工验收分别标记为通过、失败或受限，不以自动测试替代真实 UI 结论。
- 启动性能指标拥有明确测量方法，不只记录单次观察值。

回退：整体 revert 本 WP 的文档、测试辅助、回归测试和三处窄修复；不得改写真实用户数据。

### WP-0-02：用户数据与共享应用锁契约基线

主要结果：明确 Runtime v2 可以读取、可以兼容写入和禁止修改的数据边界，并冻结双入口互斥的结果契约。

允许范围：

- 数据路径和 schema 清单。
- 脱敏测试夹具。
- 应用锁设计记录和兼容验收脚本设计。

必须记录：

- 角色、Core 配置、历史、Memory、插件配置和用户资源目录的真实路径、格式、版本字段、原子写入方式和写入者。
- `desktop.*`、`ui.*` 的独立存储位置或命名空间。
- Qt 可忽略的兼容新增字段和禁止写入的格式。
- 同一用户会话使用的稳定 lock identity、持有时间、异常退出释放行为和冲突提示。
- Qt → Tauri → Qt 兼容夹具、备份失败、临时写入失败、异常中断和未来 schema 的安全失败预期。

明确非目标：

- 不实现 Tauri 应用锁。
- 不迁移或改写真实用户数据。
- 不建设通用 schema migration 平台。

退出证据：

- 每类共享数据至少有一个脱敏代表样本或明确的样本缺失记录。
- 兼容矩阵明确区分只读复用、兼容写入、v2 专属和禁止修改。
- ADR-0003 的 Phase 1A 与 Phase 3 验收输入已经可执行。

回退：删除新增文档和脱敏夹具，不触碰真实用户数据。

### WP-0-03：旧迁移逐文件复用准入清单

主要结果：把旧迁移从“可整体恢复的实现”转换为固定来源、逐文件审查的证据库。

固定取证源：

```text
branch: feat/tauri-assistant-migration
commit: 190dfafd24f5c5226bff8b4347837b6e45d9a331
```

允许范围：

- 只读比较旧迁移 commit。
- 复用矩阵、依赖图和候选测试清单。
- 不进入生产路径的最小验证记录。

每个候选文件或模块必须记录：

- 原路径和固定 commit。
- 复用原因及对应未来 Work Package。
- Qt、生命周期、全局状态、同步调度和持久化依赖。
- 可以保留、必须删除和需要重写的部分。
- 修改现有 Assistant 业务语义的风险。
- 对应自动测试、故障测试和替代方案。

初始分类原则：

- 优先准入：协议 codec、golden fixtures、纯 DTO、纯算法、故障场景和测试夹具。
- 谨慎准入：Assistant Adapter、TTS 合成拆分、截图坐标算法、Headless Scheduler、主题和路径模型。
- 原则上拒绝：巨型 BrainHostApplication、secondary window bridge、混合所有权 AppState、同步 Supervisor、巨型设置页和工作室脚本。

明确非目标：

- 不 cherry-pick 旧迁移 commit。
- 不整体恢复任何 stash 或目录。
- 不因为旧实现已有测试就直接视为准入。

退出证据：

- 所有计划在 Phase 1A–3 复用的文件都有明确准入结论。
- 每项“复用”都绑定一个具体 Work Package，没有“以后可能需要”的无归属候选。
- 被拒绝模块的有效故障经验和测试场景已单独保留。

回退：只包含审查记录，可独立 revert。

### WP-0-04：架构审查收口并批准首个实现 WP

主要结果：关闭进入 Phase 1A 前的决策缺口，并把 WP-1A-01 从 `planned` 更新为可激活状态。

必须确认：

- 单透明窗口是产品硬约束还是 Phase 1A 的首选技术方案；验证失败时的停止或备选路径是什么。
- Phase 1A 切换 v2 分支默认入口后，到 Phase 3 前使用 legacy Qt 的 dogfooding 成本可以接受。
- `hello`、`initialize`、`shutdown` 的初始 deadline 和不可重试错误分类可以进入技术验证。
- Runtime v2 的工具链版本、参考 Windows 环境和 bundled Python 来源明确。
- 主计划、治理文件、三份 ADR 和本文不存在冲突或悬空引用。

明确非目标：

- 不编写 Tauri 或 Core Host 生产代码。
- 不把 Proposed ADR 误标为已经技术验证。
- 不提前冻结 Phase 2 仍需实验的 sequence、executor 和 frame 上限参数。

退出证据：

- 主计划完成最终审查，状态文字与实际批准范围一致。
- 三份 ADR 被认可为 `Proposed` 技术基线。
- WP-0-01、WP-0-02、WP-0-03 均为 `accepted`。
- WP-1A-01 已补齐激活记录所需的允许目录、验收环境和回退方式。

回退：恢复文档状态，不影响运行时代码。

## 4. Phase 1A：空 Shell 与透明窗口技术门

### WP-1A-01：不启动 Python 的最小 Tauri Shell

主要结果：Runtime v2 拥有一个不依赖 Python、可以立即显示、诊断启动失败并正常退出的最小桌面根。

允许能力：

- `desktop/` 最小 Tauri crate、静态 startup 页面、构建配置和最小日志。
- 单个普通验证窗口或尚未启用复杂交互的透明主窗口。
- 与该 Shell 直接相关的 Rust、前端和启动冒烟测试。

明确禁止：

- 不启动 Python。
- 不实现 Supervisor、IPC、聊天、设置、托盘和角色加载。
- 不切换当前默认入口。

退出证据：

- 开发构建和 release 构建均可启动并显示 startup 页面。
- Python 缺失、环境变量缺失和数据目录异常不阻止 Shell 显示与退出。
- Shell 关闭后无自身后台任务或窗口残留。

独立回退：删除或 revert 最小 `desktop/` Shell，不影响 Qt 入口。

### WP-1A-02：透明窗口几何、锚点和表现状态

主要结果：验证单透明桌宠窗口在不接入真实交互和 Python 的情况下，可以稳定表达基础窗口状态并保持立绘桌面锚点。

允许能力：

- `idle`、`bubble`、`composer`、`expanded` 四种窗口状态。
- 固定立绘锚点、向上/向左扩展、工作区边界修正和主题占位内容。
- 单屏、多屏、负坐标、100%/125%/150% DPI 的几何测试。

明确禁止：

- 不实现 Python Core、聊天请求或真实角色业务状态。
- 不实现复杂动画、局部模糊和前端框架迁移。
- 不在本 WP 切换默认入口或实现共享应用锁。

退出证据：

- 状态切换不移动立绘桌面锚点。
- 长占位文本不会无限扩大原生窗口。
- 多屏、负坐标和目标 DPI 下尺寸与边界可重复。
- 显示、隐藏和展开没有明显白闪或布局抖动。

独立回退：回退窗口状态和布局模块，保留 WP-1A-01 Shell。

### WP-1A-03：点击穿透、拖动、焦点和 IME 技术门

主要结果：证明透明桌宠窗口的鼠标命中、输入和焦点模型在目标 Windows 环境真实可用。

允许能力：

- 透明区域点击穿透。
- 立绘或指定拖动区域移动窗口。
- 输入框、按钮和可交互区域命中。
- 中文 IME、焦点恢复、Alt+Tab、显示/隐藏和窗口展开交互。
- 实现这些行为所需的最小 Windows/Tauri 平台代码。

明确禁止：

- 不接入 Python 或聊天。
- 不实现托盘、设置和其他次级窗口。
- 不用多个临时兼容层掩盖单窗口方案失败。

退出证据：

- 真实 WebView 和物理鼠标/键盘验收通过，不能只靠 DOM 单元测试。
- IME 候选框位置、窗口焦点和点击穿透在目标 DPI 下正确。
- 若单窗口方案失败，按 WP-0-04 的既定路径停止或更新架构，不直接进入 WP-1A-04。

独立回退：回退命中和焦点平台代码，保留静态透明窗口与布局验证结果。

### WP-1A-04：共享应用锁、legacy Qt 入口和 v2 开发入口

主要结果：两个桌面入口竞争同一个应用锁，legacy Qt 成为明确回退入口，当前 v2 分支默认入口安全切到 Tauri。

允许能力：

- 将现有 Qt 入口保存为 `legacy_qt_main.py` 和显式启动脚本。
- Tauri 与 Qt 共用稳定 lock identity。
- 当前 v2 开发分支的 `main.py`/启动脚本切换到 Tauri。
- 冲突提示、异常退出释放和入口测试。

明确禁止：

- 不启动 Python Core。
- 不改变正式安装包入口和发布链。
- 不修改共享用户数据 schema。

退出证据：

- Qt 持锁时 Tauri 安全失败，Tauri 持锁时 Qt 安全失败。
- 异常退出后锁由操作系统释放，不依赖残留标志文件。
- Tauri 默认入口无 Python 时仍可显示和退出。
- legacy Qt 命令可以启动当前完整 Qt 应用。

独立回退：恢复原 `main.py` 和启动脚本，并回退双方应用锁接入。

## 5. Phase 1B：进程监管与 Fake Core

### WP-1B-01：Windows 受控进程树原语

主要结果：建立每个 generation 独立、可验证回收后代进程的 `ManagedProcessTree` Windows 实现。

允许能力：

- Windows Job Object 或经 ADR 更新后等价的受控进程树机制。
- 测试子进程与一层、多层后代进程。
- `spawn`、`pid`、`wait`、`terminate_tree`、`verify_tree_exited` 和句柄释放语义。

明确禁止：

- 不实现协议握手、自动重启或 Python Assistant。
- 不用模糊的单一 `close()` 混合正常释放、强杀和 Drop 保险。
- 无法建立受控进程树时不得继续运行未监管子进程。

退出证据：

- 正常退出、忽略退出和多后代进程均可确定回收。
- Job 建立失败安全返回，不遗留已创建进程。
- 在父进程本身处于 Windows Job 的目标环境中完成技术验证或记录明确限制。

独立回退：回退 Windows 进程树模块和对应测试，不影响 Shell。

### WP-1B-02：串行 Supervisor 与 generation 生命周期

主要结果：所有 spawn、stop、restart 和 app shutdown 意图通过一个串行状态机处理，并以 generation 隔离旧回调。

允许能力：

- SupervisorState。
- generation ID、generation number、独立 cancellation token。
- 串行意图队列、幂等 stop/finalize 和 app shutdown 禁止重启规则。
- 使用测试进程或抽象进程树，不建立真实业务 transport。

明确禁止：

- 不实现自动恢复策略、协议 Router 和 Python Core。
- 不让窗口或多个任务直接操作 `ManagedProcessTree`。

退出证据：

- shutdown during spawn、stop 中 retry、连续 stop 和旧 generation 回调均有确定结果。
- 同一时间最多一个 spawn、stop 或 restart 流程。
- app shutdown 开始后不能创建新 generation。

独立回退：回退 Supervisor 状态机，保留已验证的进程树原语。

### WP-1B-03：Fake Core 正常启动和关闭链

主要结果：用最小测试 transport 验证 Supervisor 可以完成 Fake Core 的 spawn、最小 hello、运行、协议关闭和最终回收。

允许能力：

- 测试专用 Fake Core 和最小握手。
- 正常启动、延迟 hello、正常 shutdown 和忽略 shutdown 后强杀。
- 基础启动/关闭 deadline。

明确禁止：

- 不冻结业务 IPC Envelope。
- 不创建真实 `app.core_host`。
- 不接入 initialize、Snapshot 或 Assistant 模块。

退出证据：

- 正常路径和强制回收路径均无后代残留。
- 延迟 hello 不阻塞 Tauri 主线程和窗口退出。
- 协议关闭与进程退出同时发生时汇合到同一幂等 finalize。

独立回退：回退 Fake Core transport 集成，保留 Supervisor 和进程树。

### WP-1B-04：Supervisor 恢复、竞态和进程泄漏门禁

主要结果：完成 ADR-0001 所需的有限重启、手动重试、竞态和完整故障矩阵。

允许能力：

- restart budget/backoff、不可重试分类占位和手动 retry。
- spawn、hello、运行、停止和 backoff 各阶段的 shutdown/retry 竞态。
- Fake Core 崩溃、卡死、旧 generation 事件和后代忽略退出。

明确禁止：

- 不接入真实 Python Assistant。
- 不以无限重启或额外全局状态绕过竞态。

退出证据：

- ADR-0001 Fake Core 验证矩阵全部自动化或明确记录受限项。
- 重复启停、连续失败和手动 retry 后无进程、句柄和计时器泄漏。
- ADR-0001 可以从 `Proposed` 更新为 `Technically Validated`，但仍需实现审查后才进入 `Accepted`。

独立回退：回退恢复策略，保留确定的单次启动和关闭链。

## 6. Phase 1C：最小真实 Core Host

### WP-1C-01：最小无 Qt Python Core Host 与基础握手

主要结果：真实 Python 子进程先建立 transport，并在不导入 Qt 或重型领域模块的情况下响应 hello、health 和 shutdown。

允许能力：

- `app.core_host` 最小入口、帧读写、control dispatcher 和单 writer queue。
- `system.hello`、`system.health`、`system.shutdown`。
- import guard、stdout 污染检测和基础错误 Envelope。

明确禁止：

- hello 前不得导入 Assistant、Memory、MCP、插件、TTS、PySide6 或 `app.ui`。
- 不实现 initialize、聊天和并发业务 Router。

退出证据：

- import guard 证明最小 Host 路径无 Qt 和重型领域导入。
- 分片帧、合并帧、非法 JSON、超大帧和 stdout 污染安全失败。
- health 和 shutdown 在无业务初始化时可靠响应。

独立回退：回退 `app.core_host` 与真实 Host 接入，保留 Fake Core Supervisor。

### WP-1C-02：initialize、readiness 和最小 Snapshot

主要结果：Core 在握手后后台初始化假组件，并通过 CoreReadiness 和最小 Snapshot 表达状态。

允许能力：

- `core.initialize`。
- `transport_ready`、`initializing`、`setup_required`、`ready`、`degraded`、`failed`。
- 最小组件状态、generation、revision 和 capability Snapshot。
- 初始化卡死和 initialize 期间 shutdown。

明确禁止：

- 不加载真实 Assistant、Memory、MCP、插件、Tools 或 TTS。
- 不让 Rust 修改 Python Snapshot 业务字段。

退出证据：

- hello 响应不等待初始化。
- 初始化卡死时 health、shutdown 和 Shell 仍响应。
- 新 generation 建立时旧 Snapshot 立即失效。

独立回退：回退 initialize/readiness 层，保留基础 Host 握手。

### WP-1C-03：协议协商、stderr 排水和故障 transport

主要结果：建立 Desktop/Core/Protocol 版本协商、日志排水和真实 transport 故障边界。

允许能力：

- protocol major/minor 和 capabilities。
- generation credential 的最小验证机制。
- Rust 持续排空 stderr、有界日志队列、generation/PID 标记和脱敏。
- Rust 主动关闭 stdin、损坏帧、旧 generation 消息和协议不兼容。

明确禁止：

- 不实现并发业务请求、Operation 或聊天。
- 不将 API Key、完整 Prompt、credential 和插件私密配置写入普通日志/UI。

退出证据：

- major 不兼容和缺失必要 capability 进入 diagnostics，且不无限自动重启。
- stderr 持续输出和日志过载不阻塞 Core。
- Rust 关闭 stdin 后 Python 在 deadline 内退出或由 Supervisor 回收。

独立回退：回退协商和日志增强，保留兼容的最小握手路径。

### WP-1C-04：bundled Python 端到端与 lifecycle 接口冻结

主要结果：使用目标 bundled Python 完成真实进程树、握手、initialize、Snapshot 和 shutdown，并冻结最小 lifecycle 接口。

允许能力：

- bundled Python 定位、环境构造和 release 资源路径。
- Phase 1C 全链端到端测试和协议 golden fixtures。
- lifecycle 接口文档与变更控制记录。

明确禁止：

- 不接入聊天和 Assistant 领域服务。
- 不为未来 Named Pipe、Unix Domain Socket 或代码生成平台提前实现抽象。

退出证据：

- 开发和 release 环境均使用目标 Python 完成全链冒烟。
- lifecycle fixture 可由 Rust 和 Python 共同读取。
- ADR-0002 完成 Phase 1C 的 `Technically Validated` 前置证据。
- 后续破坏性 lifecycle 修改必须暂停功能开发并更新 ADR/fixtures。

独立回退：恢复到 Fake Core 或开发 Python 路径，不影响 legacy Qt。

## 7. Phase 1D：恢复、诊断和修复入口

### WP-1D-01：Shell 启动、初始化和失败状态路由

主要结果：Shell 使用 SupervisorState、CoreReadiness 和 ShellRoute 的组合展示真实启动状态。

允许能力：

- startup、pet 占位、diagnostics 和 fatal_error 路由。
- spawning、transport ready、initializing、setup required、degraded、failed 和 restarting 提示。
- 状态组合和旧 generation UI 事件过滤。

明确禁止：

- 不实现自动 Runtime 修复。
- 不接入聊天、设置或真实角色业务。

退出证据：

- Core 缺失、启动失败、初始化卡死和崩溃均不会产生空白窗口。
- UI 路由不反向成为 Supervisor 或 CoreReadiness 真相源。

独立回退：回退状态页面，保留底层 Supervisor/Core Host。

### WP-1D-02：diagnostics 与最小 Runtime Repair 页面

主要结果：用户可以理解 Runtime 缺失、损坏、协议不兼容和初始化失败，并执行安全操作。

允许能力：

- 错误原因、Desktop/Core/Protocol 版本、运行目录和日志位置。
- 重试、打开诊断、打开安装说明或文件位置、退出。
- 受控显示必要绝对路径。

明确禁止：

- 不自动下载、替换、迁移或回滚 Python Runtime。
- 不向普通 WebView 暴露任意文件读取或 Shell 命令。

退出证据：

- 每种不可重试错误有明确说明和安全操作。
- 诊断页面不泄露 credential、API Key、完整 Prompt 或私密配置。

独立回退：回退 Runtime Repair 页面和动作，保留基础 diagnostics 状态。

### WP-1D-03：手动重试和恢复路径端到端验收

主要结果：用户触发的 retry 通过同一 Supervisor 状态机完成旧树清理、新 generation 创建和页面恢复。

允许能力：

- diagnostics/restarting 页面上的手动 retry。
- 连续点击合并、停止期间 retry 和失败预算重置规则。
- Shell、Supervisor、Core Host 的恢复端到端测试。

明确禁止：

- 不引入第二套重启路径。
- 不接入真实 Assistant 业务。

退出证据：

- 旧 generation 未清理完成前不会创建新 generation。
- 连续 retry 只产生一个有效意图。
- 恢复成功后页面状态与当前 generation 一致。

独立回退：移除 UI retry 入口，保留自动恢复和诊断能力。

## 8. Phase 2：并发 IPC 与只读快照

### WP-2-01：并发 request/response/event Router

主要结果：支持多个并发 in-flight request、乱序 response 和 event/response 交错，同时保持有界队列。

允许能力：

- Rust pending request router、独立 reader/writer。
- Python bounded task registry 和单 writer queue。
- request ID、generation 校验和窗口关闭后的 waiter 清理。

明确禁止：

- 不接入真实 Assistant、设置、TTS、Tools 或截图。
- 不让业务任务直接写 stdout。

退出证据：

- 并发响应乱序和事件交错不串请求。
- reader、writer 和 pending registry 均有界且可清理。
- 旧 generation response 不能完成当前 waiter。

独立回退：恢复到最小 lifecycle transport，不影响 Supervisor。

### WP-2-02：控制面优先级与阻塞任务隔离

主要结果：同步 sleep、阻塞文件 I/O 和 CPU 密集任务运行时，health、cancel 和 shutdown 仍可处理。

允许能力：

- control dispatcher 与 domain execution plane 分离。
- bounded async task、thread executor 和必要的测试 worker process。
- control/interactive/background 调度语义。

明确禁止：

- 不为假设中的插件平台建设通用 worker 编排器。
- 不在 transport/control 线程执行领域代码。

退出证据：

- sleep、阻塞 I/O、CPU 循环和非协作任务故障测试通过。
- thread 无法安全终止的任务拥有明确的 worker process 或 Core 强杀边界。
- shutdown/cancel 不排在普通长任务之后。

独立回退：回退执行平面实现，保留并发 transport Router。

### WP-2-03：Operation、deadline 和取消语义

主要结果：长任务通过 generation-scoped Operation 表达，并定义 deadline、取消竞态和唯一终态。

允许能力：

- operation accepted、started、progress、completed、failed、cancelled。
- request deadline、operation cancel 和不可取消原因。
- 完成与取消同时发生、重复取消和晚到事件的幂等规则。

明确禁止：

- 不实现真实聊天 token streaming。
- 不以 Core 全局 busy 状态代替独立 Operation 生命周期。

退出证据：

- 每个 Operation 最多一个可见终态。
- Core 重启、窗口关闭和 deadline 到期会清理 registry/waiter。
- progress 丢弃或合并不影响终态交付。

独立回退：回退 Operation API，保留短请求并发 Router。

### WP-2-04：WebView 到 Rust 的受控 Gateway

主要结果：WebView 只能提交注册过的业务意图，不能伪造 transport 和授权字段。

允许能力：

- command 注册表、payload 校验、窗口权限和 deadline/priority 上限。
- Rust 注入 request ID、generation、协议版本和 credential。
- 前端封装 client、Tauri capability、CSP 和远程导航限制。

明确禁止：

- 不提供无限制 `host_call(method, params)`。
- 不向 WebView 暴露任意文件系统、Shell 或 Python 内部方法。

退出证据：

- 未知 command、错误窗口、伪造字段和超限 payload 默认拒绝。
- 页面导航或注入不能扩大当前窗口权限。
- Gateway 安全失败不影响 Shell 退出和诊断。

独立回退：禁用业务 Gateway，保留只读 lifecycle 状态展示。

### WP-2-05：Snapshot revision、generation 和资源描述符

主要结果：建立只读 Snapshot 缓存、revision 重取和最小 generation-scoped 受控资源描述符。

允许能力：

- 完整 Snapshot 请求、revision 校验和新 generation 清空。
- 组件状态、公开角色摘要和 active interaction 摘要。
- 使用测试资源验证 opaque token、TTL、大小、窗口/command 范围和读取次数。

明确禁止：

- Rust 不推导或修改 Python 业务对象。
- Snapshot 不包含密钥、完整 Prompt、私密配置和任意裸路径。
- 不在本 WP 接入真实截图、音频或角色导入。

退出证据：

- revision 不连续时请求完整 Snapshot，不在 Rust 猜测补丁。
- 旧 generation Snapshot 和 token 立即失效。
- token 不能扩展为任意文件访问。

独立回退：关闭测试资源能力，保留基础 Snapshot。

### WP-2-06：背压、协议故障矩阵与基础 Envelope 冻结

主要结果：完成 ADR-0002 的背压、损坏协议、慢 writer 和 golden fixtures 门禁，并冻结基础 Envelope。

允许能力：

- progress 合并、预留 control/response/terminal 配额和过载断开策略。
- 大量 progress、慢 writer、超大帧、stdout 污染和连接关闭测试。
- Rust/Python golden fixtures 和基础 Envelope 变更控制。

明确禁止：

- 不因测试方便扩大 frame 上限或改用裸文件路径。
- 不提前接入 Phase 3 业务。

退出证据：

- shutdown/cancel response 和终态事件不会被 progress 挤出。
- 无法恢复的过载只关闭当前 generation，不拖垮 Shell。
- ADR-0002 可在实现审查后更新为 `Accepted`。
- 后续破坏性 Envelope 修改必须暂停功能开发并更新 ADR/fixtures。

独立回退：回退背压优化参数，保留已冻结 Envelope 和安全断开边界。

## 9. Phase 3：基础聊天垂直链

### WP-3-01：无 Qt Assistant Adapter 与真实 readiness

主要结果：`app.core_host` 通过 Adapter/Facade 建立当前角色、Assistant Session、Chat Pipeline 和基础 Provider，并表达真实 ready/setup_required/degraded。

允许能力：

- 读取现有角色与 Core 配置。
- 构建基础 Provider 客户端，不在启动阶段发起远程网络验证。
- 建立最小 Assistant Session 和公开角色 Snapshot。
- 为真实迁移需要增加的无 Qt Adapter 和等价性测试。

明确禁止：

- 不重写 AgentRuntime、Memory、插件、MCP、TTS 或配置领域。
- 不修改现有业务语义；确需修改必须拆出独立批准范围。
- 不接入聊天 UI。

退出证据：

- 无角色、无有效 Provider 和首次配置未完成进入 setup_required，不触发重启。
- 可选组件失败进入 degraded，不阻止基础 Session 建立。
- Core Host 导入和运行路径不加载 PySide6 或 Qt UI。

独立回退：回退 Assistant Adapter，Core Host 退回假组件 readiness。

### WP-3-02：无 UI 的真实聊天 Core 垂直链

主要结果：通过冻结 IPC 完成真实 `chat.send`、完成、错误和取消链，不依赖桌宠 UI。

允许能力：

- `chat.send`、`chat.started`、必要 progress、`chat.completed`、`chat.failed`、`chat.cancelled`。
- 真实 Chat Pipeline、基础历史写入和可恢复 Provider 错误。
- 自动测试使用确定性 fake/local Provider，人工验收使用已有开发配置。

明确禁止：

- 不实现 TTS、Tools 确认、截图、主动事件和 token streaming。
- 不将跳过打字机映射为 Core cancel。

退出证据：

- 正常回复、网络错误、格式错误、取消和 Core shutdown 均有唯一终态。
- Provider 网络不可达只影响请求，不改变 Core readiness 为启动失败。
- History 失败时仍可聊天并返回 degraded/不保存提示所需状态。

独立回退：关闭真实 chat command，保留 Assistant readiness 和 Core Host。

### WP-3-03：使用 Fake Core 的桌宠聊天表现层

主要结果：使用确定性 Fake Core 完成立绘、初始消息、气泡、输入框、思考、错误、取消和完整回复打字机展示。

允许能力：

- 立绘加载和简单淡入淡出/位移。
- composer 打开/关闭、发送、取消、错误和重连状态。
- 完整回复后的 WebView 打字机和立即跳过动画。
- 长文本边界、主题、DPI、IME 和固定锚点视觉验收。

明确禁止：

- 不接入真实 Provider 或修改 Python Assistant。
- 不实现 Live2D、复杂 Canvas、局部模糊或高级动画引擎。

退出证据：

- Fake Core 可以稳定驱动成功、慢响应、错误、取消和重启 UI 状态。
- 跳过打字机不发送 Core cancel。
- CSS 动画不阻塞输入、取消或关闭。

独立回退：回退聊天表现模块，保留 Phase 1A Shell 和窗口技术门。

### WP-3-04：真实聊天与桌宠 UI 端到端接通

主要结果：把 WP-3-02 的真实聊天 Core 链接入 WP-3-03 的桌宠表现层，形成第一条真实产品垂直链。

允许能力：

- 真实角色立绘、初始消息、输入、发送、思考、完成、错误和取消。
- 根据回复段或表情状态切换立绘。
- 受控 Gateway、Chat Operation 和 UI 状态映射。

明确禁止：

- 不加入 TTS、Tools、截图、主动互动、设置、历史窗口和工作室。
- 不为 UI 便利破坏 lifecycle 或基础 Envelope。

退出证据：

- 使用已有开发配置完成真实聊天和取消。
- UI 与 Core 的终态一致，晚到旧 generation 事件不改变当前界面。
- 真实主题、长文本、IME 和目标 DPI 验收通过。

独立回退：切回 Fake Core UI 演示路径，保留真实 headless chat 能力。

### WP-3-05：Core 崩溃恢复与 UI 重新水合

主要结果：Core 崩溃时桌宠窗口保持存在，旧 generation 立即失效，新 Core ready 后按明确契约恢复 UI。

允许能力：

- 崩溃、重启和 rehydrating 状态。
- 当前气泡、最后完成回复、未提交输入、活动交互摘要和可恢复/不可恢复状态的明确所有权。
- 重启后完整 Snapshot 重取和 UI 水合。

明确禁止：

- 不跨 generation 恢复未完成模型任务、Operation 或工具确认。
- 不把 WebView 草稿提升为 Python 领域真相源。

退出证据：

- 强杀 Core 不关闭或重建桌宠窗口。
- 旧请求、Operation、Snapshot、token 和事件全部失效。
- UI 明确区分已完成内容、已中断请求和仍保留的本地草稿。
- 重复崩溃受 restart budget 控制且无进程树残留。

独立回退：保留崩溃诊断但禁用自动 UI 水合，退回显式重新开始交互。

### WP-3-06：legacy Qt → Tauri v2 → legacy Qt 兼容门禁

主要结果：证明 v2 dogfooding 不会破坏现有角色、配置、历史、Memory 和 legacy Qt 回退能力。

允许能力：

- 使用 WP-0-02 夹具执行双向兼容测试。
- Tauri 读取现有数据并写入 Phase 1–3 明确允许的兼容数据。
- 备份失败、临时写入失败、异常中断和未来 schema 安全状态测试。

明确禁止：

- 不执行破坏性 schema 迁移。
- 不以保留 Qt 源码或静态解析测试代替真实双向启动。

退出证据：

```text
legacy Qt 创建/修改数据并退出
-> Tauri 获取同一应用锁并完成基础聊天
-> Tauri 退出且所有写入任务结束
-> legacy Qt 重新获取应用锁并读取兼容数据
```

- 两个入口同时启动时只有一个成功持锁。
- v2 专属配置不改变 Qt 行为。
- 不支持的未来 schema 进入 diagnostics/只读安全状态。
- ADR-0003 可以更新为 `Accepted`。

独立回退：停止 v2 共享数据写入并退回只读使用，不删除 legacy Qt 数据或入口。

## 10. Phase 4–7 开始前的强制拆分主题

以下不是已批准 Work Package，不得提前编码。对应 Phase 开始前必须按本文模板建立编号、依赖、允许目录、故障矩阵和独立回退。

### Phase 4 候选拆分

- TTS 合成接口与 audio ADR。
- 无 Qt/Rust 播放技术门和设备故障验证。
- Action ID 工具确认。
- 手动截图与受控资源。
- 自动观察与主动事件调度。
- Phase 4 组合稳定化和资源回收门禁。

### Phase 5 候选拆分

- `core.*` 配置读取、validate、change plan 和原子保存。
- `desktop.*`、`ui.*` 和已确定的 `audio.*` 配置仓库。
- 设置窗口和逐域保存结果。
- 第一版角色切换与受控 Core 重启。
- 历史分页。
- 扩展诊断 Snapshot。
- 首次设置流程。

### Phase 6 候选拆分

- Workspace/Draft 数据模型。
- 角色导入和校验。
- 预览与运行中 Assistant 隔离。
- 原子保存、发布和回滚。
- 大文件 Operation 和故障恢复。

### Phase 7 候选拆分

- 完整 Python、Rust 和协议测试。
- 真实 Tauri WebView E2E。
- Windows DPI、多屏、IME、托盘、音频和截图验收。
- Core、MCP、TTS、浏览器和更新链故障注入。
- 长时间运行与重复启停。
- 干净 Windows 安装和最终发布入口审查。

## 11. 当前启动点

当前不激活任何生产实现 Work Package。`WP-0-01` 已 accepted；下一项是 `WP-0-02` 用户数据与共享应用锁契约基线，WP-1A-01 及后续继续保持 `planned`。

只有 WP-0-01 至 WP-0-04 全部 `accepted`，主计划完成最终审查，才允许将 WP-1A-01 更新为 `active`。
