# Sakura Runtime v2 Work Package 拆分与执行清单

> 状态：WP-1C-02 accepted（`a06e1dada`）；下一生产阶段为跨平台 Phase 1P
> 工作分支：`refactor/tauri-runtime-v2`
> 主计划：`docs/superpowers/plans/2026-07-14-tauri-python-core-v2.md`
> 治理约束：`docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
> 旧迁移取证源：`feat/tauri-assistant-migration` / `190dfafd24f5c5226bff8b4347837b6e45d9a331`

本文把 Runtime v2 Phase 0–7 拆成可独立验证、稳定化和回退的 Work Package。主计划继续描述产品目标、架构边界和阶段结果；本文是 Work Package 顺序、状态和范围的执行真相源；产品发布能力范围以 `docs/runtime-v2/product-capability-parity.md` 为准；技术选择及其状态仍以 ADR 为准。

Phase 4–7 已建立强制编号和依赖，但每个 WP 仍须在进入 `active` 前补齐实际允许目录、验收环境、故障矩阵和回退命令，不得仅凭总表提前编码。

## 1. 执行规则

- 状态只使用 `planned`、`active`、`stabilizing`、`accepted`。
- 同一时间最多一个 Work Package 为 `active` 或 `stabilizing`。
- 前置 Work Package 未 `accepted` 时，不得开始依赖它的生产实现。
- Work Package 从 `planned` 进入 `active` 前，必须补充实际允许目录、验收环境和回退命令。
- 每个 Work Package 完成生产实现后必须进入 `stabilizing`，清零 P0、P1 和退出条件相关缺陷后才能标记 `accepted`。
- 文档调研、只读验证和不进入生产分支的实验记录可以提前进行，但不得提前提交后续 Work Package 的生产代码。
- Work Package 状态只在本文登记，避免在主计划、提交正文和多个清单中维护互相冲突的状态。
- WP-1A、WP-1B、WP-1C-01/02 的既有 `accepted` 保留为原 Windows 范围内的历史结论；ADR-0004 生效后，平台敏感工作必须完成正式三平台矩阵才能称为全局 accepted。
- WP-1C-02 已以 `a06e1dada66b02474f3d65d4124f31094cda5e9e` 完成实现、验证、accepted 记录和生产提交闭环；下一项允许激活的生产 WP 是 WP-1P-01。
- WP-1C-03 及之后所有生产实现强制依赖 WP-1P-06，不得用已有 Windows 证据绕过跨平台阶段。

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
| WP-0-02 | 用户数据与共享应用锁契约基线 | WP-0-01 | accepted |
| WP-0-03 | 旧迁移逐文件复用准入清单 | WP-0-01 | accepted |
| WP-0-04 | 架构审查收口并批准首个实现 WP | WP-0-02、WP-0-03 | accepted |
| WP-1A-01 | 不启动 Python 的最小 Tauri Shell | WP-0-04 | accepted |
| WP-1A-02 | 透明窗口几何、锚点和表现状态 | WP-1A-01 | accepted |
| WP-1A-03 | 点击穿透、拖动、焦点和 IME 技术门 | WP-1A-02 | accepted |
| WP-1A-04 | 共享应用锁、legacy Qt 入口和 v2 开发入口 | WP-1A-03 | accepted |
| WP-1B-01 | Windows 受控进程树原语 | WP-1A-04 | accepted |
| WP-1B-02 | 串行 Supervisor 与 generation 生命周期 | WP-1B-01 | accepted |
| WP-1B-03 | Fake Core 正常启动和关闭链 | WP-1B-02 | accepted |
| WP-1B-04 | Supervisor 恢复、竞态和进程泄漏门禁 | WP-1B-03 | accepted |
| WP-1C-01 | 最小无 Qt Python Core Host 与基础握手 | WP-1B-04 | accepted |
| WP-1C-02 | initialize、readiness 和最小 Snapshot | WP-1C-01 | accepted |
| WP-1P-01 | 跨平台 target、接口与错误分类冻结 | WP-1C-02 | planned |
| WP-1P-02 | 三平台 RuntimeLocator 与 bundled Python 布局 | WP-1P-01 | planned |
| WP-1P-03 | Windows/POSIX 共享应用锁 backends | WP-1P-02 | planned |
| WP-1P-04 | Windows/macOS/Linux 受控进程树 backends | WP-1P-03 | planned |
| WP-1P-05 | 三平台窗口交互、IME 与原生诊断 backends | WP-1P-04 | planned |
| WP-1P-06 | 三平台最小 Shell + Core lifecycle 和 CI 总门禁 | WP-1P-05 | planned |
| WP-1C-03 | 协议协商、stderr 排水和故障 transport | WP-1P-06 | planned |
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
| WP-4-01 | Memory 能力等价 | WP-3-06 | planned |
| WP-4-02 | Tools、Operation 与 Action ID 确认 | WP-4-01 | planned |
| WP-4-03 | MCP 生命周期与工具调用等价 | WP-4-02 | planned |
| WP-4-04 | Python 插件能力等价 | WP-4-03 | planned |
| WP-4-05 | TTS、播放与音频设备门禁 | WP-4-04 | planned |
| WP-4-06 | 截图、受控资源与平台权限 | WP-4-05 | planned |
| WP-4-07 | 自动观察、主动互动、提醒与任务 | WP-4-06 | planned |
| WP-4-08 | Phase 4 组合稳定化与资源回收 | WP-4-07 | planned |
| WP-5-01 | 配置仓库、校验、change plan 与原子保存 | WP-4-08 | planned |
| WP-5-02 | 设置窗口与首次设置流程 | WP-5-01 | planned |
| WP-5-03 | 角色切换、Session 与历史分页 | WP-5-02 | planned |
| WP-5-04 | 托盘、置顶、快捷键与开机启动 | WP-5-03 | planned |
| WP-5-05 | 浏览器与移动/本地桥接生命周期 | WP-5-04 | planned |
| WP-5-06 | 扩展诊断、Repair 与更新前置检查 | WP-5-05 | planned |
| WP-6-01 | Workspace/Draft 数据模型 | WP-5-06 | planned |
| WP-6-02 | 角色导入、资源与 schema 校验 | WP-6-01 | planned |
| WP-6-03 | 预览与运行中 Assistant 隔离 | WP-6-02 | planned |
| WP-6-04 | 原子保存、发布与回滚 | WP-6-03 | planned |
| WP-6-05 | 大文件 Operation 与故障恢复 | WP-6-04 | planned |
| WP-7-01 | 完整自动化与三平台 CI 矩阵 | WP-6-05 | planned |
| WP-7-02 | 三平台真实 Tauri WebView E2E | WP-7-01 | planned |
| WP-7-03 | 产品功能等价与数据兼容总审查 | WP-7-02 | planned |
| WP-7-04 | 三平台打包、签名、更新与干净安装 | WP-7-03 | planned |
| WP-7-05 | 长时间运行、休眠恢复与故障注入 | WP-7-04 | planned |
| WP-7-06 | 最终发布审查与进入 dev 决策 | WP-7-05 | planned |

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
关联提交：c555e1b95（test(runtime): 收口 legacy Qt 基线验收）
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

激活记录：

```text
状态：active
开始日期：2026-07-15
允许目录：docs/runtime-v2/baselines/；tests/fixtures/runtime_v2/wp_0_02/；tests/unit/test_wp_0_02_data_contract.py；docs/adr/0003-runtime-v2-data-compatibility.md；.gitignore 中仅限跟踪该脱敏角色夹具的精确反向规则；仅允许在本文更新 WP-0-02 状态与验收记录
明确禁止目录：main.py；app/；desktop/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；现有用户数据 schema；旧迁移分支代码；WP-0-03、WP-0-04 及后续 Work Package 生产实现
验收环境：当前 Windows 开发机；项目 .\runtime\python.exe；只读盘点真实仓库与 data/；所有写入/故障注入仅在 temp/runtime-v2-wp-0-02/ 的脱敏夹具副本执行；不安装依赖、不启动 legacy Qt/Tauri、不调用外部服务
关联 ADR：ADR-0003（Phase 1A 共享应用锁输入；Phase 3 双向数据兼容门禁输入）
计划提交：docs(runtime): 建立用户数据与共享应用锁契约
回退命令：git revert <WP-0-02-commit>；不得删除、恢复或改写真实 data/ 和用户资源
```

稳定化记录：

```text
状态：stabilizing
自动测试：docs/runtime-v2/baselines/run_wp_0_02_baseline.ps1 连续三次通过；每轮定向 pytest 4 passed；最终轮 1.27s
故障测试：7/7 passed：正常 Qt-parser→Tauri-compatible append→Qt-parser、强制备份失败、临时写入失败、原子替换失败、异常中断、损坏文件和未来 schema
真实应用验收：本 WP 不启动 legacy Qt/Tauri；真实双入口锁与 Qt→Tauri→Qt 留作 WP-1A-04 / WP-3-06，步骤与结果契约已冻结
数据门禁：真实 data/ 121 个文件；最终 canonical manifest SHA-256 before/after 均为 63d79065372c9943e9de12065dcf6df14eef14447fe2bc56fd43587e533ee6cf；path/length/UTC mtime/SHA-256 零变化
已知问题：当前 Qt QLockFile 前仍有 data/ 动作；多数 legacy 格式无独立 version；best-effort .bak 不等价于 mandatory migration backup；插件/notes/部分角色写回非原子
回退步骤：整体 revert 本 WP 提交；不得触碰真实 data/、characters/、Memory/Qdrant、插件数据、migration backup 或既有 lock artifact
关联提交：待提交
```

验收记录：

```text
状态：accepted
自动测试：主审查文件 10/10 存在；当前 docs/*.md 引用全部存在；一致性矩阵 12/12 项有结论；三份 ADR 状态均为 Proposed；WP-0-01/02/03 状态和实际提交已核对；git diff --check 退出码 0
故障测试：单窗口失败判定、停止/替代/批准流程，hello/initialize/shutdown deadline，不可自动重试分类，Runtime 缺失、shared mutex、未来/损坏 schema 和 legacy Qt 回退责任均已绑定具体未来 WP
真实应用验收：本 WP 仅修改 Markdown，按范围不启动 legacy Qt/Tauri、不创建或编译 Tauri；WP-1A-01 的 debug/release、Python 缺失、startup 可见和退出步骤已准备但仍未执行
ADR 状态：ADR-0001、ADR-0002、ADR-0003 仅认可为 Proposed 技术基线，没有标记为 Technically Validated 或 Accepted
工具链与平台：Windows 11 23H2 build 22631.4890 x64；Rust/Cargo 1.96.0；Tauri 2.11.3；tauri-build 2.6.3；WebView2 150.0.4078.65；bundled Python 来源为官方 CPython 3.12.8 Windows embeddable amd64 release workflow
范围门禁：只修改 7 个 Markdown；没有 main.py、app/、desktop/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/ 或 tests/ 变化；没有真实 data/ 变化
P0/P1：未确认；没有数据污染、凭据泄露、范围扩张、悬空引用或架构根冲突
已知限制：所有技术方案仍需在归属 WP 真实验证；WP-1A-01 只是准备完整，状态继续为 planned；Tauri CLI 当前未安装且不是 WP-1A-01 前置
回退步骤：整体 revert 本 WP 提交；只回退审查文档和状态记录，不触碰生产代码、旧迁移分支或真实 data/
关联提交：本 WP accepted 提交
```

验收记录：

```text
状态：accepted
自动测试：.\docs\runtime-v2\baselines\run_wp_0_02_baseline.ps1 连续三次退出码 0；每轮 4 passed；Python 辅助脚本 py_compile 通过
故障测试：7/7 场景通过；fixture 30 个文件，tree SHA-256 6c7b34e2f6af7dfce4d0a69a756499e552fea87943902782d383ef6df78ea8ff，执行前后完全一致
真实应用验收：本 WP 按范围不启动 Qt/Tauri；Phase 1A named mutex 与 Phase 3 真实 Qt→Tauri→Qt 步骤、提示、失败和只读结果已成为 ADR-0003 可执行输入
数据门禁：真实 data/ 121 个文件；三次完整脚本均证明 path/length/UTC mtime/SHA-256 完全一致，最终摘要 63d79065372c9943e9de12065dcf6df14eef14447fe2bc56fd43587e533ee6cf
核心契约：共享数据只在 config_version=4 且结构有效时允许批准的兼容写；Phase 3 当前只批准 history JSONL；v2 私有配置位于 data/runtime_v2/；Qt/Tauri 共用 Local\SakuraDesktop.SharedUserData.v1
已知限制：真实双入口锁尚未实现/验证；installed/legacy 多数格式无独立 version；Qdrant、插件私有数据、TTS 和 logs/diagnostics 仍需后续领域门禁；best-effort .bak 不可作为 mandatory migration backup
P0/P1：未确认；没有数据污染或范围扩张
回退步骤：整体 revert 本 WP 提交；不删除、恢复或改写真实 data/、characters/、Memory/Qdrant、插件数据、migration backup 或锁文件
关联提交：5e6cf364e（docs(runtime): 建立用户数据与共享应用锁契约）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-15
允许目录：docs/runtime-v2/baselines/；仅允许在本文更新 WP-0-03 状态与验收记录
明确禁止目录：main.py；app/；desktop/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；旧迁移分支代码；WP-0-04、Phase 1A 及后续生产实现
验收环境：当前 Windows 开发机；只使用 git cat-file、ls-tree、diff-tree、show 等命令读取固定 commit 190dfafd24f5c5226bff8b4347837b6e45d9a331；不安装依赖、不创建或编译 Tauri、不启动 legacy Qt/Tauri、不调用外部服务、不读取真实用户私有内容
关联 ADR：ADR-0001（进程监管与故障矩阵）；ADR-0002（IPC、Envelope、generation、Snapshot）；ADR-0003（应用锁与数据兼容）；治理 G-007
计划提交：docs(runtime): 建立旧迁移逐文件复用准入清单
回退命令：git revert <WP-0-03-commit>；只回退审查文档和状态记录，不触碰旧迁移来源、生产代码或真实 data/
```

稳定化记录：

```text
状态：stabilizing
自动测试：固定 commit 对象、local/remote 固定分支引用和共同基线均已确认；R01–R67 连续且唯一；分类计数 6/34/17/7/3；40 个准入/条件准入项均绑定唯一具体 WP；WP-1A-01 至 WP-3-06 共 27 项全部登记；96 个引用路径经 git cat-file -e 全部存在
故障测试：151 个迁移差异路径按 app 45、desktop 53、tests 28、docs 7、.github 4、plugins 2、根文件 12 完整覆盖；同步 Supervisor、根进程 kill、stdout 污染、旧 generation、late watcher、源码字符串门禁和数据回滚风险已转为文档/测试输入
真实应用验收：本 WP 按范围不启动 legacy Qt/Tauri、不创建或编译 Tauri、不运行旧生产实现；窗口、进程、IPC、聊天和数据真实验收绑定各未来 Work Package
数据门禁：未读取真实 API Key、Token、聊天、Memory、notes 或插件私有内容；未修改生产目录或真实 data/
已知问题：准入不等于技术门通过；旧迁移没有可准入的 Windows Job Object、并发 Router、revisioned Snapshot、真实 shared named mutex 或 Qt→Tauri→Qt 门禁实现
回退步骤：整体 revert 本 WP 提交；不得 checkout、restore、merge、cherry-pick 或修改旧迁移分支及真实 data/
关联提交：待提交
```

验收记录：

```text
状态：accepted
自动测试：R01–R67 共 67 项连续且唯一；准入 6、有条件准入 34、拒绝 17、延后 7、无归属删除 3；40 个复用项全部绑定唯一具体 Work Package；WP-1A-01 至 WP-3-06 共 27 项无悬空；96 个引用路径在固定 commit 中全部存在；151 个迁移差异路径完整覆盖
故障测试：被拒绝实现中的进程泄漏、同步阻塞、shutdown/retry 竞态、旧 generation、late watcher、IPC 损坏、裸路径资源、设置回滚和数据兼容风险已单独保留为文档或测试输入；没有复制被拒绝生产结构
真实应用验收：本 WP 为只读取证和文档准入，不启动 Qt/Tauri、不创建或编译 Tauri；所有真实窗口、进程、IPC、聊天与 Qt→Tauri→Qt 验收均绑定具体未来 WP
范围门禁：只修改本 Work Package 基线文档和本文状态记录；没有修改 main.py、app/、desktop/、plugins/、data/、runtime/、characters/、third_party/ 或 tools/mcp/
数据门禁：没有读取或提交真实 API Key、Token、聊天、Memory、notes 或插件私有数据；没有真实 data/ 变化
P0/P1：未确认；没有数据污染或范围扩张
已知限制：准入项仍需在归属 WP 逐文件重新读取和技术验证；旧迁移没有可准入的 Job Object、并发 Router、revisioned Snapshot、shared named mutex 或真实双向数据门禁实现
回退步骤：整体 revert 本 WP 提交；不得恢复旧迁移目录、stash 或生产实现，不触碰真实 data/
关联提交：239f495ad4c0b324c6b6e340bc155ab23997f7e9（docs(runtime): 建立旧迁移逐文件复用准入清单）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-16
允许目录：docs/runtime-v2/baselines/WP-0-04-architecture-review.md；docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md；仅在关闭实际冲突时窄改 docs/superpowers/plans/2026-07-14-tauri-python-core-v2.md、docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md 和 docs/adr/0001-runtime-v2-process-supervision.md、0002-runtime-v2-ipc.md、0003-runtime-v2-data-compatibility.md
明确禁止目录：main.py；app/；desktop/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；tests/；旧迁移分支代码；WP-1A-01 或任何后续生产实现
验收环境：当前 Windows 开发机；只读检查 Git 提交、引用和 Markdown；不安装依赖、不创建/编译/运行 Tauri、不启动 legacy Qt、不调用外部服务、不读取真实用户私有内容
关联 ADR：ADR-0001、ADR-0002、ADR-0003；三份 ADR 在本 WP 只能认可为 Proposed 技术基线
计划提交：docs(runtime): 完成 Runtime v2 架构审查收口
回退命令：git revert <WP-0-04-commit>；只回退审查文档和状态记录，不触碰生产代码或真实 data/
```

稳定化记录：

```text
状态：stabilizing
自动测试：三份 ADR 状态均为 Proposed；WP-0-01/02/03 accepted 与提交 c555e1b95、5e6cf364e、239f495ad4c0b324c6b6e340bc155ab23997f7e9 已核对；当前文档引用路径检查全部存在
故障测试：单窗口失败停止/替代/批准路径、lifecycle deadline、不可自动重试分类、legacy Qt 回退和数据责任均已形成可执行审查输入；一致性矩阵 12/12 项有结论和后续 WP
真实应用验收：本 WP 只做架构审查，不启动 legacy Qt/Tauri，不创建、编译或运行 Tauri；WP-1A-01 真实 Shell 验收步骤已准备但未执行
范围门禁：仅修改 WP-0-04 审查文档、主计划、治理、三份 ADR 和 Work Package 清单；没有生产目录、tests/ 或真实 data/ 变化
已知问题：工具链、窗口、进程树、IPC 和数据兼容仍需各归属 WP 真实技术验证；这不改变三份 ADR 的 Proposed 状态
回退步骤：整体 revert 本 WP 提交；不触碰生产代码、旧迁移分支或真实 data/
关联提交：待提交
```

Accepted 记录（补录历史真相源）：

```text
状态：accepted
验收日期：2026-07-16
关联提交：9f1e71f61（docs(runtime): 完成 Runtime v2 架构审查收口）
结果：三份 ADR 作为 Proposed 技术基线通过审查，WP-1A-01 获准激活；提交未包含生产代码或真实 data/ 变化
历史范围说明：本记录只证明当时 Windows-first 计划完成内部一致性审查。2026-07-22 新增 ADR-0004/Phase 1P 修正正式目标平台，不撤销本提交，也不把本记录当成跨平台技术证据
回退步骤：git revert 9f1e71f61；只回退当时的架构审查文档，不回退后续已验证实现
```

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

激活记录：

```text
状态：active
开始日期：2026-07-16
允许目录：新建 desktop/ 下的最小 Tauri crate、静态 startup 页面、Shell 自身测试与 desktop/rust-toolchain.toml；.gitignore 仅限新增 desktop 构建产物规则；本文仅在实际激活时更新 WP-1A-01 状态和验收记录
明确禁止目录：main.py；app/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；现有 tools/settings-tauri/ 与 tools/studio-tauri/；legacy Qt 入口和默认入口脚本；WP-1A-02 及后续实现
验收环境：Windows 11 23H2 build 22631.4890 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3、tauri-build 2.6.3；WebView2 150.0.4078.65；Visual Studio 18.4.1 C++ 工具链与 Windows SDK 10.0.26100.0；Node/npm 非必需；Tauri CLI 非本 WP 前置
关联 ADR：ADR-0001（Tauri 生命周期根和退出所有权）；ADR-0002（本 WP 不建立 transport）；ADR-0003（不得写共享 data/）；三份 ADR 均继续为 Proposed
自动测试要求：cargo fmt --check；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked；cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked；cargo build --manifest-path desktop/src-tauri/Cargo.toml --release --locked；可执行冒烟必须断言 startup 页面可见和退出后无 Shell 残留
真实 Shell 验收步骤：分别启动 debug 与 release Shell；确认 startup 页面立即可见；关闭窗口并确认进程退出；在隔离工作目录中不提供 runtime/、Python、用户 data/ 或 Sakura 环境变量，重复启动、显示和退出
故障测试：Python 路径缺失、runtime/ 缺失、无关环境变量缺失、工作目录无 data/、startup 静态资源缺失的构建期失败；运行期不得出现空白窗口、Python spawn、共享 data/ 写入或后台任务残留
独立回退方式：整体 revert WP-1A-01 提交，删除新增 desktop/ 最小 Shell 与专用工具链文件；Qt main.py、start.bat 和当前产品入口保持不变
计划提交：feat(runtime): 建立不启动 Python 的最小 Tauri Shell
```

稳定化记录：

```text
状态：stabilizing
自动测试：固定 Rust/Cargo 1.96.0 下 cargo fmt --check 通过；cargo test --locked 为 2 passed；debug/release cargo build --locked 均成功；移除 frontend 的隔离构建以 include_str! 和 frontendDist 明确错误退出 101
故障测试：在系统临时目录创建仅含 debug/release EXE 的隔离布局，确认不存在 runtime/ 和 data/；清除已有 Conda/Python/Sakura 相关变量并把 PYTHONHOME、PYTHONPATH、SAKURA_PYTHON、SAKURA_RUNTIME_DIR、SAKURA_DATA_DIR 指向不存在路径后，两种构建仍显示并正常退出
真实应用验收：debug、release 及两种隔离副本均出现 656x459 普通 Tauri/WebView2 窗口；startup 页面文字和样式真实可见；关闭返回退出码 0；运行期后代仅有 WebView2，关闭后约 0.2 秒内清空
数据与进程门禁：四次最终真实验收均无 Python 后代；真实 data/ 121 个文件的 path/length/UTC mtime/SHA-256 canonical manifest 前后均为 a1317eb594ef3eabd485bd9638126d11a14a09b62c27878bb557e0a5de1917ff，零变化
范围门禁：只新增 desktop/ 最小 crate、静态页面、Windows 强制构建图标和专用工具链，并更新本文；未修改默认入口、legacy Qt、生产 Python、共享 data/ 或 WP-1A-02+；直接依赖只有 tauri 2.11.3 和 tauri-build 2.6.3，无 Tauri plugin
稳定化修复：首次 debug 验收后把 Windows 二进制固定为 GUI subsystem，移除调试构建的控制台宿主；验收脚本对 WebView UI Automation 控件类型的过窄筛选已按真实树修正，不属于应用缺陷
已知问题：本 WP 不生成安装包、不安装 Tauri CLI、不验证透明窗口/DPI/IME/托盘/IPC/Supervisor；运行仍依赖目标 Windows 已安装 WebView2 Runtime；当前未确认 P0/P1
回退步骤：整体 revert WP-1A-01 提交，删除新增 desktop/ 最小 Shell；main.py、start.bat、legacy Qt、Python Runtime 和真实 data/ 保持不变
关联提交：待提交
```

验收记录：

```text
状态：accepted
自动测试：stabilizing 中重复 cargo fmt --manifest-path src-tauri/Cargo.toml --check、cargo test --manifest-path src-tauri/Cargo.toml --locked、debug/release cargo build --locked，全部退出码 0；Rust 单元测试 2 passed；两轮缺失 frontend 探针均退出 101 并同时给出 include_str! 与 frontendDist 明确错误
故障测试：两轮系统临时隔离布局均只含 debug/release EXE，不含 runtime/、Python 或 data/；清除已存在的 Conda/Python/Sakura 相关变量并覆盖不存在的 PYTHONHOME、PYTHONPATH、SAKURA_PYTHON、SAKURA_RUNTIME_DIR、SAKURA_DATA_DIR 后，debug/release 均显示并正常退出，隔离布局未新增文件
真实应用验收：最终有效 debug/release 正常与隔离验收共 8 次；均出现 656x459 普通 Tauri/WebView2 窗口，Sakura Runtime v2 / Startup、WebView 已加载和无 Python/用户数据说明真实可见；关闭窗口后根进程退出码 0
数据与进程门禁：运行期后代仅有 WebView2，未启动 Python；关闭后 WebView2 后代约 0.2 秒内清空，最终无 Shell/后代残留；真实 data/ 121 个文件的 path/length/UTC mtime/SHA-256 canonical manifest 在两轮门禁前后均为 a1317eb594ef3eabd485bd9638126d11a14a09b62c27878bb557e0a5de1917ff，零变化
工具链与平台：Windows 11 23H2 build 22631.4890 x64；Rust/Cargo 1.96.0；target x86_64-pc-windows-msvc；Tauri 2.11.3；tauri-build 2.6.3；WebView2 150.0.4078.65；MSVC tools 14.50.35717；Windows SDK 10.0.26100.0；Tauri CLI 和 Node/npm 均未使用
权限与范围：单个普通非透明窗口；空 capability permissions；CSP 仅允许同源 CSS，其余脚本、连接、图片、字体、媒体、frame、worker 和表单默认拒绝；直接依赖只有 tauri/tauri-build，无激活的 tray 或 Tauri plugin；Windows 强制要求的 32x32 ICO 仅作为构建资源
范围门禁：只新增 desktop/ 最小 Shell 和更新本文；没有 main.py、start.bat、app/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、Settings/Studio、默认入口或 legacy Qt 变化；没有 Supervisor、IPC、聊天、设置、托盘、角色加载、共享锁或 WP-1A-02 实现
P0/P1：未确认；没有数据污染、凭据泄露、崩溃、无法退出、进程泄漏、范围扩张或不可独立回退改动
已知限制：本 WP 不生成安装包、不验证干净 Windows 安装，不包含透明窗口、DPI、多屏、IME、焦点、托盘、应用锁、Core 或产品功能；运行需要目标 Windows 已安装 WebView2 Runtime
回退步骤：整体 revert 本 WP accepted 提交，删除新增 desktop/ 最小 Shell 和专用工具链；当前 main.py、start.bat、legacy Qt、Python Runtime、角色、插件和真实 data/ 行为保持不变
关联提交：本 WP accepted 提交（feat(runtime): 建立不启动 Python 的最小 Tauri Shell）
```

主要结果：Runtime v2 拥有一个不依赖 Python、可以立即显示、诊断启动失败并正常退出的最小桌面根。

最小结果上限：

- 不启动 Python 的最小 Tauri Shell。
- startup 页面可见。
- Python 缺失时仍可显示并退出。
- 不包含 Supervisor、IPC、聊天、设置、托盘、角色加载或默认入口切换。

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

激活记录：

```text
状态：active
开始日期：2026-07-20
允许目录：desktop/frontend/ 下的四状态纯布局、最小透明表现层和 Node 可执行测试；desktop/src-tauri/ 下的单窗口几何、物理/逻辑坐标换算、显示器工作区选择、原子窗口布局应用、Rust 测试和构建配置；desktop/tests/ 下仅限 WP-1A-02 Windows 真实窗口验收脚本；本文仅更新 WP-1A-02 状态与验收记录
明确禁止目录：main.py；start.bat；app/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；现有 tools/settings-tauri/ 与 tools/studio-tauri/；legacy Qt 和默认入口；WP-1A-03 及后续生产实现；data/runtime_v2/
验收环境：当前 Windows 11 23H2 build 22631.4890 x64 开发机；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3、tauri-build 2.6.3；WebView2 150.0.4078.65；Visual Studio 18.4.1 C++ 工具链与 Windows SDK 10.0.26100.0；Node v22.14.0；本机真实显示器/DPI 能力按执行时取证，缺失物理组合以确定性自动测试补足并明确记录
关联 ADR：ADR-0001（Tauri 生命周期根与退出门禁）；ADR-0002（本 WP 不建立 IPC）；ADR-0003（不得读写共享 data/ 或 data/runtime_v2/）；三份 ADR 均继续为 Proposed
自动测试要求：cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked；node --test desktop/frontend/tests/*.test.js；debug/release cargo build --locked；覆盖四状态、固定锚点、单/多屏、负坐标、100%/125%/150% DPI、边缘/超大工作区、长文本、极端尺寸、快速切换/晚到结果和 Rust/前端共享契约
真实窗口验收：分别启动 debug/release 透明 Tauri/WebView 窗口；切换 idle/bubble/composer/expanded 并核对物理立绘锚点；验证显示/隐藏/展开/收起无明显白闪或布局抖动；记录本机真实显示器坐标与 DPI；关闭后核对 Shell/WebView 后代清空、无 Python 后代；真实 data/ canonical manifest 前后零变化
故障测试：目标工作区负坐标；窗口贴近各边缘；期望尺寸大于工作区；零/极端/长文本输入；快速连续状态切换；旧 revision 晚到；目标显示器变化；共享布局契约损坏时安全失败
独立回退方式：整体 revert WP-1A-02 accepted 提交，恢复 WP-1A-01 的普通 startup Shell；不修改或清理 legacy Qt、Python Runtime、真实 data/ 或用户资源
计划提交：feat(runtime): 建立透明桌宠窗口几何与锚点模型
```

稳定化记录：

```text
状态：stabilizing
自动测试：固定 Rust/Cargo 1.96.0 下 cargo fmt --check 通过；cargo test --locked 为 10 passed；Node 内置测试为 7 passed；debug cargo build --locked 成功；release locked build 与最终重复门禁待执行
故障测试：四状态尺寸/矩形/共享契约、固定物理锚点、单/多屏、负坐标、100%/125%/150% DPI、工作区四边、超小工作区统一缩放、非法/超大契约、长/极端文本、快速切换、晚到/重复 revision 均已有可执行测试并通过
真实应用验收：debug 透明 Tauri/WebView 窗口真实显示；idle/bubble/composer/expanded 为 320x420、736x500、736x592、816x680；四态物理立绘锚点均为 (2224,1380)；隐藏后 220ms 内重新显示；截图确认气泡和输入区居中覆盖占位立绘且无右下角缺口；release 待验收
DPI/多屏：本机只有 DISPLAY1 单屏 2560x1440、工作区 2560x1392、96 DPI/100%；125%/150%、多屏和负坐标缺少真实物理环境，当前由确定性 Rust 测试补足，不虚报真实通过
数据与进程门禁：debug 运行期后代只有 6 个 msedgewebview2.exe；无 Python 后代；关闭后 0.5 秒复查无本次 Shell/WebView 后代；真实 data/ canonical manifest 前后均为 7d877f22c2dc579ed1ecd924728e26d7a6395f2607a5355be00b6added74266d
已知问题：release 与最终重复验收尚未执行；本机缺少真实多屏、负坐标和 125%/150% DPI；当前按钮只是 WP-1A-02 技术门，不代表 WP-1A-03 的点击穿透、拖动、焦点或 IME 通过
回退步骤：整体 revert WP-1A-02 accepted 提交，恢复 WP-1A-01 普通 startup Shell；不触碰 main.py、legacy Qt、Python Runtime、真实 data/ 或用户资源
关联提交：待 accepted 后提交
```

验收记录：

```text
状态：accepted
自动测试：cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check 退出码 0；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked 为 10 passed；node --test desktop/frontend/tests/*.test.js 为 7 passed；debug/release cargo build --locked 均退出码 0；git diff --check 和 Windows 验收脚本 PowerShell 语法解析均通过
故障测试：共享 JSON 契约由 Rust 与前端共同执行；覆盖四状态尺寸和矩形边界、逻辑/物理坐标、固定锚点、单/多屏选择、副屏负坐标、100%/125%/150% DPI、工作区四边、期望窗口大于工作区时统一缩放、空工作区/非法 DPI、损坏/超大契约、10 万字符与极端文本、快速连续切换、旧/重复 revision 和晚到布局结果；全部通过
真实应用验收：debug 与 release 均实际显示单个透明 Tauri/WebView 窗口；idle/bubble/composer/expanded 原生物理尺寸依次为 320x420、736x500、736x592、816x680；四态物理立绘锚点均为 (2224,1380)；气泡居中覆盖占位立绘，输入区与气泡同中心线，右下角无缺口；状态展开/收起、隐藏 220ms 后显示均无明显白闪或布局抖动；窗口关闭后根进程退出码 0
DPI/多屏证据：真实环境仅 DISPLAY1 单屏 2560x1440、工作区 2560x1392、96 DPI/100%；本机没有可用的真实多屏、负坐标或 125%/150% DPI 环境，因此这些物理证据明确缺失，以确定性 Rust 测试补足，不记录为真实通过
工具链与平台：Windows 11 23H2 build 22631.4890 x64；rustc/cargo 1.96.0；Tauri 2.11.3；tauri-build 2.6.3；WebView2 150.0.4078.65；Node v22.14.0
数据与进程门禁：debug/release 每轮运行期后代仅为 6 个 msedgewebview2.exe，Python 后代为 0；关闭后 0.5 秒复查本轮 Shell/WebView 后代为 0；真实 data/ 的 path/length/UTC mtime/SHA-256 canonical manifest 每轮前后均为 7d877f22c2dc579ed1ecd924728e26d7a6395f2607a5355be00b6added74266d，零变化；未创建或写入 data/runtime_v2/
范围门禁：只修改 desktop/frontend/、desktop/src-tauri/、desktop/tests/ 和本文；没有修改 main.py、start.bat、app/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、legacy Qt、默认入口或 WP-1A-03+；没有 Python Core、Supervisor、Fake Core、IPC、聊天、真实角色业务、点击穿透、拖动、焦点或 IME 平台实现
P0/P1：零；退出条件相关缺陷为零；用户指出的控制面板错位和气泡右下角缺口已在 stabilizing 中按参考图修复并经 debug/release 截图复核
已知限制：立绘是本 WP 明确范围内的 CSS 占位图，不读取真实角色资源；真实多屏、负坐标、125%/150% DPI 和干净机证据仍缺失；状态按钮与 220ms visibility probe 仅用于技术门，不代表 WP-1A-03 输入、焦点、点击穿透或 IME 验收
回退步骤：整体 revert 本 WP accepted 提交，恢复 WP-1A-01 的 656x459 普通 startup Shell；不触碰 main.py、legacy Qt、Python Runtime、真实 data/ 或用户资源
关联提交：本 WP accepted 提交（feat(runtime): 建立透明桌宠窗口几何与锚点模型）
```

重新稳定化记录：

```text
状态：stabilizing
重新开始日期：2026-07-20
触发原因：用户真实验收确认 idle/bubble/composer/expanded 每一档切换都会闪一下；前端把只应覆盖首帧加载的整窗 opacity=0 错误应用到每次状态切换，属于“展开和收起无明显白闪”退出条件缺陷
允许修复范围：仅调整 desktop/frontend/ 的布局提交时序、首帧可见性样式和对应可执行测试；desktop/tests/ 验收脚本可增加连续帧/切换可见性证据；本文更新重新稳定化与最终验收记录
明确禁止：不得借修复进入 WP-1A-03；不实现点击穿透、拖动、焦点、输入命中或 IME；不修改 Rust 几何契约、Python、legacy Qt、默认入口或用户数据
修复门禁：状态切换期间 body/stage 必须持续可见；Win32 原生窗口 bounds 一次更新后立即提交 DOM 布局；旧/晚到结果不得回滚新布局；debug/release 真实连续切换无可见灭帧、白闪或锚点漂移
回退步骤：revert 本次闪烁修复提交可回到 7065859084c9e630d34e173c09af9948786337e1；若修复无法通过真实门禁，则 WP 保持 stabilizing，不开始 WP-1A-03
```

重新验收记录：

```text
状态：accepted
根因与修复：每次状态切换都把 body 从 opacity=1 切到 opacity=0 并执行 90ms 过渡，造成稳定可见的整窗闪烁；现仅在首次 pet-geometry-loading 阶段保持透明，四态切换不再修改 body/stage 可见性；舍弃会在旧原生边界中提前绘制新 DOM 的方案，最终使用 Win32 一次更新原生 bounds 后立即提交 DOM 布局
自动测试：cargo fmt --check 通过；Rust 10 passed；前端 8 passed，新增“原生 bounds 先于 DOM commit”时序测试；debug/release cargo build --locked 均通过；PowerShell 验收脚本语法解析通过
真实闪烁门禁：验收脚本先在窗口隐藏期间采集桌面背景，再从立绘候选点选择与背景差异最大的固定物理像素，连续切换四态并以约 5ms 间隔采样 140ms；若采样接近背景则按透明/空白帧失败
debug 结果：正常可见像素距背景 101277；四态切换期间最小距离 96644；没有透明/空白帧；四态锚点均为 (2224,1380)
release 结果：正常可见像素距背景 103906；四态切换期间最小距离 99213；没有透明/空白帧；四态锚点均为 (2224,1380)
数据与进程门禁：debug/release 真实 data/ canonical manifest 前后均为 eb5f789b502eb2275fddcf9655caa5685803a785c14586540ddc10dd0fae4c9a；Python 后代为 0；关闭后本轮 Shell/WebView 后代为 0；根进程退出码 0
P0/P1：零；重新稳定化触发的状态切换闪烁缺陷已清零；本次没有开始 WP-1A-03
回退步骤：revert 本次闪烁修复提交会回到 7065859084c9e630d34e173c09af9948786337e1，并使 WP-1A-02 重新处于存在已知退出条件缺陷的 stabilizing 状态；不得在该状态开始 WP-1A-03
关联提交：本次闪烁修复提交（fix(runtime): 消除桌宠状态切换闪烁）
```

第二次重新稳定化记录：

```text
状态：stabilizing
重新开始日期：2026-07-20
触发证据：用户提供三组三帧慢放截图，明确显示状态切换期间先更新原生窗口 bounds、WebView 仍绘制旧布局，随后 DOM 才更新；立绘在中间帧发生明显水平/垂直位移后归位
前次门禁缺口：固定立绘像素探针只采样单个底部点，旧布局在新窗口中的裁切仍可能覆盖该点，因此 debug/release 的单点距离证据不能证明整幅立绘未移动；2834a16a99bf8b3ae11a416203f698d84fb3c837 不再视为退出条件最终证据
修复方向：状态切换前只登记待布局，不立即绘制；原生窗口引发 WebView viewport resize 时，由 ResizeObserver 在下一帧绘制前提交待布局；Tauri Promise 返回只确认 revision/结果和最终状态，不再承担首次 DOM 几何更新
修复门禁：必须复现并消除用户截图中的“新原生边界 + 旧 DOM”中间帧；真实 debug/release 连续切换需使用多个立绘采样点或整块截图差分，不能再以单点通过作为无位移结论
明确禁止：WP 继续 stabilizing；不得开始 WP-1A-03；不引入第二原生窗口、隐藏 Qt、焦点/命中/IME 平台能力或用户数据写入
```

最终重新验收记录：

```text
状态：accepted
最终根因：用户三组三帧慢放证明动态移动/缩放 HWND 与 WebView DOM 布局无法在同一个合成帧原子提交；原生窗口先到新 bounds 时，DWM 会短暂展示新位置中的旧 WebView 表面，造成整幅立绘位移；单点像素门禁没有覆盖该空间位移
失败方案结论：整窗 opacity 过渡会产生明显灭帧；DOM 提前提交会在旧 HWND 位置绘制新布局；原生 bounds 先提交会在新 HWND 位置绘制旧布局；ResizeObserver 仍无法阻止 DWM 先合成旧表面；以上方案均不作为最终实现
最终实现：单透明 HWND 使用固定 816x680 逻辑包络和固定 viewport portraitAnchor=(480,668)；四态继续输出 320x420、736x500、736x592、816x680 的逻辑活动尺寸和向上/向左 activeOffset，但原生窗口 placement、WebView viewport 和立绘本地矩形在四态间完全不变；状态切换只改变包络内气泡、输入区和技术门布局
自动测试：cargo fmt --check 通过；Rust 10 passed，并断言三档 DPI 下四态 physicalPlacement 完全一致；前端 8 passed，并断言四态逻辑尺寸保留、native viewport 恒定、portraitRect/portraitAnchor 恒定、activeOffset 向上/向左展开；debug/release cargo build --locked 均通过；PowerShell 验收脚本语法解析通过
真实应用验收：用户按原慢放方式手工复测确认没有问题；debug/release 四态原生窗口均为 (1744,712,816x680)，逻辑活动尺寸依次为 320x420、736x500、736x592、816x680，四态物理锚点均为 (2224,1380)
闪烁探针：debug/release 正常可见像素距隐藏背景均为 121509，连续四态切换期间最小距离均为 116430，没有透明/空白帧；逐态截图与用户慢放结论一致
数据与进程门禁：debug/release 真实 data/ canonical manifest 前后均为 eb5f789b502eb2275fddcf9655caa5685803a785c14586540ddc10dd0fae4c9a；Python 后代为 0；关闭后本轮 Shell/WebView 后代为 0；根进程退出码 0
已知限制与风险：固定透明包络大于 idle 逻辑活动区，透明空白区的点击穿透与交互区命中必须由 WP-1A-03 真实验证；本 WP 不提前实现命中、拖动、焦点或 IME；真实物理环境仍只有单屏 100% DPI
P0/P1：零；用户报告的状态切换闪烁和整幅立绘中间帧位移均已清零；本次没有开始 WP-1A-03
回退步骤：revert 最终固定包络修复提交会回到 2834a16a99bf8b3ae11a416203f698d84fb3c837，但会重新引入用户慢放确认的立绘位移；回退后 WP 必须重新标记 stabilizing，且不得开始 WP-1A-03
关联提交：最终固定包络修复提交（fix(runtime): 固定透明窗口包络消除立绘位移）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-20
允许目录：desktop/frontend/ 下的共享命中区域纯逻辑、真实输入控件、IME/focus 状态机和 Node 可执行测试；desktop/src-tauri/ 下的共享命中几何、Win32 HWND 区域、拖动后锚点/DPI/工作区修正、Rust 测试和构建配置；desktop/tests/ 下仅限 WP-1A-03 Windows 真实交互验收脚本；docs/superpowers/plans/2026-07-20-wp-1a-03-hit-drag-focus.md；本文仅更新 WP-1A-03 状态与验收记录
明确禁止目录：main.py；start.bat；app/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；现有 tools/settings-tauri/ 与 tools/studio-tauri/；legacy Qt 和默认入口；data/runtime_v2/；WP-1A-04 及后续生产实现
验收环境：当前 Windows 11 23H2 build 22631.4890 x64 开发机；单屏 2560x1440、工作区 2560x1392、100% DPI；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3、tauri-build 2.6.3；WebView2 150.0.4078.65；Visual Studio 18.4.1 C++ 工具链与 Windows SDK 10.0.26100.0；Node v22.14.0；真实多屏、负坐标、125% 和 150% DPI 如本机不可用则以确定性自动测试补足并明确记录为缺失物理证据
关联 ADR：ADR-0001（Tauri 生命周期根与退出门禁）；ADR-0002（本 WP 不建立 Core IPC，Tauri command 仅承载窗口技术门）；ADR-0003（不得读写共享 data/ 或 data/runtime_v2/）；三份 ADR 均继续为 Proposed
自动测试要求：cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked；node --test desktop/frontend/tests/*.test.js；debug/release cargo build --locked；覆盖四状态命中模型、边界/优先级、拖动锚点、单/多屏、负坐标、100%/125%/150% DPI、边缘/极端工作区、快速切换/晚到结果、IME/focus 状态机、共享 Rust/前端契约和平台失败安全恢复
真实窗口验收：分别启动 debug/release 单透明 Tauri/WebView 窗口；验证透明空白点击穿透、立绘/气泡/状态控件/input/button 不穿透、立绘与气泡正文拖动、拖动后四态锚点固定、英文和中文 IME、候选窗位置、Alt+Tab、hide/show、状态往返恢复输入、无白闪/布局抖动/立绘漂移；关闭后核对 Shell/WebView 后代清空、无 Python 后代；真实 data/ canonical manifest 前后零变化
故障测试：命中边界与重叠；interactive 优先于 drag；输入/按钮/状态控件禁止拖动；旧 revision 晚到；命中平台设置失败后恢复整窗交互；拖动跨屏/DPI/工作区边缘；窗口包络大于工作区；极端坐标；composition 中焦点/状态变化和提交抑制
独立回退方式：整体 revert WP-1A-03 accepted 提交，移除命中/拖动/输入焦点平台代码、真实输入控件和验收脚本，恢复 WP-1A-02 的固定透明包络与四状态静态布局；不得触碰默认入口、legacy Qt、Python Runtime 或真实 data/
计划提交：feat(runtime): 建立透明窗口命中、拖动与输入焦点技术门
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-20
生产实现：固定 816x680 逻辑包络保持不变；共享 layout contract 新增四态 controlsRect；前端纯模型按 interactive > drag > neutral > transparent 分类；Rust 使用相同 contract 转换到窗口物理坐标并以 Win32 HWND region 实现空白区穿透，平台设置失败时清除 region 恢复整窗交互；立绘与气泡正文使用等待鼠标释放的 Win32 move loop，完成后按目标工作区/DPI重新计算并保存物理锚点；composer 使用真实 textarea、中文 composition 状态机和本地技术反馈
自动测试：前端 18 passed；Rust 17 passed；已覆盖四态命中输出、半开边界、interactive 优先、输入/控件不拖动、100%/125%/150% DPI、单/多屏、负坐标、极端坐标/工作区、拖动后四态锚点、快速状态结果、IME composition、Alt+Tab/hide-show/状态往返焦点恢复和平台失败安全恢复纯逻辑
旧迁移取证：只读固定 commit 190dfafd24f5c5226bff8b4347837b6e45d9a331 的 windows.rs 和 pet_controller.js；采用 start_dragging 调用经验、物理/逻辑换算思路、composition guard 和 revision 场景；拒绝 secondary windows、强制 always-on-top、整窗 set_ignore_cursor_events、旧 DesktopAppState/组合根、聊天/capture/settings 耦合
真实应用验收：待执行 debug/release Windows/WebView 物理门禁；完成前不得 accepted
已知问题：真实点击穿透、拖动、中文 IME 候选窗、Alt+Tab、hide/show、闪烁、进程和 data 门禁仍待 stabilizing 验证
回退步骤：整体 revert WP-1A-03 accepted 提交，恢复 WP-1A-02 固定透明包络和静态四状态布局；不得触碰默认入口、legacy Qt、Python Runtime 或真实 data/
关联提交：待 accepted 后提交
```

验收记录：

```text
状态：accepted
自动测试：cargo fmt --check 通过；Rust 17 passed；前端 Node 18 passed；PowerShell WP-1A-02/WP-1A-03 验收脚本语法解析通过；debug/release cargo build --locked 均成功；git diff --check 退出码 0
命中区域契约：四态由同一 layout-contract.json 输出 viewport 逻辑矩形；interactive=input/button/state controls，drag=portrait/visible bubble，neutral 当前为空，transparent 为固定 816x680 包络中三者并集的补集；Rust 以 scaleFactor*contentScale 向外取整成窗口物理坐标；Win32 SetWindowRgn 失败时清除 region 恢复整窗可交互/可关闭
拖动与锚点：用户报告的两项 stabilizing 缺陷均已清零；气泡正文和立绘均可拖动；根因取证证明 Tauri start_dragging 仅异步 PostMessage，旧实现过早保存拖前位置；最终改为等待 Win32 move loop 鼠标释放后捕获位置、选择目标显示器、修正工作区并更新物理锚点；debug/release 中气泡从 (1744,712) 移到 (1654,662)，立即切 composer 不回跳；立绘再移到 (1474,562)，四态锚点均为 (1954,1230)
真实点击穿透：debug/release 均以 WindowFromPoint/GetAncestor 证明固定包络透明点不属于 Sakura HWND，并以真实鼠标点击证明后方窗口被激活；portrait、bubble、controls、textarea 和 send 均由 Sakura HWND 接收；输入框、发送和状态控件模拟拖动后原生 bounds 零变化
真实输入/IME/focus：debug/release 均真实输入英文 focus；Alt+Tab 确认离开窗口后返回并追加 A；hide/show 后追加 H；idle→composer 后追加 S；截图依次证明 focus、focusA、focusAH、focusAHS；Microsoft Pinyin composition 候选“樱花”显示在真实输入光标下方且处于当前窗口/显示器内，空格后提交为 focusAHS樱花；composition 状态机测试证明 composition 中 Enter/button/失焦/切态不会产生本地提交，更不接入真实聊天
闪烁与布局回归：debug/release 四态原生 bounds 均固定 816x680；切换前后初始物理锚点均为 (2224,1380)；像素探针正常距离 80602、切态最小距离 76475，无透明/空白帧；拖动后四态锚点继续固定为 (1954,1230)，没有整幅立绘位移、白闪或布局抖动
真实平台范围：当前真实物理环境仅 1 个 2560x1440 显示器、工作区 2560x1392、100% DPI；没有真实多屏、负坐标、125% 或 150% DPI 证据，不把自动测试描述为物理验收
自动补足范围：Rust 确定性测试覆盖多屏选择、副屏负坐标、显示器间隙、100%/125%/150% DPI、工作区四边、窗口包络大于工作区、极端坐标、跨屏后锚点和拖动后四态；前端/Rust共同覆盖四态命中、半开边界、interactive 优先、快速状态结果和共享契约
数据与进程门禁：debug/release 真实 data/ canonical manifest 前后均为 eb5f789b502eb2275fddcf9655caa5685803a785c14586540ddc10dd0fae4c9a；运行期 Python 后代为 0；关闭后 Shell/WebView 后代为 0；根进程退出码 0
旧迁移取证：只读固定 commit 190dfafd24f5c5226bff8b4347837b6e45d9a331 的 desktop/src-tauri/src/windows.rs 与 desktop/frontend/pet/pet_controller.js；采用物理/逻辑换算、composition guard、revision 场景和平台调用经验；拒绝 secondary-window、强制 always-on-top、整窗 set_ignore_cursor_events、旧 DesktopAppState/组合根以及 chat/capture/settings 耦合；未 cherry-pick 或恢复旧迁移分支
明确非目标：没有 Python Core/Supervisor/Fake Core/IPC/聊天/Provider/角色业务；没有位置/草稿/焦点持久化；没有多窗口、托盘、设置、TTS、Tools、截图、吸边、磁吸或动画；没有修改默认入口、legacy Qt、main.py、start.bat、app/、plugins/、data/、runtime/ 或 characters/
已知限制：真实物理多屏、负坐标、125% 和 150% DPI 仍缺失；当前命中使用小型矩形语义区域，气泡圆角透明像素仍归气泡拖动区；首轮正式目标仍仅 Windows x64/WebView2
P0/P1：零；退出条件相关缺陷为零；单窗口方案在当前真实 Windows/WebView 环境成立
回退步骤：整体 revert 本 WP accepted 提交，移除命中/拖动/输入焦点平台代码、真实输入控件和验收脚本，恢复 WP-1A-02 固定透明包络和静态四状态布局；不触碰默认入口、legacy Qt、Python Runtime 或真实 data/
关联提交：本 WP accepted 提交（feat(runtime): 建立透明窗口命中、拖动与输入焦点技术门）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-20
允许目录：main.py、legacy_qt_main.py、start.bat、start-legacy-qt.bat；.gitattributes 中仅限上述两个 Windows batch 入口的精确 CRLF 规则；app/core/instance.py；与本 WP 直接相关的 tests/unit/、tests/integration/、tests/fixtures/runtime_v2/wp_1a_04/；desktop/src-tauri/ 中 shared mutex、入口冲突和 Shell 启动所需最小 Rust 代码/测试；desktop/tests/ 中 WP-1A-04 Windows 真实验收脚本；docs/adr/0003-runtime-v2-data-compatibility.md；本文仅更新 WP-1A-04 状态与验收记录
明确禁止目录：除 app/core/instance.py 外的 app/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；Settings/Studio；共享 schema；Python Core/Supervisor/Fake Core/IPC/聊天/TTS/Tools/MCP/Memory/插件/截图/主动互动；WP-1B 及后续生产实现
验收环境：当前 Windows 11 23H2 build 22631.4890 x64 开发机；单屏 2560x1440、工作区 2560x1392、100% DPI；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3、tauri-build 2.6.3；WebView2 150.0.4078.65；Visual Studio 18.4.1 C++ 工具链与 Windows SDK 10.0.26100.0；Node v22.14.0；项目 bundled runtime/python.exe；真实验收全部有 deadline，并核对根/后代/句柄/计时器无残留
关联 ADR：ADR-0003（共享用户数据、exact named mutex、legacy Qt 回退与 data 零变化门禁；完成后仅更新为 Technically Validated，不得 Accepted）；ADR-0001（Tauri 为唯一桌面生命周期根，默认入口不得由 Python 常驻托管）
自动测试要求：先观察 WP-1A-04 定向测试预期失败，再执行最小生产实现；cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked；node --test desktop/frontend/tests/*.test.js；相关 runtime/python.exe -m pytest；debug/release cargo build --locked；脚本语法/静态检查；git diff --check
真实验收要求：真实 Qt、真实 debug/release Tauri、默认入口和显式 legacy Qt 入口均有界运行；覆盖成功、双向冲突、mutex API fatal、正常释放、强杀释放、stale data/sakura.lock、重复执行和回退路径；每次涉及真实 data/ 前后记录 path/length/UTC mtime/SHA-256 全清单并证明零变化，且不得删除真实 lock/Qdrant lock
独立回退方式：整体 revert WP-1A-04 accepted 提交，恢复原 main.py 和 start.bat 的 legacy Qt 默认入口，移除 legacy_qt_main.py、start-legacy-qt.bat 及双方 named mutex 接入；不得删除或改写真实 data/、历史 data/sakura.lock 或 Qdrant lock
计划提交：feat(runtime): 建立共享应用锁与双入口回退
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-20
生产实现：app/core/instance.py 使用 CreateMutexW(initialOwner=TRUE) 获取 exact Local\SakuraDesktop.SharedUserData.v1，无任何文件系统/log I/O；legacy_qt_main.py 从激活时 main.py 演化并把锁前移到 crash log/selfcheck/default/version/migration/Assistant 前；Rust SharedInstanceGuard 在 Builder/WebView 前获取同一 mutex，冲突与 fatal 走原生提示；main.py 仅以 os.execv 替换为已构建 Tauri，start.bat 直接执行 Tauri，start-legacy-qt.bat 显式保留完整 Qt 回退
RED/GREEN：Python 首轮 1 failed（冻结 identity 缺失）后 3 passed；入口契约首轮 3 failed 后 7 passed；Rust shared mutex 首轮 1 passed/1 failed、扩展 fatal 后 1 passed/2 failed，最终全套 Rust 20 passed；并发运行 Python/Rust exact-name 测试曾真实触发 Win32 ERROR_INVALID_HANDLE，根因是 fatal 测试的同名 Event 跨进程重叠，已将 Rust 内同名内核对象测试串行并规定跨语言门禁顺序执行
待验收：cargo fmt/test、Node 当前测试、相关 Python pytest、debug/release locked build、脚本语法/静态检查、双入口成功/冲突/API fatal/正常释放/强杀释放/stale lock/重复执行/默认与回退入口，以及真实 data/ 全清单零变化
已知问题：真实 debug/release Tauri、隔离数据完整 Qt smoke、默认/回退脚本、进程/句柄残留与 data manifest 尚未完成，不得 accepted
回退步骤：整体 revert WP-1A-04 accepted 提交，恢复 WP-1A-03 时 main.py/start.bat 的 legacy Qt 默认入口，移除双端 shared mutex、legacy_qt_main.py 和 start-legacy-qt.bat；不得删除或改写真实 data/、历史 data/sakura.lock 或 Qdrant lock
关联提交：待 accepted 后提交
```

稳定化停止记录（2026-07-20）：真实验收 harness 按 systematic-debugging 已完成三次独立根因修复（StrictMode 空数组属性、受限环境拒绝 CIM、已退出 PID 空对象）；其后新的真实 Tauri `WM_CLOSE` 退出超时仍未满足有界退出门。按实施者门禁停止，不做第 4 次 harness 修复。WP 保持 stabilizing，不更新 ADR 状态、不写 accepted、不提交；失败轮安全审计确认真实 `data/` 121 文件、1,045,949,482 bytes、canonical SHA-256 `a6e1699dbf693c587d481f57e1956b420a2bf64262973908238ff8160aba42f2` 前后相同，Sakura Shell 与项目 runtime Python 残留均为 0；后续恢复、修复和验收记录继续按时间顺序追加于本文。

恢复执行停止记录（2026-07-20）：继续系统诊断后确认先前 `WM_CLOSE` 超时源于 harness 选中了 `Tao Thread Event Target` 而非真实 `Tauri Window`；后续还修正了进程级冲突对话框定位、Qt hold/deadline 边界、WebView 后代条件等待、Windows `os.execv` 新 PID 交接和仅凭 parent PID 产生的旧进程误判。最新真实轮 `acceptance-resumed-20260720-223519` 暴露不可接受的数据写入：隔离 Qt smoke 仍向真实 `data/logs/sakura-runtime.log` 写入启动/关闭日志，文件从 8,130,010 bytes、mtime `2026-07-20T14:33:54.7038291Z`、SHA-256 `d815f9587c24d740853d89b3360e11ee0ae309686212152c8b7bcf3baf59bb0` 变为 8,130,975 bytes、mtime `2026-07-20T14:35:32.3884607Z`、SHA-256 `9af00440d823f0034113f2ac59cac04340beda9b0668a03f1923e61952df9207`；全清单 121 文件不变、总长度增加 965 bytes、canonical SHA-256 从 `91a0497dcc01cbfce2f87679e25d7e466c29d5b6584d202d03dc944ce313f9e5` 变为 `929ae6111cf0f7100184127f6fa691c6ff60c706e6c4c1f417a4bf8ee4abcdb4`。命中数据污染强制停止条件；不恢复或清理真实日志，WP 保持 stabilizing，不更新 ADR、不提交、不启动 WP-1B-01。

批准隔离修复后的环境停止记录（2026-07-20）：TDD 源顺序测试先以缺少隔离重定向按预期失败，fixture 最小改为在导入 `legacy_qt_main` 前把 `_FILE_LOG_PATH` 指向临时根并禁止读取真实 debug 配置，定向入口测试 `5 passed`。首轮真实隔离 smoke `isolation-smoke-20260720-225400` 未进入 ready，20 秒后回收测试 Python；只读检查发现 debug Tauri PID 35580 已于 22:48:21 启动，早于本轮 smoke，且不是本轮测试进程，导致共享锁环境不满足独占验收前提。未擅自关闭该既有进程；真实 `data/` before/after 均为 121 文件、1,045,960,564 bytes、canonical SHA-256 `929ae6111cf0f7100184127f6fa691c6ff60c706e6c4c1f417a4bf8ee4abcdb4`，零变化；项目 runtime Python 残留 0。按物理环境/既有进程使真实验收证据不可靠的门禁，WP 保持 stabilizing 并停止，不提交、不进入 WP-1B-01。

获授权恢复后的最终停止记录（2026-07-20）：PID 35580 在受控关闭前已自行退出，核对环境为 Shell/Python 0 后，`isolation-smoke-20260720-225738` 真实通过：隔离日志 965 bytes，真实 `data/` 121 文件、1,045,960,564 bytes、canonical SHA-256 `929ae6111cf0f7100184127f6fa691c6ff60c706e6c4c1f417a4bf8ee4abcdb4` 前后相同，残留 0。自动门禁 fresh 结果为 cargo fmt 通过、Rust 20/20、Node 18/18、Python 8/8（后续 harness regression 后入口文件 6/6）、PowerShell/parser/py_compile 通过、debug/release locked build 通过。第一次完整矩阵 `acceptance-final-20260720-225853` 在默认入口发现到的 Tauri 退出后读取 `$null` ExitCode；最小 PowerShell 复现证明 `Get-Process` 对象没有 ExitCode，而 `Start-Process -PassThru` 对象为 0，安全 RED/GREEN 后仅对另有 launcher/batch 退出证据的发现进程跳过该字段。第二次完整矩阵 `acceptance-final-20260720-230141` 又在不同点失败：`start.bat` 外层 cmd PID 7336 在直接子 Tauri 被观察前退出。两轮均确认真实 data canonical SHA-256 零变化、Shell/Python 残留 0，但完整成功/故障/回退矩阵仍未通过；命中“自动测试或真实应用行为持续与契约不一致”停止条件，不再做第三次 harness 修复。WP 保持 stabilizing，不 accepted、不更新 ADR、不提交、不进入 WP-1B-01。

负责人调整门禁后的实机验收就绪记录（2026-07-20）：负责人明确授权 Agent 自主诊断并解决自动门禁问题，改为每个阶段代码与自动门禁完成后停在 stabilizing，由负责人执行真实实机验收。系统调试确认 `start.bat`/`start-legacy-qt.bat` 的裸 LF/混合换行会破坏 Windows cmd 解析；字节级 RED 后将两个入口固定为 CRLF，并在 `.gitattributes` 仅为这两个路径冻结 `text eol=crlf`。发现进程统一由 launcher/batch 验证返回码，窗口根验证退出与后代清理。最终自动矩阵 `acceptance-owner-ready-20260720-231244` 11/11 场景通过；真实 `data/` before/after 均为 121 文件、1,045,960,564 bytes、canonical SHA-256 `929ae6111cf0f7100184127f6fa691c6ff60c706e6c4c1f417a4bf8ee4abcdb4`；根进程残留 0。fresh 最终门禁：cargo fmt 通过、Rust 20/20、Node 18/18、Python 11/11、PowerShell parser/py_compile、debug/release locked build、git diff --check 全部通过；P0/P1 与退出条件相关自动缺陷为 0。当前只等待负责人按 Phase 1A 实机清单确认可见性、双向冲突、正常退出、强杀释放和显式 Qt 回退；确认前保持 stabilizing，不更新 ADR、不 accepted、不提交、不开始 WP-1B-01。

负责人首轮实机验收记录（2026-07-20）：负责人按 Phase 1A 清单完成默认 Tauri 可见/退出、显式 legacy Qt 回退、Qt→Tauri 与 Tauri→Qt 双向冲突、正常退出后重获、强杀后重获六项检查并报告“全部通过”。提交前独立代码审查随后发现锁在 `aboutToQuit` 阶段释放、早于 `app.exec()` 返回后的 lingering QThread drain，故该轮人工结果保留但不据此 accepted，WP 重新进入 stabilizing 修复。

退出清理锁复核就绪记录（2026-07-20）：新增静态生命周期 RED 与真实 QThread-drain barrier。修复前 `acceptance-qthread-red-20260720-233832` 在 drain marker 已出现且旧 Qt 仍存活时，第二个 Tauri 未得到 `already_running`，精确证明锁过早释放；失败轮 finally 清理后 Shell/Python 均为 0，真实 `data/` before/after canonical SHA-256 均为 `1cd1602645b63308e74e2cd831d25870614ae26ff3bb993996a681071f0bd84c`。最小修复移除 `aboutToQuit` 锁释放，在 acquiring 主线程以 `try/finally` 覆盖完整 acquired 生命周期，直到外部工具清理和 lingering QThread drain 返回后才释放；若 drain 超时返回 `False`，则以 `os._exit(1)` fail-closed，让 Windows 随进程终止原子回收 mutex，不经 Python 栈展开提前释放。验收脚本登记每个本轮根进程的 PID、StartTime、路径及运行期间观察到的后代身份，finally 只对精确匹配身份回收并核对零残留，不按全局进程名清扫。正常 drain 修复轮 `acceptance-qthread-green-20260720-234011` 为 12/12；加入超时故障注入后的最终轮 `acceptance-drain-fail-closed-green-20260720-235133` 为 13/13，证明 drain 期间冲突、drain 完成后可重获，以及 drain 超时必须先终止旧 Qt 才可重获。最终轮真实 `data/` 121 文件、1,045,977,101 bytes，before/after canonical SHA-256 均为 `1cd1602645b63308e74e2cd831d25870614ae26ff3bb993996a681071f0bd84c`，精确登记进程残留 0。fresh 回归为 Rust 20/20、Node 18/18、Python 13/13、PowerShell parser、隔离 py_compile、debug/release locked build、cargo fmt 与 git diff --check 全部通过；等待独立复审及负责人针对 QThread-drain 生命周期完成一次简短实机复验，完成前保持 stabilizing。

验收记录：

```text
状态：accepted
验收日期：2026-07-20
修改范围：main.py、legacy_qt_main.py、start.bat、start-legacy-qt.bat、.gitattributes 的两个精确 CRLF 规则、app/core/instance.py、desktop/src-tauri 的 shared mutex/入口代码、WP-1A-04 测试与验收脚本、ADR-0003 和本文记录
自动测试：cargo fmt 退出码 0；Rust 20/20；Node 18/18；Python 13/13；PowerShell parser、隔离 py_compile、debug/release cargo build --locked、git diff --check 全部通过
故障测试：双向应用锁冲突；同名 Event API fatal；正常/强杀释放；stale data/sakura.lock；重复执行；默认/显式回退入口；QThread drain 期间持锁；drain 超时 os._exit fail-closed；验收失败精确 PID/StartTime/path/后代清场
真实应用验收：自动矩阵 acceptance-drain-fail-closed-green-20260720-235133 为 13/13；负责人两轮实机确认默认 Tauri、显式 Qt 回退、双向冲突、正常/强杀释放及 Qt 正常退出后立即启动 Tauri，全部通过
数据门禁：最终真实 data/ 121 文件、1,045,977,101 bytes；before/after path/length/UTC mtime/SHA-256 canonical digest 均为 1cd1602645b63308e74e2cd831d25870614ae26ff3bb993996a681071f0bd84c；未迁移、清理或恢复真实用户数据
进程门禁：每个测试根设置 deadline；精确登记 PID/StartTime/path 与观察后代；最终根、后代、项目 runtime Python 和 Sakura Shell 残留为 0
关联 ADR：ADR-0003 更新为 Technically Validated；Phase 3 兼容门禁未开始，ADR 不得 Accepted
明确非目标：没有 Python Core、Supervisor、Fake Core、IPC、Assistant、聊天、设置、TTS、Tools、MCP、Memory、插件、截图、主动互动或 WP-1B 生产能力；没有改变 legacy Qt 业务语义或共享 schema
P0/P1：零；退出条件相关缺陷为零；最终独立复审无 Critical/Important
已知限制：目标仍仅当前 Windows x64/WebView2 环境；legacy batch 自动场景只声明冲突传播，成功回退由隔离 Qt smoke 与负责人实机覆盖；QThread drain 超时采用进程级 fail-closed，退出码 1
独立回退方式：整体 git revert 本 WP accepted 提交，恢复 WP-1A-03 的 main.py/start.bat legacy Qt 默认入口，移除 legacy_qt_main.py、start-legacy-qt.bat 与双方 named mutex 接入；不删除、不恢复、不改写真实 data/、历史 data/sakura.lock、Qdrant lock 或同期日志
关联提交：本 WP accepted 提交（feat(runtime): 建立共享应用锁与双入口回退）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-20
允许目录：desktop/src-tauri/Cargo.toml 中仅限现有 windows crate 的 Job Object/CreateProcess 所需 Win32 feature；desktop/src-tauri/src/main.rs 中仅限声明进程树模块；desktop/src-tauri/src/managed_process_tree.rs 及其同文件 Rust 测试；desktop/tests/ 中仅限 WP-1B-01 Windows 真实进程树验收脚本；本文仅更新 WP-1B-01 状态与验收记录
明确禁止目录：main.py、legacy_qt_main.py、start*.bat、app/、desktop/frontend/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、共享用户数据 schema；shared instance/window 既有语义；Supervisor/generation 状态机、Fake Core transport、协议握手、自动重启、Python Core/Assistant、聊天及 WP-1B-02 或后续生产能力
验收环境：当前 Windows 11 23H2 build 22631.4890 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；现有 windows crate 0.61.3；不安装或升级依赖；所有测试进程使用测试可执行文件与隔离临时目录，设置 deadline，并核对根/一层/多层后代、Job/Process/Thread 句柄和计时器无残留
关联 ADR：ADR-0001（每 generation 独立 Windows Job Object、JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE、suspended spawn、安全失败、正常/强制/Drop 分离语义）；本 WP 不单独提升 ADR 状态，待 WP-1B-04 完整故障矩阵后统一评估 Technically Validated
计划提交：feat(runtime): 建立 Windows 受控进程树原语
回退方式：整体 git revert 本 WP accepted 提交，移除 managed_process_tree 模块、对应 Win32 feature 和 WP-1B-01 验收脚本；保留 WP-1A-04 Shell 与共享应用锁，不触碰真实 data/ 或用户进程
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-21
生产实现：新增 ManagedProcessTree Windows 原语；每棵树先创建匿名 Job 并启用 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE，再以 CreateProcessW(CREATE_SUSPENDED) 创建根、AssignProcessToJobObject 成功后才 ResumeThread；正常 wait/release_exited_handles、强制 terminate_tree 和 Drop 保险为分离语义；verify_tree_exited 查询 Job ActiveProcesses，不把根退出误当整树退出；非 Windows 安全返回 UnsupportedPlatform
RED/GREEN：首个正常路径测试因 ManagedProcessSpec/ManagedProcessTree/WaitOutcome 缺失按预期编译失败；terminate_tree、Assign 失败回滚、Resume 失败回滚和 embedded NUL 拒绝均先取得缺失 API/行为 RED 后最小实现转绿；句柄门禁首轮从冷路径 77→93 失败，逐轮计数证明成功路径首次稳定到 90、失败路径首次稳定到 93 且其后各 12 轮恒定，修正为分别预热后测线性泄漏并通过
首轮自动测试（历史证据，已由下方复审后门禁取代）：cargo fmt --check 退出码 0；cargo test --locked 为 31 passed、7 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 与 git diff --check 通过
故障测试：有界 wait timeout；忽略退出后的 TerminateJobObject；根退出但后代继续存活；一层/多层后代；suspended 根 Assign 失败；已入 Job 后 Resume 失败；embedded NUL；不存在程序；重复 terminate/release；Drop 时活动多层树；成功/失败路径各 12 次句柄泄漏检查
首轮真实 Windows 验收（历史证据，已由下方复审后验收取代）：temp/runtime-v2-wp-1b-01/acceptance-20260721-001602/summary.json；直接运行真实 Rust 测试可执行文件，11/11 必需场景通过，45 秒 deadline，调用者已在 Windows Job 的嵌套验证通过，结束匹配测试根/后代为 0
首轮数据声明（历史证据）：本 WP 进程夹具只使用系统隔离临时目录，不读取或写入仓库 data/；首轮摘要曾写入 dataTouched=false，该无测量支撑字段已在复审修复中移除，不再作为最终门禁证据
旧迁移复用：按 WP-0-03 R26/WP-1B-01 结论不复用旧 ManagedProcess；没有 cherry-pick、restore 或复制旧迁移实现
已知限制：尚未接入 Supervisor/generation、stdio 管道、协议握手、Fake Core 或 UI；正式目标仅 Windows，非 Windows 只有安全失败；真实验收的外层脚本通常只来得及观察测试根，后代回收证据由 Job ActiveProcesses、隔离 PID marker 和退出后全路径零残留共同提供
P0/P1：自动门禁当前为零；等待独立规格/代码复审和负责人实机复验后才能 accepted
回退步骤：整体 revert 本 WP accepted 提交；移除 managed_process_tree.rs、main.rs 模块声明、JobObjects feature 和 Windows 验收脚本；不影响 WP-1A-04 Shell/共享应用锁，不触碰真实 data/ 或用户进程
关联提交：待 accepted 后提交
独立复审修复记录（2026-07-21）：首轮复审发现 3 个 Important：Job ActiveProcesses 轮询可能在 deadline 后观察到空树并 false-pass；验收脚本异常传播时可能跳过 finally 后的全路径残留扫描；验收可能选择旧测试产物、硬编码 dataTouched=false 且句柄计数默认与其他测试并行。按 TDD 保持本 WP stabilizing，未进入 WP-1B-02。
复审 RED/GREEN：新增 deadline 边界测试先因 classify_job_poll/JobPollDecision 缺失编译失败；新增 PowerShell 清场契约先因缺少 --test-threads=1 失败；真实验收更新后首轮又因新增场景仍断言 11/11 按预期失败。最小修复将 verify_tree_exited 与 Assign 后回滚统一到严格 deadline 判定和按剩余时间睡眠；验收 finally 在异常传播前按精确可执行路径发现、登记、停止并复扫根与未观测后代；校验测试产物不早于本 WP 源码并记录双方 SHA-256；移除硬编码 dataTouched，改为明确不访问 data/ 的作用域声明；直接 Rust 验收固定 --test-threads=1。三组 RED 均转绿。
复审后自动门禁：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 32 passed、7 ignored fixture、0 failed；debug/release cargo build --locked 均通过；两个 PowerShell 脚本 parser、验收清场契约、git diff --check 和测试可执行文件残留扫描均通过。
复审后故障注入：FailureCleanupProbe 单独启动 root-exits-with-descendant-holding 真实夹具并得到预期 exit 44；主验收 finally 回收退出根留下的同路径后代；外层再次扫描精确测试可执行路径为零残留。
复审后真实 Windows 验收：temp/runtime-v2-wp-1b-01/acceptance-review-fixes-green-20260721/summary.json；12/12 必需场景通过，45 秒 deadline，登记 6 个精确进程身份，最终残留 0；测试 exe SHA-256 为 67d7a8b8f0937a4e9d4d4bf38aca38153b6c24a6f24cc149f5cb74811d16a678，摘要同时记录 Cargo.toml、main.rs、managed_process_tree.rs 的路径、长度、mtime 和 SHA-256。
负责人实机证据：负责人报告“针对性复验通过”；复审修复没有扩展 UI、Supervisor、Fake Core、IPC 或 data/ 范围，且随后重新执行的真实 Windows 进程树成功/失败验收均通过。
最终复审：Critical 0、Important 0；spec compliance 与 code quality 均通过；退出条件相关缺陷为零。
```

验收记录：

```text
状态：accepted
验收日期：2026-07-21
修改范围：desktop/src-tauri/Cargo.toml 的 Win32 JobObjects feature；main.rs 的 managed_process_tree 模块声明；managed_process_tree.rs 及同文件测试；desktop/tests/ 的 WP-1B-01 Windows 验收与清场契约脚本；本文状态和证据记录
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 32 passed、7 ignored、0 failed；debug/release cargo build --locked 均通过；PowerShell parser、验收清场契约、git diff --check 和最终测试进程残留扫描通过
故障测试：wait timeout；严格 deadline 边界；根退出后代存活；一层/多层后代；Assign/Resume 回滚；不存在程序与 embedded NUL；重复 terminate/release；Drop 活动多层树；成功/失败各 12 轮句柄检查；异常验收时未观测后代精确清场
真实应用验收：自动真实 Windows 进程树矩阵 acceptance-review-fixes-green-20260721 为 12/12，FailureCleanupProbe 按预期 exit 44 后残留 0；负责人报告针对性复验通过
数据门禁：本 WP 及验收不访问仓库 data/，不修改共享用户数据 schema，不迁移、清理或恢复真实用户数据；不再以硬编码 dataTouched 字段充当测量证据
进程门禁：测试根均有 deadline；按 PID/StartTime/path 登记并以精确测试 exe 路径补获未观测后代；最终根/一层/多层后代残留 0；句柄线性泄漏检查在单测试线程通过
关联 ADR：ADR-0001；本 WP 只验证 Windows Job Object 原语，ADR 保持 Proposed，待 WP-1B-04 完整 Supervisor/Fake Core 故障矩阵后统一评估 Technically Validated
明确非目标：未实现 Supervisor/generation、stdio/IPC、Fake Core、自动重启、Python Core/Assistant、聊天、设置、TTS、Tools、MCP、Memory、插件、截图、主动互动或 WP-1B-02 及后续能力
P0/P1：零；退出条件相关缺陷为零；最终独立复审 Critical/Important 均为零
已知限制：正式目标仅 Windows，非 Windows 安全返回 UnsupportedPlatform；release_exited_handles 的约定是完成 wait/树退出验证后的最终释放，释放后再次 wait/terminate 返回 InvalidState；测试辅助 OpenProcess 对同用户隔离夹具的访问失败按进程已退出处理，后续可收紧 oracle，但不影响当前成功、回滚与零残留多重证据
独立回退方式：整体 git revert 本 WP accepted 提交，移除 managed_process_tree.rs、main.rs 模块声明、JobObjects feature 与两个 Windows 验收脚本；保留 WP-1A-04 Shell/共享应用锁，不触碰 data/ 或用户进程
关联提交：本 WP accepted 提交（feat(runtime): 建立 Windows 受控进程树原语）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-21
允许目录：desktop/src-tauri/src/main.rs 中仅限声明 Supervisor 模块；desktop/src-tauri/src/core_supervisor.rs 及其同文件 Rust 单元/真实测试进程测试；desktop/tests/ 中仅限 WP-1B-02 Windows 串行 Supervisor 真实验收脚本；本文仅更新 WP-1B-02 状态与验收记录
明确禁止目录：Cargo.toml/Cargo.lock 与依赖；managed_process_tree.rs 和 WP-1B-01 既有原语；main.py、legacy_qt_main.py、start*.bat、app/、desktop/frontend/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、共享 schema；真实 transport/stdio/IPC/hello/initialize、Fake Core、自动恢复 budget/backoff、Python Core/Assistant、聊天及 WP-1B-03 或后续生产能力
验收环境：当前 Windows 11 23H2 build 22631.4890 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；不安装或升级依赖；纯状态机测试与现有 ManagedProcessTree 测试进程均使用隔离临时目录和 deadline，重复执行后核对根/后代/句柄/计时器零残留
关联 ADR：ADR-0001（所有 lifecycle intent 串行化、每 generation 独立 cancellation、旧 generation 回调隔离、stop/finalize/app shutdown 幂等、app shutdown 永久禁止新 generation）；不提前实现 WP-1B-04 自动恢复策略，不提升 ADR 状态
计划提交：feat(runtime): 建立串行 Supervisor generation 生命周期
回退方式：整体 git revert 本 WP accepted 提交，移除 core_supervisor 模块与 WP-1B-02 验收脚本；保留 WP-1B-01 ManagedProcessTree，不触碰 Shell、共享应用锁、data/ 或用户进程
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-21
生产实现：新增同步串行 CoreSupervisor；所有 Start/Stop/Restart/AppShutdown intent 先进入内部 FIFO 再由同一可变状态所有者归约；每代使用独立 GenerationId、generation number 和 cancellation 状态；只发出 SpawnGeneration/StopGeneration lifecycle action，不直接操作 transport；restart 必须等待旧 generation stopped barrier 后才能发出下一代 spawn；AppShutdown 一经提交永久禁止新 generation；只有当前 Running 且未取消的 generation callback 可被接受
RED/GREEN：首个 restart-during-spawn 测试因全部 Supervisor 类型缺失编译失败后转绿；app shutdown、spawn failure 和幂等 stop 测试先因 observe_spawn_failed 缺失编译失败后转绿；旧 generation callback 测试先因 accepts_generation_callback 缺失编译失败后转绿；随后新增 spawn failure during stop barrier 测试，首次真实失败为错误地提前发出 replacement spawn，最小修复后保持 Stopping 直到 observe_generation_stopped，再转绿
首轮自动测试（历史证据，已由下方复审后门禁取代）：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 40 passed、7 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 通过
故障测试：restart during spawn；app shutdown during spawn；spawn failure；spawn failure during stop；重复 stop/finalize/app shutdown；显式 stop 覆盖 pending restart；旧 generation 晚到 spawn/callback；start/restart after app shutdown；manual restart only after old cleanup
首轮真实 Windows 验收（历史证据，已由下方复审后验收取代）：temp/runtime-v2-wp-1b-02/repeat-a-20260721/summary.json 与 repeat-b-20260721/summary.json；同一 PowerShell 会话连续两轮各 8/8，通过 30 秒 deadline；真实 ManagedProcessTree 完成 generation 1 restart 清理后才创建 generation 2，并由 app shutdown 回收；测试 exe SHA-256 a2bbb9701bda8dc45adfff6aeca81d7bda92a5db5fd647fc32e3fac84f17f91c；最终精确路径根/后代残留 0
数据门禁：本 WP 与验收不访问仓库 data/，不修改共享 schema，不迁移、清理或恢复真实用户数据
旧迁移复用：不复用旧迁移 Supervisor；没有 cherry-pick、restore 或复制旧迁移实现
已知限制：尚未实现 Fake Core、transport/stdio/hello/initialize、协议优雅关闭、自动恢复 budget/backoff 或 Tauri app 事件接线；Restarting 枚举只冻结状态词，不在本 WP 提前实现 WP-1B-04 backoff；真实进程由既有 WP-1B-01 原语承载，Supervisor 本身只产出串行 lifecycle action
P0/P1：自动门禁当前为零；等待独立规格/代码复审后才能 accepted
回退步骤：整体 revert 本 WP accepted 提交；移除 core_supervisor.rs、main.rs 模块声明和 windows_core_supervisor_acceptance.ps1；保留 WP-1B-01 进程树原语，不触碰 Shell、data/ 或用户进程
关联提交：待 accepted 后提交
独立复审修复记录（2026-07-21）：复审期间补出三个 lifecycle 缺口。其一，spawn failure during stop 曾绕过旧代 cleanup barrier 提前 spawn，RED 后改为等待 generation_stopped；其二，非 Stopping 的当前代退出曾误报 Stopped，RED 后区分为 Exited；其三，初版 cancellation 只是快照布尔值，无法唤醒异步 generation worker，新增每代独立 Arc<AtomicBool> token 并由 SpawnGeneration action 携带，随后复审又发现 spawn failed/意外退出清空 current 前未 cancel，终态 token 测试先 RED 后统一修复。验收工作目录同时从仓库根改为隔离 Evidence 目录。
复审后自动门禁：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 43 passed、7 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 与 git diff --check 通过。
复审后真实 Windows 验收：temp/runtime-v2-wp-1b-02/final-repeat-a-20260721/summary.json 与 final-repeat-b-20260721/summary.json；同一 PowerShell 会话连续两轮各 11/11，30 秒 deadline；test exe SHA-256 817f253215c75abc956f552a7e5bce74fa974916dcb86a4c256d43a17e04e684；core_supervisor.rs SHA-256 66f62d7eb3216e230defcadab4963dc67291f8eb82cc9006b9977bb38b0f00ec；两轮最终精确路径根/后代残留 0。
最终验收脚本绑定（2026-07-21）：为防止 harness-after-evidence 漂移，摘要 sourceManifest 新增验收脚本自身。当前最终双轮为 temp/runtime-v2-wp-1b-02/final-current-a-20260721/summary.json 与 final-current-b-20260721/summary.json，均为 11/11、残留 0；验收脚本 SHA-256 826871a8df0318b5bd48ebfcef28dba6cf6e17cd1bbd71aa321092f69b2d2b89，test exe 和 core_supervisor.rs SHA-256 与上一记录相同。
当前门禁：P0/P1 为零；等待独立复审最终结论后方可 accepted。
最终复审：Critical、Important、Minor 均为 0；spec compliance 与 code quality 均通过；复审者独立复跑 43 passed、7 ignored 并核对最终双轮 11/11、脚本/源码/测试 exe SHA-256 和进程残留 0；退出条件相关缺陷为零。
```

验收记录：

```text
状态：accepted
验收日期：2026-07-21
修改范围：desktop/src-tauri/src/core_supervisor.rs 及同文件测试；main.rs 的模块声明；desktop/tests/windows_core_supervisor_acceptance.ps1；本文 WP-1B-02 状态和证据记录
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 43 passed、7 ignored、0 failed；debug/release cargo build --locked 均通过；PowerShell parser、git diff --check 和最终测试进程残留扫描通过；独立复审重复运行同样通过
故障测试：restart/stop/app shutdown during spawn；spawn failure 与 stopping barrier；意外退出 Exited 语义；每个终态 cancellation；重复 stop/finalize/app shutdown；显式 stop 覆盖 pending restart；旧 generation 晚到 spawn/stop/callback；shutdown 后 Start/Restart 永久禁止
真实应用验收：当前脚本绑定的 final-current-a/b 同会话连续两轮各 11/11；真实 generation 1 Windows Job 完整回收后才创建 generation 2，app shutdown 再回收第二代；30 秒 deadline；最终根/后代残留 0
数据门禁：测试 cwd 为隔离 Evidence 目录；本 WP 与验收不访问仓库 data/，不修改共享 schema，不迁移、清理或恢复真实用户数据
进程门禁：真实测试根有 deadline；按 PID/StartTime/path 登记，finally 精确路径补获、停止并二次复扫；所有 terminal event 取消 generation token；最终测试 exe 根/后代残留 0
关联 ADR：ADR-0001；本 WP 验证串行 intent/generation 生命周期，ADR 保持 Proposed，待 WP-1B-04 完整故障矩阵后统一评估 Technically Validated
明确非目标：未实现 Fake Core、transport/stdio/IPC、hello/initialize、协议优雅关闭、自动恢复 budget/backoff、Tauri app 事件接线、Python Core/Assistant、聊天或 WP-1B-03 及后续能力
P0/P1：零；退出条件相关缺陷为零；最终独立复审 Critical/Important/Minor 均为零
已知限制：Restarting 状态词已冻结但 restart backoff 留待 WP-1B-04；当前 Supervisor 只产出 lifecycle action，WP-1B-03 才接最小测试 transport/Fake Core；generation number 极限溢出不属于可达运行规模
独立回退方式：整体 git revert 本 WP accepted 提交，移除 core_supervisor.rs、main.rs 模块声明与 windows_core_supervisor_acceptance.ps1；保留 WP-1B-01 ManagedProcessTree，不触碰 Shell、共享应用锁、data/ 或用户进程
关联提交：本 WP accepted 提交（feat(runtime): 建立串行 Supervisor generation 生命周期）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-21
允许目录：desktop/src-tauri/src/main.rs 中仅限声明 cfg(test) Fake Core 模块；desktop/src-tauri/src/fake_core_runtime.rs 中仅限测试专用 Fake Core、隔离临时目录 marker transport、Supervisor/ManagedProcessTree 正常启动关闭集成测试；仅在 TDD 证明本 WP 正常链缺口时窄改 core_supervisor.rs 及同文件测试；desktop/tests/ 中仅限 WP-1B-03 Windows Fake Core 真实验收脚本；本文仅更新 WP-1B-03 状态与验收记录
明确禁止目录：Cargo.toml/Cargo.lock 与依赖；managed_process_tree.rs 和 WP-1B-01 既有原语；main.py、legacy_qt_main.py、start*.bat、app/、desktop/frontend/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、共享 schema；真实业务 IPC Envelope、真实 app.core_host、initialize/Snapshot、自动重启 budget/backoff、旧代恢复策略、Python Core/Assistant、聊天及 WP-1B-04 或后续生产能力
验收环境：当前 Windows 11 23H2 build 22631.4890 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；不安装或升级依赖；Fake Core 使用当前 Rust 测试可执行文件和按 PID 唯一的系统临时目录；hello 3 秒、协议 shutdown 3 秒、完整树停止 5 秒 deadline；每轮 finally 精确回收并核对根/后代/句柄/计时器和临时 marker 零残留
关联 ADR：ADR-0001（spawn、最小 hello、running、system.shutdown、超时强杀、幂等 finalize、窗口/主线程可退出）；ADR-0002 仅引用 hello-before-heavy-init 和 lifecycle deadline，不冻结业务 envelope、不提升 ADR 状态
计划提交：feat(runtime): 建立 Fake Core 正常启动关闭链
回退方式：整体 git revert 本 WP accepted 提交，移除测试专用 Fake Core 模块、验收脚本及任何经 TDD 证明的最小 Supervisor 正常链适配；保留 WP-1B-01 进程树和 WP-1B-02 串行状态机，不触碰 Shell、data/ 或用户进程
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-21
生产实现：新增仅在 cfg(test) 编译的 Fake Core runtime；以当前 Rust 测试可执行文件作为真实子进程，用父测试 PID+单调序号生成系统临时目录，并通过继承环境传递唯一目录；marker transport 只包含 transport.ready、hello.request/response、shutdown.request/ack，不定义业务 IPC envelope；Supervisor 只在 hello 完成后进入 Running，Stop action 先尝试 3 秒协议关闭，超时则 TerminateJobObject，并在总计 5 秒内验证整树退出/释放句柄/幂等 finalize
Supervisor 最小适配：新增 FinalizeOutcome，使协议 ack 与进程退出同时到达、重复 finalize 时可以验证只有第一次 applied；保留原 observe_generation_stopped 兼容入口，不改变 WP-1B-02 intent/generation 语义
RED/GREEN：首个正常 Fake Core 测试因 run_fake_core_scenario/FakeCoreMode 缺失编译失败后转绿；忽略 shutdown 测试先因 IgnoreShutdown variant 缺失编译失败后转绿；延迟 hello shutdown 测试先因后台场景 helper 缺失编译失败后转绿；延迟 hello 正常完成测试首次在 3 秒 hello deadline 真实失败，最小增加 250ms 延迟后响应且 shutdown 可抢占，再转绿；重复 finalize 测试先因 finalize_generation 缺失编译失败后转绿
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 48 passed、10 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 与 git diff --check 通过
故障测试：250ms 延迟 hello 正常完成；hello 等待在 worker 且 AppShutdown 主线程归约少于 100ms；hello 未完成时 shutdown 控制请求可抢占；Fake Core 忽略 shutdown 后 3 秒强杀；协议 ack/根退出汇合到只 applied 一次的 finalize；所有路径总停止 5 秒 deadline
真实 Windows 验收：temp/runtime-v2-wp-1b-03/repeat-a-20260721/summary.json 与 repeat-b-20260721/summary.json；同一 PowerShell 会话连续两轮各 4/4，30 秒外层 deadline；单轮登记 4/5 个精确测试根/后代身份；hello 3 秒、shutdown 3 秒、全树 5 秒门禁通过；test exe SHA-256 0d3d9939c6e043e66fe6e51e7167802287150bd0b05f611b3d2043118fa767f9；最终精确路径进程和本轮 Fake Core 临时目录残留均为 0
数据门禁：测试 cwd 为隔离 Evidence 目录，transport 使用系统唯一临时目录；本 WP 与验收不访问仓库 data/，不修改共享 schema，不迁移、清理或恢复真实用户数据
旧迁移复用：不复用旧迁移 Fake Core/transport；没有 cherry-pick、restore 或复制旧迁移实现
已知限制：Fake Core 和 marker transport 仅测试编译，不是 app.core_host 或业务 IPC；不含 initialize/Snapshot/自动恢复/旧代故障矩阵；当前以后台 hello worker + 同线程快速 AppShutdown 归约证明不阻塞 lifecycle，尚未把 Fake Core 接入真实 Tauri Shell UI，该真实窗口/退出组合门禁在 Phase 1B 完成时统一实机验收
P0/P1：自动门禁当前为零；等待独立规格/代码复审确认本 WP 的 test-only 集成满足退出证据后才能 accepted
回退步骤：整体 revert 本 WP accepted 提交；移除 fake_core_runtime.rs、main.rs cfg(test) 声明、windows_fake_core_lifecycle_acceptance.ps1 和 FinalizeOutcome 最小适配；保留 WP-1B-01/02，不触碰 Shell、data/ 或用户进程
关联提交：待 accepted 后提交
```

稳定化追加记录（2026-07-21，保留上文历史证据）：

```text
状态：stabilizing
审查修复：独立复审发现 marker 等待先查文件再查 deadline 会接受迟到响应；新增 deadline 已过但 marker 已存在的边界测试，先稳定复现失败，再改为先判绝对 deadline 后转绿。延迟 hello 夹具新增 hello.pending/hello.release 确定性同步；正常路径从 hello.request 起使用同一 3 秒绝对 deadline 覆盖 pending 与 response，shutdown 路径不 release，并明确断言提交 AppShutdown 时 hello 尚 pending、shutdown ack 后仍无 hello.response
验收脚本 RED/GREEN：新增 deadline 边界场景后，旧脚本因仍要求 4/4 真实失败；最小更新为 5 个必需场景及 5 passed、0 failed、3 ignored 后转绿。失败路径 finally 后 Fake Core 临时目录残留为 0
最新自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 49 passed、10 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 与 git diff --check 通过
最新真实 Windows Fake Core 验收：temp/runtime-v2-wp-1b-03/final-fixed-a-20260721/summary.json 与 final-fixed-b-20260721/summary.json；两轮各 5/5，单轮登记 5 个精确测试根/后代身份；进程与 fixture 目录残留均为 0；test exe SHA-256 8a84d1460cb9a836b742ebf7b40ffc31ab5b33a77fa34230b2f686e6c58f3340；fake_core_runtime.rs SHA-256 c145155bc1b35aece6ff5b0ea078e1bf381fd5fb89a5151ce330a8a58d4a3e05；验收脚本 SHA-256 8a2ed2ca838e1ea9f3c451b835631a88d37f36f6a15675ad6ab24da628836c7a
代码审查：marker transport 保持 cfg(test)/Windows；正常、强杀、延迟 hello 抢占、严格 deadline、进程树回收与幂等 finalize 的代码级退出证据成立；未发现 WP-1B-04 越界
待决退出条件：冻结退出证据要求“延迟 hello 不阻塞 Tauri 主线程和窗口退出”，但本 WP 允许范围又把 main.rs 限为 cfg(test) 模块声明并禁止真实生产接线。当前测试证明 lifecycle 归约和后台 hello worker 不阻塞，但没有启动真实 Tauri event loop/WebView/window。未经项目负责人明确批准调整/延期该退出条件或扩展允许范围，不将本 WP 标记 accepted，也不开始 WP-1B-04
已知限制：spawn_fake_core 使用的环境变量为本 WP 私有且由全局 mutex 串行保护，但当前清场会删除而非恢复调用前同名环境值；验收环境未预置该私有变量，不影响本轮结果，后续 accepted 前可用 RAII 收紧
关联提交：待 accepted 后提交
```

计划门禁裁定（2026-07-21）：项目负责人已明确将需要真实实机判断的验收统一放在每个 Phase 完成时执行，并要求 Agent 在 Phase 结束时停下给出步骤。本裁定解决了上文记录的范围矛盾：WP-1B-03 只要求证明 pending hello 不阻塞 Supervisor lifecycle caller，保持 test-only Fake Core 和不接生产入口的允许范围；真实 Tauri event loop/WebView/window 与 pending hello 共存时的关闭、可见性和进程树零残留，明确移入 Phase 1B 最终包 WP-1B-04，作为 Phase 1B accepted 前不得延期或省略的真实应用/实机门禁。该裁定只调整证据落点，不删除门禁、不授权真实 Python Core 或 WP-1C 能力。

验收记录：

```text
状态：accepted
验收日期：2026-07-21
修改范围：desktop/src-tauri/src/fake_core_runtime.rs；core_supervisor.rs 的 FinalizeOutcome 最小适配及测试；main.rs 的 cfg(test) 模块声明；desktop/tests/windows_fake_core_lifecycle_acceptance.ps1；本文 WP-1B-03 状态、证据与负责人门禁裁定
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 49 passed、10 ignored fixture、0 failed；debug/release cargo build --locked 均通过；PowerShell parser 和 git diff --check 通过
故障测试：deadline 已过但 marker 已存在时严格拒绝；250ms delayed hello 只在显式 release 后响应；pending hello 时 AppShutdown lifecycle 归约少于 100ms且取消 worker；shutdown ack 后无迟到 hello；忽略 shutdown 后 3 秒以精确原因码 92 强杀；5 秒内整树退出；重复 finalize 仅第一次 applied
真实 Windows 进程验收：final-fixed-a/b 两轮各 5/5；真实 Rust Fake Core 子进程由独立 Windows Job 承载；每轮登记 5 个精确根/后代身份；根、后代、Job 句柄、hello worker、计时器和 marker 目录残留均为 0；外层 30 秒、hello/shutdown 各 3 秒、全树停止 5 秒 deadline
真实应用验收裁定：本 WP 不把 test-only Fake Core 接入生产 Tauri；负责人已批准将真实 Tauri event loop/WebView/window 与 pending hello/AppShutdown 共存验收移入 Phase 1B 最终包 WP-1B-04，门禁已同时写入该包允许能力和退出证据，不得省略
数据门禁：验收 cwd 为隔离 Evidence 目录，marker 位于唯一系统临时目录；不访问仓库 data/，不修改 schema，不迁移、清理或恢复真实用户数据
关联 ADR：ADR-0001 正常启动/关闭与强制回收链获得技术证据，ADR 状态仍待 WP-1B-04 完整故障矩阵统一评估；ADR-0002 仅引用 hello-before-heavy-init 与 lifecycle deadline，不冻结业务 Envelope
明确非目标：未创建真实 app.core_host 或业务 IPC；未实现 initialize/Snapshot、自动恢复 budget/backoff、Python Core/Assistant、聊天、Tauri 生产接线或 WP-1B-04 后续能力
P0/P1：零；退出条件相关缺陷为零；最终独立复审 Critical/Important 均为零
已知限制：测试私有环境变量由全局 mutex 串行保护，验收环境未预置该变量，但清场删除而不恢复潜在旧值；真实 Tauri + pending hello 组合证据按负责人裁定由 WP-1B-04 承担
独立回退方式：整体 git revert 本 WP accepted 提交，移除 fake_core_runtime.rs、main.rs cfg(test) 声明、验收脚本和 FinalizeOutcome 最小适配；保留 WP-1B-01/02，不触碰 Shell、data/ 或用户进程
关联提交：本 WP accepted 提交（feat(runtime): 建立 Fake Core 正常启动关闭链）
```

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
- 延迟 hello 不阻塞 Supervisor lifecycle caller；真实 Tauri 主线程和窗口退出组合在 Phase 1B 最终包 WP-1B-04 验收。
- 协议关闭与进程退出同时发生时汇合到同一幂等 finalize。

独立回退：回退 Fake Core transport 集成，保留 Supervisor 和进程树。

### WP-1B-04：Supervisor 恢复、竞态和进程泄漏门禁

激活记录：

```text
状态：active
开始日期：2026-07-21
允许目录：desktop/src-tauri/src/core_supervisor.rs 中仅限有限自动恢复 budget/backoff、可重试/不可重试失败分类占位、手动 retry、定时器 token 与竞态归约及同文件测试；desktop/src-tauri/src/fake_core_runtime.rs 中仅限扩展 WP-1B-04 Fake Core 崩溃/卡死/后代/旧 generation/重复恢复集成夹具；desktop/src-tauri/src/main.rs 中仅限声明并接入 debug-only Phase 1B 验收模式及 Tauri 退出事件清场；desktop/src-tauri/src/phase_1b_runtime_acceptance.rs 中仅限 debug-only 真实 Tauri + Fake Core 验收桥；desktop/tests/ 中仅限 WP-1B-04 Windows 恢复与真实 Tauri 验收脚本；docs/adr/0001-runtime-v2-process-supervision.md 仅更新技术验证证据和状态；本文仅更新 WP-1B-04 状态、设计裁定与验收记录
明确禁止目录：Cargo.toml/Cargo.lock 与依赖；managed_process_tree.rs 和 WP-1B-01 既有原语；shared_instance.rs、window_geometry.rs、window_interaction.rs、desktop/frontend/；main.py、legacy_qt_main.py、start*.bat、app/、plugins/、data/、runtime/、characters/、third_party/、tools/mcp/、共享 schema；真实 Python Core、app.core_host、业务 IPC Envelope/stdio/Router、initialize/Snapshot、Assistant、聊天、设置、TTS、Tools、MCP、Memory、插件、截图、主动互动或 WP-1C 及后续能力
验收环境：当前 Windows 11 23H2 build 22631.4890 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3；不安装或升级依赖；恢复状态机使用确定性逻辑时间验证 250ms/1s/3s 有限 backoff 和最多 3 次自动重启；真实进程/窗口验收使用 debug Tauri 与隔离系统临时目录，外层 deadline 60 秒，单次 shutdown 3 秒、整树停止 5 秒；每轮 finally 核对 Tauri 根、WebView、Fake Core 根/后代、Job 句柄、worker、timer 和临时目录零残留；真实 data/ 如被真实 Tauri 入口触及则执行前后完整 path/length/mtime/SHA-256 清单并证明零变化
关联 ADR：ADR-0001（有限 budget/backoff、失败分类、manual retry、spawn/hello/running/stopping/backoff shutdown/retry 竞态、旧 generation 隔离、真实 Tauri 主动退出和完整故障矩阵）；ADR-0002 仅沿用 test-only hello marker 和 lifecycle deadline，不冻结业务协议或提升其状态
计划提交：feat(runtime): 完成 Supervisor 恢复与进程泄漏门禁
回退方式：整体 git revert 本 WP accepted 提交，移除恢复策略、扩展 Fake Core 夹具、debug-only Phase 1B Tauri 验收桥和验收脚本，并把 ADR-0001 恢复至 Proposed；保留 WP-1B-01/02/03 的受控进程树、串行 generation 状态机和单次 Fake Core 启停链，不触碰真实 Python、Shell 既有业务语义、data/ 或用户进程
```

实现设计裁定：Supervisor 保持唯一同步可变状态所有者，不在线程内自行 sleep。可重试失败在旧 generation 完整回收后发出带唯一 token 的 ScheduleRestart action；外部有界 timer 到期后把同一 token 回送，旧 token 永远无效。自动恢复每个 episode 最多 3 次，backoff 固定为 250ms、1s、3s；成功进入 Running 不立即补满 budget，避免“启动成功后立刻崩溃”形成无限循环；只有显式 manual retry 开启新 episode。不可自动重试分类进入 Failed，但仍允许外部状态修复后的 manual retry。AppShutdown 永久取消 backoff 并禁止新 generation；stopping 期间的连续 retry 合并为一个；旧 generation/timer/callback 只完成自身清理。真实 Tauri 组合证据通过 debug-only、显式环境门控的验收桥接入，不改变 release 正常入口，不创建真实 Core Host，也不让 WebView 直接持有或操作 ManagedProcessTree。

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-21
生产实现：CoreSupervisor 新增结构化 FailureReason、Failed 状态、最多 3 次自动恢复 budget、250ms/1s/3s ScheduleRestart、唯一 RestartToken、CancelRestart、manual Retry 与停止/回退竞态归约；timer 由外部 worker 驱动，Supervisor 内部不 sleep；成功进入 Running 不重置 budget，显式 manual retry 才开启新 episode
Fake Core 实现：扩展 test-only 崩溃并遗留后代、初始化占位卡住、旧 generation 回调和恢复后正常退出场景；崩溃根以 37 退出，旧 Job 后代以原因码 94 强制回收，250ms 后创建 generation 2，最后通过 AppShutdown 正常回收
真实 Tauri 验收桥：新增仅 Windows debug_assertions 编译、显式系统临时目录与 mode 环境门控的 phase_1b_runtime_acceptance；Fake Core child 在共享应用锁前分流；父 worker 覆盖 pending hello 关闭和连续三次崩溃后的 3 秒 backoff 关闭；Tauri event loop 退出后取消 worker、协议关闭/Job 清场、join 后根进程才结束；release 正常入口不包含该模块
TDD RED/GREEN：恢复测试先因 FailureReason/Retry/ScheduleRestart/CancelRestart/Failed 缺失编译失败后转绿；显式 Restart during backoff 首次真实失败为旧 timer 未取消，最小修复为先 CancelRestart 再 spawn 后转绿；崩溃后代与初始化卡死测试先因场景 helper 缺失编译失败后转绿；真实 Tauri 入口先因 phase_1b_runtime_acceptance 模块缺失编译失败，最小桥接后 cargo check 转绿
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 当前为 63 passed、13 ignored fixture、0 failed；debug cargo check/build 与 release cargo build --locked 通过；PowerShell 验收脚本 parser 通过；本 WP Fake Core 定向验收后测试 exe 和 WP-1B-03/04 系统临时 marker 目录残留均为 0；广域复扫另发现早期 WP-1B-01 孤立目录 sakura-runtime-v2-wp-1b-01-46064，仅含 descendant-pids.txt 且无关联进程，精确删除请求同样被当前权限审查用量额度拒绝而未执行
故障矩阵：覆盖 retryable/non-retryable 分类、budget 耗尽、Running 后立即再崩溃不补 budget、manual retry 重置 episode、连续 retry 合并、stopping/backoff 中 shutdown、旧 timer、显式 restart 取消旧 timer、旧 generation 回调、spawn/hello/初始化占位阶段 shutdown、运行中崩溃、忽略 shutdown、根退出且后代存活、Job 建立/恢复失败与重复 release（含累计 WP-1B-01/02/03 证据）
真实应用验收：debug Tauri 自动验收脚本已实现 source freshness、真实可见窗口、WM_CLOSE、pending hello、第三次 restart backoff、根/后代/精确路径进程清场、隔离临时目录清场和真实 data/ 前后完整清单；首次执行请求被桌面权限审查器因当前用量额度拒绝，命令未启动、data/ 未被该请求触及，不能宣称通过
P0/P1：当前自动门禁未发现 P0/P1；真实 Tauri/实机退出条件证据缺失，因此保持 stabilizing，不更新 ADR-0001 状态、不提交、不开始 WP-1C-01
已知限制：真实 Tauri 验收脚本仍需在获得显式 GUI 执行权限后实际运行并接受审查；早期 WP-1B-01 孤立系统临时目录需在获得删除权限后精确清理并复扫；debug-only 环境门控桥将在 Phase 1C 真实 Core 接线后移除，当前只服务 Phase 1B 可回退门禁
回退步骤：整体 revert 本 WP accepted 提交；当前尚未提交时可仅移除 recovery 增量、phase_1b_runtime_acceptance.rs、main.rs debug 接线、WP-1B-04 Fake Core 增量与验收脚本；保留 WP-1B-01/02/03，不触碰真实 Python、release 正常入口或 data/
关联提交：待 accepted 后提交
```

真实验收停止记录（2026-07-22，追加且不覆盖上文历史）：

```text
状态：stabilizing
恢复前清场：经项目负责人再次要求继续并明确授权后，已精确删除早期 WP-1B-01 孤立系统临时目录 sakura-runtime-v2-wp-1b-01-46064；删除前再次验证其只含 descendant-pids.txt，删除后 Runtime v2 系统临时目录广域复扫为 0
真实执行：desktop/tests/windows_supervisor_recovery_acceptance.ps1 已实际启动首个 pending-hello debug Tauri 场景；真实窗口出现并达到 acceptance.pending_hello 后，脚本发送 WM_CLOSE，但 Tauri 根进程在 10 秒 deadline 内未退出，验收于脚本第 257 行以 “Tauri root did not exit before deadline” 失败；restart-backoff 场景未开始
安全清场：失败路径 finally 已停止并复扫精确 debug Tauri/Fake Core 路径和登记身份；最终 debug 根/后代进程为 0，WP-1B-04 系统临时目录为 0
数据门禁：temp/runtime-v2-wp-1b-04/final-real-a-20260722/data-before.json 与 data-after.json 各含 121 个文件、1,045,983,998 bytes；canonical SHA-256 前后均为 300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b，证明真实 data/ 零变化
停止原因：命中真实 Tauri 无法有界退出的 P1/强制停止条件；不继续重试、不把脚本 finally 强杀当作通过、不标记 accepted、不更新 ADR-0001、不提交 WP-1B-04，也不开始 WP-1C-01
关联提交：无，WP-1B-04 保持未提交 stabilizing 工作区
```

修复与接受记录（2026-07-22，追加且保留以上失败证据）：

```text
状态：accepted
根因：失败包含两层独立原因。其一，Tauri 2.11.3 的 App::run 在事件循环结束时直接 process::exit，原设计放在 Builder::run 返回后的 acceptance worker cancellation/join 永远不可达；其二，真实验收脚本按 PID 选择第一个可见顶层窗口，在 WebView2 多窗口实现下可能把 WM_CLOSE 发给非 Tauri 主窗口，因此 CloseRequested 根本没有到达 event loop。根退出后立即检查后代还暴露了一个观测竞态：WebView2 后代需要短暂自然收敛，但原脚本没有使用既定的 5 秒进程树 deadline
最小修复：仅在 Windows debug_assertions 且显式 Phase 1B acceptance 环境门控生效时使用 App::run_return；CloseRequested、ExitRequested 和 Exit 立即发出可克隆 cancellation signal，event loop 返回后有界 join worker并保留真实退出码；普通 debug/release 入口继续 App::run。acceptance worker 只在 Tauri build 成功后启动，构建失败不会留下未 join 线程。脚本按 tauri.conf 中精确窗口标题选择主窗口，超时保留同 PID 可见窗口和 marker 诊断，并在根退出后按 5 秒 deadline 等待登记后代自然收敛，超时仍失败且记录精确身份，不以 finally 强杀冒充通过
TDD RED/GREEN：既有 final-real-a 首次真实 RED 为 pending hello 已建立且窗口可见，但 WM_CLOSE 后根进程 10 秒不退出；加入 run_return 后诊断 RED 证明 acceptance.shutdown_requested/cleaned 均不存在，从而定位错误窗口句柄；按精确标题选择后根退出码转为 0，随后即时后代检查 RED 暴露观测竞态；加入有界自然收敛等待后同一脚本完整转绿。所有失败轮均由 finally 精确清场，保留日志并证明 data/ 零变化
自动测试：cargo fmt --check 通过；cargo test --locked -- --test-threads=1 为 63 passed、13 ignored fixture、0 failed；cargo build --locked 与 cargo build --locked --release 均通过；PowerShell parser 通过；git diff --check 通过
故障测试：retryable/non-retryable、最多 3 次自动恢复、250ms/1s/3s backoff、Running 后立即崩溃不补 budget、manual retry、新旧 timer/token/generation 隔离、spawn/hello/初始化占位/backoff shutdown、忽略 shutdown、崩溃后代、Job 建立/恢复失败、重复 stop/finalize/release 全部通过；pending hello 关闭优先于迟到 hello，第三次 backoff 关闭确认 CancelRestart 且旧 timer 不产生 generation
真实应用验收：最终源码与 debug 可执行文件 source freshness 一致；final-accepted-a-20260722 与 final-accepted-b-20260722 两轮各 2/2 场景通过。每轮真实 Tauri 主窗口均可见并由 WM_CLOSE 关闭，pending-hello 与 restart-backoff 根退出码均为 0，后者 timerCancelled=true；两轮分别登记 15/16 个根与后代身份，最终 Tauri/Fake Core 根、WebView/后代、Job、worker、句柄、timer 和系统临时运行目录均为 0
数据门禁：两轮 data-before.json/data-after.json 各为 121 个文件、1,045,983,998 bytes；path/length/mtime/SHA-256 canonical hash 前后均为 300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b，真实 data/ 零变化
实现审查：允许目录与明确禁止目录复核通过；release 不编译 acceptance 模块；未修改依赖、managed_process_tree.rs、legacy Qt、共享 schema 或领域模块；未接入真实 Python Core、业务 IPC 或后续阶段能力；P0/P1 为零，退出条件相关缺陷为零，Critical/Important 审查问题为零
已知限制：debug-only acceptance 桥只验证 Phase 1B 监管和 Tauri 共存边界，将在真实 Core 接线后移除；精确窗口标题由验收脚本与当前 tauri.conf 同步维护，漂移时 source/readiness 门禁会安全失败；真实 Python Core、initialize/Snapshot 和业务 Envelope 仍未实现
独立回退方式：整体 git revert 本 WP accepted 提交，移除恢复策略、扩展 Fake Core 夹具、debug-only acceptance 桥和真实验收脚本，并把 ADR-0001 恢复为 Proposed；保留 WP-1B-01/02/03 的受控进程树、串行 generation 和单次 Fake Core 启停链，不触碰真实 Python、legacy Qt、data/ 或用户进程
关联提交：本 WP accepted 提交（feat(runtime): 完成 Supervisor 恢复与进程泄漏门禁）
```

主要结果：完成 ADR-0001 所需的有限重启、手动重试、竞态和完整故障矩阵。

允许能力：

- restart budget/backoff、不可重试分类占位和手动 retry。
- spawn、hello、运行、停止和 backoff 各阶段的 shutdown/retry 竞态。
- Fake Core 崩溃、卡死、旧 generation 事件和后代忽略退出。
- 真实 Tauri event loop/WebView/window 与 pending hello、重启 backoff、AppShutdown 共存时的窗口退出和进程树零残留；该组合是 Phase 1B 最终实机门禁。

明确禁止：

- 不接入真实 Python Assistant。
- 不以无限重启或额外全局状态绕过竞态。

退出证据：

- ADR-0001 Fake Core 验证矩阵全部自动化或明确记录受限项。
- 重复启停、连续失败和手动 retry 后无进程、句柄和计时器泄漏。
- 自动证据通过后由项目负责人按阶段验收步骤验证真实 Tauri 窗口在 pending hello 与恢复流程中仍可见、可关闭，且关闭后完整 Core 进程树零残留。
- ADR-0001 可以从 `Proposed` 更新为 `Technically Validated`，但仍需实现审查后才进入 `Accepted`。

独立回退：回退恢复策略，保留确定的单次启动和关闭链。

## 6. Phase 1C：最小真实 Core Host

### WP-1C-01：最小无 Qt Python Core Host 与基础握手

激活记录：

```text
状态：active
开始日期：2026-07-22
允许目录：新增 app/core_host/，仅限 stdlib 长度前缀帧、基础 Envelope/错误 DTO、单 writer queue、control dispatcher 与 system.hello/system.health/system.shutdown；新增 tests/unit/test_core_host_protocol.py、tests/unit/test_core_host_import_guard.py、tests/integration/test_core_host_lifecycle.py 及 tests/fixtures/runtime_v2/wp_1c_01/ 隔离故障夹具；desktop/src-tauri/src/core_host_protocol.rs 仅限 Rust 对等 frame codec；desktop/src-tauri/src/core_host_runtime.rs 仅限显式 Python 路径下受控真实 Host 启停与基础握手；desktop/src-tauri/src/managed_process_tree.rs 仅限在既有 suspended spawn + Job Object 原语上增加 stdin/stdout/stderr 匿名管道所有权和显式隔离 current directory；desktop/src-tauri/src/main.rs 仅限模块声明和 debug-only WP-1C-01 真实 Tauri 验收接线；desktop/src-tauri/src/phase_1c_core_host_acceptance.rs 与 desktop/tests/windows_core_host_acceptance.ps1 仅限真实 Tauri + 最小 Core Host 验收；Cargo.toml/Cargo.lock 仅在现有 windows crate 确需启用匿名管道 API feature 时窄改，不增加或升级依赖；ADR-0002 仅追加 WP-1C-01 基础握手证据且保持 Proposed；本文仅更新 WP-1C-01 状态、设计裁定和验收记录
明确禁止目录：main.py、legacy_qt_main.py、start*.bat、app/agent/、app/brain_host/、app/core/、app/plugins/、app/voice/、plugins/、data/、runtime/ 内容、characters/、third_party/、tools/mcp/、desktop/frontend/、共享 schema；不得整体复制旧迁移 R36/R37 或修改旧 BrainHost；不得实现 core.initialize、CoreReadiness/Snapshot、协议兼容诊断、generation credential、stderr 限流/脱敏、并发 pending Router、Operation/cancel、聊天、Assistant、Memory、MCP、插件、Tools、TTS、截图、主动互动、资源描述符或 WP-1C-02 及后续能力
验收环境：当前 Windows 11 23H2 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3；仓库 runtime/python.exe Python 3.12.8 只作为现有测试解释器，不在本 WP 冻结 release/bundled Python 定位规则；不安装或升级依赖；所有真实子进程由独立 Windows Job 管理并使用隔离临时 cwd，hello/shutdown 各 3 秒、完整树停止 5 秒、外层 60 秒 deadline
关联 ADR：ADR-0001（沿用进程树最终停止权和 deadline）；ADR-0002（仅实现基础 framing/control 子集，版本/capability/credential 与 stderr 门禁留在 WP-1C-03，ADR 保持 Proposed）；ADR-0003（不读取或修改共享 data/schema）
计划提交：feat(runtime): 建立最小无 Qt Python Core Host
退出条件：Python/Rust 对等 codec 覆盖分片、合并、非法 UTF-8/JSON、零长/超大/半帧和 EOF；真实 Python Host 在 hello 前 import guard 证明无 PySide6/app.ui/Assistant/Memory/MCP/插件/TTS；stdout 仅有协议帧且污染安全失败；hello、重复 health、未知 control 错误和 shutdown 均在 deadline 内响应；stdin 关闭与 Tauri 主动退出均有界回收完整 Job，无根、后代、pipe、writer/thread、句柄或临时目录残留
故障测试：header/payload 任意分片与多帧合并；非法 JSON/UTF-8、超大长度、半 header/payload、stdout 前缀污染；未知 kind/name、错误 generation、错误 payload；writer queue 关闭/重复 shutdown；stdin EOF；Python 忽略 shutdown 时 Job 强制回收；真实窗口关闭发生在 hello/health 后且不等待人工点击
人工验收步骤：运行有界 debug Tauri + 真实 Core Host 验收脚本，确认真实窗口可见、hello/health marker 已建立、自动 WM_CLOSE 后根退出码 0，并复核 summary 中 Python 根/后代、Tauri/WebView、Job、pipe、writer/thread 和临时目录残留均为 0；自动证据通过后由项目负责人按同一步骤进行独立复验
回退方式：整体 git revert 本 WP accepted 提交，移除 app/core_host、双端 codec、managed process pipe 窄扩展、真实 Host runtime、debug-only 验收桥和脚本，并把 ADR-0002 恢复至本 WP 前记录；保留 WP-1B-01 至 WP-1B-04 的 ManagedProcessTree、Supervisor、Fake Core 与恢复门禁，不触碰 legacy Qt、data/ 或用户进程
```

稳定化记录：

```text
状态：stabilizing
进入日期：2026-07-22
生产实现：新增无 Qt app.core_host，使用 4-byte big-endian + UTF-8 JSON 帧、8 MiB 上限、严格基础 Envelope、稳定错误 DTO、32 项有界单 writer queue 和仅含 system.hello/system.health/system.shutdown 的同步 control dispatcher；Host 捕获二进制 stdout 后安装文本写入 guard，成功路径 stdout 仅产生协议帧
Rust 接入：新增对等 codec 与 CoreHostRuntime；ManagedProcessTree 在保持 CREATE_SUSPENDED、先加入独立 kill-on-close Job 再 ResumeThread 的前提下增加三条匿名管道和显式 current directory，父端句柄禁止继承；Rust 为每个 control response 建立有 deadline 的临时 reader，超时后终止完整 Job并 join reader；正常 shutdown/EOF 后验证 Job 为空、stdout 无尾随污染、读取小型 stderr并显式释放 pipe/进程/Job句柄
Tauri 验收桥：新增仅 Windows debug_assertions 且显式环境门控的 Phase 1C session；普通 debug/release 不自动启动 Python。真实窗口存在期间由 worker 完成 hello 和两次 health，窗口 CloseRequested/ExitRequested/Exit 触发 system.shutdown、完整 Job 验证和 worker join
TDD RED/GREEN：Python 测试先因 app.core_host 不存在而收集失败，最小 Host 后 18 项转绿并修正一个 split==frame length 的测试 oracle；Rust codec 测试先因符号不存在编译失败，最小对等 codec 后 4 项转绿；CoreHostRuntime 测试先因类型不存在编译失败，suspended Job + pipes 接入后真实 Python lifecycle 转绿；随后新增真实 stdout 污染与忽略 shutdown 夹具，分别证明 framing 拒绝和原因码 93/97 完整 Job 强制回收
当前定向结果：Python 19 passed；Rust core_host 9 passed；真实受控 Python 根与故障夹具均按 deadline 退出，定向测试结束后未发现失败
待稳定化门禁：Python import guard 独立复扫、相关 Python 扩展测试、完整 Rust 串行测试、Debug/Release locked build、PowerShell parser、两轮真实 Tauri + Core Host 验收、data/ 完整清单零变化、精确进程/临时目录/句柄复扫、实现审查、ADR-0002 基础证据和独立回退复核
P0/P1：当前定向实现未发现；完整门禁前不得 accepted、不得提交、不得开始 WP-1C-02
```

Accepted 记录：

```text
状态：accepted
验收日期：2026-07-22
修改范围：新增 app/core_host 的 stdlib framing、基础 Envelope/错误 DTO、单 writer queue 和三个 system control；新增 Rust 对等 codec、CoreHostRuntime 和 debug-only 显式环境门控验收桥；managed_process_tree.rs 仅增加匿名 stdio 管道所有权与显式 current directory；Cargo.toml 仅为既有 windows 依赖启用 Win32_System_Pipes feature；新增隔离测试/故障夹具/Windows 真实验收脚本；ADR-0002 仅追加基础 transport 技术证据并保持 Proposed；本文更新 WP-1B-04 表格遗漏和 WP-1C-01 状态/证据
自动测试：Python 19 passed；Rust 完整串行门禁 72 passed、13 ignored fixture、0 failed；cargo fmt --check、Debug/Release cargo build --locked、PowerShell parser、Python py_compile 和 git diff --check 全部通过，构建无警告
故障测试：覆盖任意 header/payload 分片、合并帧、非法 UTF-8/JSON、零长/超大/半帧、stdout 污染、缺失 payload、bool deadline、generation mismatch、未知 control、重复 health、writer queue 重复关闭/迟到写、stdin EOF；忽略 shutdown 夹具在 250ms control deadline 后以原因码 93 强制回收，stdout 污染夹具以原因码 97 强制回收
真实应用验收：首次 final-a-20260722 在 20 秒内未建立 ready marker，脚本 finally 清场后确认 Tauri/Python 进程 0、系统临时目录 0、data 清单不变；增加失败诊断后该超时未再复现，不计入通过证据。最终源码的 final-accepted-a-20260722 和 final-accepted-b-20260722 两轮均 status=passed，真实窗口可见，Tauri 根退出码 0，hello、两次 health、protocol shutdown 成功；每轮登记 9 个进程身份，最终根/后代身份残留 0、系统临时目录残留 0
数据安全：真实 data/ 两轮均为 121 文件、1,045,983,998 bytes；before/after canonical SHA-256 均为 300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b；路径、长度、mtime 和逐文件 SHA-256 完整清单证明零变化
退出条件：无 Qt 最小 Host、双端 framing、基础 hello/health/shutdown、deadline、stdout 安全失败、stdin EOF、完整 Job 回收、真实窗口和数据零变化证据均满足；最终精确复扫 Tauri/runtime Python 匹配进程 0、sakura-runtime-v2-wp-1c-01-* 系统临时目录 0；P0/P1=0，退出条件相关缺陷=0
明确非目标：未实现 core.initialize、CoreReadiness/Snapshot、协议版本/capability 协商、generation credential、持续 stderr 排水/脱敏、并发 pending Router、Operation/cancel、Assistant、聊天、Memory、MCP、插件、Tools、TTS、截图、主动互动、资源描述符、bundled/release Python 定位或 WP-1C-02 及后续能力
已知限制：CreateProcessW 的 stdio 继承依赖父进程其他句柄保持默认不可继承；STARTUPINFOEX handle allowlist、credential 和持续 stderr 排水属于 WP-1C-03。首次 ready 超时未稳定复现，验收脚本已保留 failure-diagnostic.json 以便再次发生时获取 marker、窗口和精确进程身份；最终连续两轮无同类失败
独立回退方式：git revert 本 WP accepted 提交，移除 app/core_host、双端 codec、CoreHostRuntime、managed process pipe 窄扩展、debug-only Phase 1C 验收桥、脚本/夹具和 ADR-0002 本节，并把本文 WP-1C-01 恢复为 planned；保留 WP-1B-01 至 WP-1B-04 的 ManagedProcessTree、Supervisor、Fake Core 与恢复门禁，不触碰 legacy Qt、data/ 或用户进程
负责人门禁：自动真实验收已通过；按项目负责人要求，本 WP accepted 后停止并提供同一脚本的独立实机复验步骤；不得在复验前开始 WP-1C-02
```

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

激活记录：

```text
状态：active
开始日期：2026-07-22
前置提交：eb302748614b785cfdf32f84037b729b9403d1b8（WP-1C-01 accepted）
允许目录：app/core_host/server.py 与 __main__.py 中仅限假组件后台 initialize/readiness/Snapshot 生命周期；tests/unit/test_core_host_readiness.py、tests/integration/test_core_host_lifecycle.py 与 tests/fixtures/runtime_v2/wp_1c_02/ 中仅限本 WP 故障夹具；desktop/src-tauri/src/core_host_runtime.rs 中仅限带 payload 的 lifecycle request、Python Snapshot 只读缓存与 generation 失效；desktop/src-tauri/src/phase_1c_core_host_acceptance.rs、main.rs 和 desktop/tests/windows_core_host_acceptance.ps1 中仅限正常/卡死 initialize 的真实 Tauri 验收；本文仅更新 WP-1C-02 状态和证据
明确禁止目录：Cargo.toml/Cargo.lock 与依赖；core_host_protocol.rs 基础 Envelope/framing；managed_process_tree.rs；main.py、legacy_qt_main.py、start*.bat；app/agent/、app/brain_host/、app/core/、app/plugins/、app/voice/、plugins/、data/、runtime/ 内容、characters/、third_party/、tools/mcp/、desktop/frontend/、共享 schema；协议 major/minor/capability 协商、generation credential、stderr 限流/脱敏、并发 Router、Operation/cancel、Gateway、Assistant、聊天、Memory、MCP、插件、Tools、TTS、截图、主动互动及 WP-1C-03 或后续能力
验收环境：当前 Windows 11 23H2 x64；x86_64-pc-windows-msvc；Rust/Cargo 1.96.0；Tauri 2.11.3；仓库 runtime/python.exe；不安装或升级依赖；所有测试使用隔离临时目录和明确 deadline；initialize 接受不超过 5 秒，readiness watchdog 30 秒，shutdown 3 秒，完整树停止 5 秒，外层真实验收 60 秒
关联 ADR：ADR-0001（initialize/shutdown deadline 与旧树清理）；ADR-0002（先 transport 后后台 initialize、Python 构造 Snapshot、Rust 只读缓存和 generation 隔离）；ADR-0003（不读取或修改共享 data/schema）
计划提交：feat(runtime): 建立 Core 初始化就绪与最小快照
退出条件：hello 不等待 initialize；initialize 快速接受或确定性拒绝；后台初始化不阻塞 health/shutdown；卡死初始化时真实 Tauri 窗口可见且可有界关闭；Snapshot 仅由 Python 构造并带 schemaVersion/generationId/generationNumber/revision/readiness/components/capabilities；Rust 不推导或改写业务字段；新 generation 建立时旧 Snapshot 立即清空；两轮真实验收证明 data/ 零变化且进程、Job、pipe、writer/init thread、计时器和临时目录零残留
故障测试：重复 initialize；非法 initialize payload；ready/setup_required/degraded/failed/hang 假模式；initialize 与 shutdown 竞态；卡死时重复 health；旧 generation Snapshot 拒绝；revision 单调；新 generation 缓存清空；stdin EOF；重复关闭和多轮执行
已知风险：本 WP 只冻结最小 lifecycle Snapshot，Phase 2 revision gap/事件/资源 token 尚未实现；真实 readiness 仍是假组件，不代表 Assistant 可用
独立回退方式：整体 git revert 本 WP accepted 提交，恢复 WP-1C-01 的 hello/health/shutdown Host、Rust runtime 和真实验收；保留基础 framing、受控进程树、Supervisor 与 Fake Core，不触碰 legacy Qt、data/ 或用户进程
```

Accepted 记录：

```text
状态：accepted
验收日期：2026-07-22
修改范围：Python Host 新增 generation number、假组件后台 initialize、六态 CoreReadiness、revisioned 最小 Snapshot、重复 initialize 合并和可取消 initialize worker；Rust 新增带 payload lifecycle request、Python Snapshot 严格只读缓存和 begin_generation 立即清空；真实验收桥增加 ready/hang 模式、Snapshot 证据和 Win32 窗口诊断；Windows 验收把 WebView2 user data folder 放入每轮受控临时目录，并区分运行时读取的 Python 源码与决定 EXE 新旧的 Rust 编译源码。未修改 main.rs 产品窗口路径，未导入或接入 Assistant、Memory、MCP、插件、Tools、TTS 或其他领域模块
根因与修复：旧验收只隔离 Core runtime，WebView2 仍复用真实用户 LocalAppData 下的共享 EBWebView profile。连续启动和失败清场后，下一轮 WebView2 环境可能停在仅有 Tao Thread Event Target 与浏览器子进程、尚未建立 Tauri Window 的状态，使 Core 已 ready 或保持 initializing 的正确结果被误判为 Shell 失败。脚本现为每轮设置 WEBVIEW2_USER_DATA_FOLDER=<受控 runtime>/webview2-user-data，硬断言该目录已由 WebView2 创建，并在完整进程树退出后由既有安全边界一并删除；不再依赖或修改共享 WebView2 profile。源码新旧检查只用 Rust 编译输入判断 EXE，Python 实时源码仍进入证据清单，避免 cargo 正确 no-op 后被 Python mtime 误报为旧 EXE
TDD RED/GREEN：Python 新测试首次为 11 failed、6 passed，缺口精确落在 generation number、core.initialize/core.snapshot、readiness、非法 payload 拒绝和 worker 清理；Rust 新测试首次编译失败 5 项，缺失 CoreSnapshotCache、request_with_payload、refresh_snapshot 和 cached_snapshot；最终 Python Core Host 定向 30 passed，Rust 完整串行门禁 75 passed、13 ignored fixture、0 failed
自动测试：cargo fmt --check、Debug/Release cargo build --locked、PowerShell parser 和 git diff --check 全部通过，构建无警告。仓库全量 Python 扩展门禁为 1492 passed、3 skipped、15 failed；15 项均属于本 WP 禁止修改且当前 diff 未触碰的 legacy main.py/Qt 单实例旧测试，未发现 Runtime v2 定向回归
故障与竞态：ready/setup_required/degraded/failed/hang、非法/未知 initialize payload、重复 initialize、hung initialize 下重复 health、initialize/shutdown、stdin EOF、Snapshot generation mismatch、新 generation cache 清空和旧 Snapshot 拒绝均由 Python/Rust 可执行测试通过；hung initialize 可在 3 秒协议期和 5 秒完整树门内正常退出且不强杀
历史失败证据：round-a-hang、round-a-hang-retry、hang-diagnostic、ready-after-failures、hang-window-class、final-a-ready、explicit-show-diagnostic、ready-event-hang 中 Core 多次已建立 hello/initialize/snapshot，失败轮只观察到 Tao 内部事件窗口。对 main.rs 的两项强制显示诊断实验无效并已撤回，证明不应修改产品窗口生命周期掩盖验收环境泄漏
真实应用验收：带隔离创建硬断言的 final-isolated-round-1-ready、final-isolated-round-1-hang、final-isolated-round-2-ready、final-isolated-round-2-hang 使用同一 debug EXE SHA-256 c2f219eaad8877a74fce61f723441cbe82dc4f8a6dfb63fc4bb6c506dbe3e6ee，连续两组均 status=passed 且 webViewUserDataCreated=true；四轮真实 Tauri Window 可见、根退出码 0、hello/initialize/两次 health/protocol shutdown 成功，ready Snapshot 为 ready，hang Snapshot 保持 initializing
数据与资源安全：最终四轮真实 data/ 均为 121 文件、1,046,428,570 bytes，before/after canonical SHA-256 均为 e4982a382a9668de39276ce1d3203d9f47c18b69e9b1aef8787ffbd1eb0fe1ca，路径、长度、mtime 和逐文件 SHA-256 零变化；每轮登记 9 个进程身份且最终残留 0，受控 runtime 与隔离 WebView2 user data 目录残留 0
退出条件：hello 不等待 initialize；后台初始化不阻塞 health/shutdown；卡死初始化时真实窗口可见且可有界关闭；Python 独占构造 Snapshot，Rust 只读缓存并在新 generation 立即清空；P0/P1=0，退出条件相关缺陷=0
已知限制：本 WP 的组件初始化仍是假模式，Phase 2 revision gap/事件/资源 token 和真实 Assistant readiness 尚未实现；协议协商、generation credential 与持续 stderr 排水属于 WP-1C-03
独立回退方式：git revert 本 WP accepted 提交，恢复 WP-1C-01 的 hello/health/shutdown Host、Rust runtime 和真实验收；保留基础 framing、受控进程树、Supervisor 与 Fake Core，不触碰 legacy Qt、data/ 或用户进程
负责人门禁：自动真实验收已通过；本 WP accepted 后停止并提供同一脚本的独立实机复验步骤，不在本次继续 WP-1C-03
关联提交：a06e1dada66b02474f3d65d4124f31094cda5e9e（feat(runtime): 建立 Core 初始化就绪与最小快照）
```

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

## 7. Phase 1P：跨平台基础回补

Phase 1P 是 2026-07-22 架构审查后的强制纠偏阶段。它保留 WP-1A、WP-1B、WP-1C-01/02 的 Windows 证据，把现有 Win32 实现变成 Windows backend，并在任何后续 Runtime 或产品能力前补齐 macOS/Linux。规范来源为 ADR-0004。

### WP-1P-01：跨平台 target、接口与错误分类冻结

主要结果：冻结 Windows x64、macOS arm64、Linux x64 的最低环境、Rust target、平台服务接口、稳定错误类别和证据责任。

允许能力：

- 新增平台矩阵、接口和错误分类 ADR/spec；建立 compile-only skeleton 和测试 contract。
- 定义 `InstanceLockBackend`、`ManagedProcessTreeBackend`、`WindowInteractionBackend`、`RuntimeLocator`、`NativeDiagnosticsBackend` 的所有权与调用方向。
- 盘点现有 Windows 实现如何无语义变化地成为 backend。

明确禁止：

- 不实现具体 macOS/Linux backend，不接入 WP-1C-03 或产品领域能力。
- 不重写 Supervisor、generation、framing、CoreReadiness 或 Snapshot。
- 不把平台差异加入 IPC 业务字段。

退出证据：三平台 target 和最低环境明确；公共层/平台层依赖图、稳定错误表、CI/真实验收责任和逐文件迁移清单完成审查；所有引用存在；Windows backend 迁移不改变现有产品语义。

独立回退：回退接口冻结文档和无行为 skeleton，恢复 WP-1C-02 accepted 提交态，不触碰其实现。

### WP-1P-02：三平台 RuntimeLocator 与 bundled Python 布局

主要结果：公共启动链不再依赖 `.exe`、仓库 `target/debug` 或硬编码 `runtime/python.exe`，三平台开发/测试/发布布局可重复定位。

允许能力：

- 实现 `RuntimeLocator`、平台资源根、Python/Core 路径、架构检查和结构化错误。
- 冻结 Windows、macOS app bundle/sidecar、Linux 发布包的 Python 来源、布局、完整性和 golden fixtures。
- 更新默认入口与验收 harness 使用 locator；开发模式只能显式选择仓库 Runtime。

明确禁止：

- 不静默回退 PATH/system Python，不在线下载或自动修复 Runtime。
- 不启动真实 Assistant，不改 IPC、Snapshot 或共享数据 schema。

故障测试：Runtime 缺失、无执行权限、错误 CPU 架构、损坏入口、资源根移动、开发/发布模式混淆、macOS bundle 与 Linux 包路径异常。

退出证据：三平台 compile/test 通过；每个平台从 golden 发布布局定位唯一受控 Python/Core；普通公共代码没有 `.exe` 或 Windows 仓库路径假设；错误可进入 diagnostics。

独立回退：回退 locator 与布局 fixture，保留 WP-1P-01 接口和既有 Windows 显式路径测试。

### WP-1P-03：Windows/POSIX 共享应用锁 backends

主要结果：Windows named mutex 与 macOS/Linux advisory lock 实现同一 `sakura.desktop.shared-user-data.v1` 语义，Rust/Tauri 与 legacy Python 双向互斥。

允许能力：

- 保留现有 Win32 mutex 为 Windows backend。
- 为 macOS/Linux 实现同路径、同打开模式、同 advisory lock 语义的 Rust/Python backends。
- 增加平台锁路径 golden fixture、冲突/权限/fatal 错误和正常/强杀释放测试。

明确禁止：

- 不用普通文件存在、PID 文本或 stale 猜测代替 OS lock。
- 锁文件不得位于共享 `data/`；冲突入口不得先写日志、配置或 migration。

退出证据：三个平台分别通过 Qt 持锁/Tauri 冲突、Tauri 持锁/Qt 冲突、正常释放、强杀释放、API/权限失败、无人持锁但文件存在和获取前数据零变化；ADR-0003 更新平台证据但保持数据兼容总门未完成。

独立回退：回退 POSIX backends 和公共适配，恢复 Windows backend；不得删除真实锁文件或用户数据。

### WP-1P-04：Windows/macOS/Linux 受控进程树 backends

主要结果：保留 Windows Job Object，补齐 macOS/Linux session/process group、整树终止、wait 验证和身份安全边界。

允许能力：

- 把现有 `ManagedProcessTree` 的 Win32 资源移入 Windows backend，公共 API 保持 Supervisor 所需语义。
- 实现 macOS/Linux spawn、piped spawn、terminate tree、wait、verify exited 和 release。
- Linux 可增加 parent-death signal 保险，但组级最终停止权仍属于 Tauri。

明确禁止：

- 不改 Supervisor 状态机、restart budget、generation 语义和 IPC Envelope。
- 不以 `cfg(not(windows)) => UnsupportedPlatform` 作为正式 backend。

故障测试：spawn/组建立失败、根提前退出、一个/多个后代忽略 shutdown、旧组停止中 retry、PID/PGID identity 变化、重复 terminate/wait/release、Tauri 强杀和父进程异常退出。

退出证据：三平台 Fake Core 与真实 Python 根均证明旧树清理后才能新建 generation；Tauri 退出后根、后代、pipe、handle/fd 和临时目录零残留；ADR-0001 增补跨平台证据。

独立回退：回退 POSIX backends 和公共适配，保留 Windows Job、Supervisor 与 Fake Core 历史实现。

### WP-1P-05：三平台窗口交互、IME 与原生诊断 backends

主要结果：共享布局/命中模型继续复用，Windows、macOS、X11/Wayland 使用明确 backend 完成透明窗口、拖动、焦点、IME、scale 和窗口身份诊断。

允许能力：

- 保留 Win32 region/move loop 为 Windows backend。
- 实现或技术验证 macOS 原生命中与拖动、Retina、多屏、Spaces 和 IME。
- 分别实现/验证 Linux X11 与 Wayland；补齐能识别真实 Sakura 主窗口的原生诊断。
- 若单窗口在某平台失败，只能在同一 Tauri App 内提出受控多窗口替代并先更新 ADR。

明确禁止：

- 不静默关闭点击穿透、拖动、IME、焦点恢复或显示隐藏。
- 不引入隐藏 Qt、第二桌面生命周期根或管理员权限依赖。
- 不接入聊天和最终视觉重做。

退出证据：三平台真实 Shell 可见且可关闭；目标 scale/DPI、多屏、负坐标、拖动、IME、Alt+Tab/Spaces/desktop、显示隐藏和失败安全恢复通过；Linux 证据明确 X11/Wayland，不把 compositor/Tao 内部窗口当主窗口。

独立回退：按 backend 独立回退平台实现，保留共享纯布局模型和已经验证的 Windows backend。

### WP-1P-06：三平台最小 Shell + Core lifecycle 和 CI 总门禁

主要结果：把 WP-1P-02 至 05 组合为持续门禁，三个平台都能从正式 locator 启动 Shell 与最小 Core 并完成有界关闭。

允许能力：

- 建立 Windows/macOS/Linux CI、平台测试脚本、golden fixtures 和有界真实验收入口。
- 组合共享锁、RuntimeLocator、窗口 backend、受控进程树和 WP-1C-02 的 hello/initialize/health/Snapshot/shutdown。
- 记录平台工具链、WebView、Python、CPU、session/compositor 和包布局。

明确禁止：

- 不接入 WP-1C-03 协议扩展、真实 Assistant、聊天或 Phase 2+ 能力。
- 不以 compile-only、mock、单元测试或 Windows 结果替代另一个平台的真实生命周期证据。

故障测试：Runtime 缺失、锁冲突、spawn/pipe 失败、hello/initialize 卡死、损坏 stdout、忽略 shutdown、根崩溃并遗留后代、窗口关闭、重复两轮和失败后恢复。

退出证据：三个平台连续两轮完成 `Shell visible -> lock held -> Core hello/initialize/health/Snapshot -> protocol shutdown -> full tree exited -> lock reacquirable`；无根、后代、pipe、fd/handle、timer、WebView profile 和临时目录残留；真实用户数据零变化；ADR-0004 更新为 `Technically Validated`。

独立回退：回退组合门禁和 CI 接线，保留每个平台已独立验证的 backend；WP-1C-03 继续保持 planned。

## 8. Phase 1C 续：协议与 bundled Runtime 冻结

### WP-1C-03：协议协商、stderr 排水和故障 transport

主要结果：建立 Desktop/Core/Protocol 版本协商、日志排水和真实 transport 故障边界。

强制前置：WP-1P-06 accepted。三平台必须使用相同 framing、Envelope、generation credential、capability 和错误语义；平台差异只允许位于已验证 transport/process backend。

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

强制前置：WP-1C-03 accepted；必须通过 WP-1P-02 的三平台 `RuntimeLocator` 和 WP-1P-04 的进程树 backend，不得恢复硬编码 `runtime/python.exe`。

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

独立回退：恢复到 WP-1P 已验证的开发 RuntimeLocator/Fake Core 路径，不影响 legacy Qt；不得回退为 Windows-only 硬编码路径。

## 9. Phase 1D：恢复、诊断和修复入口

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

## 10. Phase 2：并发 IPC 与只读快照

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

## 11. Phase 3：基础聊天垂直链

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

## 12. Phase 4–7 强制 Work Package

以下编号、顺序、主要结果和等价范围已经冻结，但仍是 `planned`。每个 WP 进入 `active` 前必须补充逐文件允许目录、平台环境、协议字段、故障矩阵、人工步骤和独立回退；不得把表中相邻行合并成一次“大迁移”。每行对应的详细现有能力与发布状态见产品功能等价台账。

### Phase 4：Assistant 辅助能力等价

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-4-01 | CAP-008 | Memory 检索、写入、整理、外部存储与降级 | 聊天不因 Memory 失败不可用；Qdrant/SQLite/模型资源可回收；三平台数据兼容 |
| WP-4-02 | CAP-009/010 | 内置 Tools、Operation、Action ID 确认 | WebView 不能伪造执行参数；取消/超时唯一终态；副作用有确认证据 |
| WP-4-03 | CAP-011 | MCP 配置、启动、工具调用与恢复 | MCP 进程属于当前 generation；崩溃/超时/退出零残留；凭据不泄漏 |
| WP-4-04 | CAP-012 | 现有插件 context/event/tool 和私有数据等价 | 插件加载/卸载、错误隔离、反向清理和数据兼容通过 |
| WP-4-05 | CAP-013/014 | TTS、播放、设备错误与 audio ADR | 三平台真实音频设备；服务/模型子进程回收；播放失败不拖垮聊天 |
| WP-4-06 | CAP-015 | 手动截图、资源 token 与平台权限 | 多屏/DPI、macOS 权限、X11/Wayland portal、token 失效通过 |
| WP-4-07 | CAP-016/017 | 自动观察、主动互动、提醒、任务调度 | 时区/休眠恢复、重复事件、取消、数据持久化和截图权限通过 |
| WP-4-08 | CAP-008–017 | Phase 4 组合稳定化 | 长任务不阻塞 control；Memory/MCP/plugin/TTS/screenshot 全资源零残留 |

### Phase 5：配置、平台桌面能力与桥接等价

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-5-01 | CAP-018/019 | `core.*`/`desktop.*`/`ui.*`/`audio.*` 仓库、validate、change plan、原子保存 | 失败不产生半更新；Qt 可读数据不破坏；三平台路径/权限通过 |
| WP-5-02 | CAP-020 | 设置窗口、逐域结果和首次设置 | 键盘/IME/焦点、密钥输入、失败恢复和重新打开状态一致 |
| WP-5-03 | CAP-006/007/021 | 角色切换、Session、历史分页与受控重启 | 旧 generation/资源失效；角色、历史、Memory/TTS scope 不串线 |
| WP-5-04 | CAP-022 | 托盘、置顶、快捷键、显示隐藏、开机启动 | Windows/macOS/Linux 原生行为、权限和重复注册/卸载通过 |
| WP-5-05 | CAP-023/024 | 浏览器自动化与移动/本地桥接 | 浏览器树受控；端口/防火墙/鉴权安全；插件不拥有第二生命周期根 |
| WP-5-06 | CAP-025 | 扩展诊断、Repair、安全重试、更新前置检查 | 三平台路径/日志/权限诊断；无自动下载；失败后仍可退出/回退 |

### Phase 6：角色 Studio 等价

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-6-01 | CAP-026 | Workspace/Draft 独立模型 | 草稿与运行数据隔离；schema/崩溃恢复通过 |
| WP-6-02 | CAP-026/027 | 角色导入、资源和 schema 校验 | ZIP 路径安全、超大资源、错误编码和跨平台路径通过 |
| WP-6-03 | CAP-026 | 预览与运行中 Assistant 隔离 | 预览不污染当前 generation、Memory、历史、插件和 TTS |
| WP-6-04 | CAP-027 | 原子保存、发布和回滚 | 中断/磁盘失败/校验失败保留旧版本；Qt/Tauri 都可读取 |
| WP-6-05 | CAP-027 | 大文件 Operation、取消和故障恢复 | progress 背压、取消、重启、临时文件和资源回收通过 |

### Phase 7：三平台发布与功能等价总门禁

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-7-01 | 全部 | 完整 Python/Rust/前端/协议和三平台 CI | required checks 全绿；无绕过或把 GUI crash 误判通过 |
| WP-7-02 | CAP-001–029 | 三平台真实 Tauri WebView E2E | 平台 UI、IME、scale、多屏、托盘、音频、截图真实验收 |
| WP-7-03 | CAP-001–030 | 产品功能等价与数据兼容总审查 | 台账逐行 parity-accepted/获批替代；Qt -> Tauri -> Qt 通过 |
| WP-7-04 | CAP-028 | 打包、签名/notarization、更新、完整性、干净安装 | 三平台正式工件从零安装/更新/回退，包内 Python 唯一且受控 |
| WP-7-05 | CAP-029 | soak、休眠恢复、重复启停和故障注入 | Core/MCP/TTS/browser/更新链无泄漏、死锁、数据损坏 |
| WP-7-06 | 全部 | 最终发布审查与进入 `dev` 决策 | P0/P1=0；回退演练完成；项目负责人明确批准合并/发布 |

## 13. 当前启动点

`WP-0-01` 至 `WP-1C-02` 已登记 accepted；WP-1C-02 对应提交为 `a06e1dada66b02474f3d65d4124f31094cda5e9e`。

下一项且唯一允许激活的生产 Work Package 是 `WP-1P-01`。`WP-1C-03` 及全部后续 WP 在 `WP-1P-06` accepted 前保持 `planned`，不得继续 Windows-only 扩张。
