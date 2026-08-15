---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# Sakura Runtime v2 交付治理与防扩张约束

> 本计划是 Runtime v2 早期交付治理的历史资料。自 2026-08-15 起，Work Package 只作为路线图，开发与
> 验证采用 [ADR-0021](../../../adr/0021-product-harness-outcome-verification.md) 的 Product Harness 原则；
> 本文不再授权、禁止或门禁当前代码修改。

> 状态：Phase 0 最终审查通过 / Runtime v2 强制治理基线
> 工作分支：`refactor/tauri-runtime-v2`
> 开发模式：个人开发，直接在当前分支提交，不创建 Work Package PR
> 适用范围：Runtime v2 Phase 0–7 的计划、实现、验证、提交和稳定化

本文件约束 Runtime v2 的实施方式，其优先级不低于主计划和技术 ADR。任何实现即使技术方向正确，只要违反本文件约束，也不能进入下一 Work Package 或最终合并。

## Work Package 定义

Work Package 是 Runtime v2 最小的、可独立验证和回退的实施单元：

- 必须属于一个明确 Phase。
- 必须只服务一个主要结果和一组相关退出条件。
- 可以包含多个按顺序提交的单一目的 commit，但不能混入下一 Work Package 的生产实现。
- 状态只使用 `planned`、`active`、`stabilizing`、`accepted`。
- 同一时间最多一个 Work Package 处于 `active` 或 `stabilizing`。

产品 Work Package 推荐编号格式：`WP-1A-01`、`WP-1B-02`。插入产品序列、直接约束后续产品开发的
仓库基础设施包可以使用独立 `WP-H-01` 命名空间，但必须登记在同一状态总表并占用同一个
`active/stabilizing` 槽位，不能借“非产品代码”建立第二条并行实施序列。

Runtime v2 Phase 0–7 的 Work Package 顺序、状态和范围统一登记在：

- `docs/plans/runtime-v2/work-packages.md`

主计划不重复维护 Work Package 状态。提交正文只记录当前 Work Package 和实际验证结果，避免形成多个互相冲突的状态源。

## G-001：单 Work Package 开发限制

同一时间只允许一个 Runtime v2 Work Package 处于功能开发或稳定化状态。

插入 Runtime v2 顺序的 Harness、CI 或仓库治理 Work Package 同样受本规则约束。另一个 WP 仍为
`active` 或 `stabilizing` 时，只能准备 ADR、Spec、Plan 和任务契约草案，不能激活或提交基础设施生产实现。

前一个 Work Package 未满足退出条件、Bug Budget 和稳定化检查前，不得开始后续 Work Package 的生产代码。允许提前进行文档讨论、只读调研和不进入生产分支的技术记录，但不得提前提交未来阶段实现。

不得以“顺手修改”“未来一定需要”或“减少以后返工”为理由跨阶段开发。

## G-002：阶段允许列表

每个 Work Package 开始前必须记录：

- 所属 Phase 和 Work Package 编号。
- 本阶段允许修改的能力。
- 允许修改的目录或模块。
- 明确禁止实现的能力。
- 退出条件。
- 故障测试。
- 人工验收步骤。
- 独立回退方式。

不属于当前允许列表的生产改动默认视为范围扩张，必须移出当前 Work Package。

阶段限制至少包括：

```text
Phase 1A：
允许空 Shell、透明窗口、锚点、命中区域、DPI、IME、焦点、
v2 开发入口、legacy Qt 启动脚本和 Qt/Tauri 共享应用锁。
禁止接入 Python Core、聊天、设置和真实 Assistant。
一个原生透明窗口是首选技术方案；真实命中/IME/focus 门失败时停止，
不得以未批准的多窗口兼容层继续入口切换。

Phase 1B：
允许进程监管、Windows 受控进程树、最小测试 transport、Fake Core、
退出、重启、restart budget、竞态和进程树清理。
禁止接入真实 Assistant 领域服务。

Phase 1C：
允许最小 Core Host、hello、health、initialize、snapshot、shutdown、
generation 隔离、协议版本/capability 协商和 stderr 排水。
禁止接入聊天、Memory、插件、MCP、Tools 和 TTS。

Phase 1P：
允许平台接口、RuntimeLocator、三平台 bundled Python 布局、共享应用锁、
受控进程树、透明窗口/命中/拖动/IME backend、原生诊断和三平台 CI。
禁止接入 WP-1C-03 协议扩展、聊天或任何产品领域能力；
现有 Windows 实现保留为 backend，不借平台化重写 Supervisor/IPC 语义。

Phase 1D：
基础聊天前只允许 startup、initializing、ready、failed、Core crashed、retry、
脱敏 diagnostics 文本和 exit；这些状态直接消费已提前完成的 WP-3-01 真实 readiness。
禁止完整 Runtime Repair 页面、自动修复、在线下载/替换、通用日志浏览平台。

Phase 2：
只允许首条聊天需要的独立 reader/writer、pending map、event/response 交错、
control 隔离、有界队列、聊天取消/唯一终态、固定 Gateway allowlist 和最小 Snapshot。
禁止完整三级业务优先级、通用 Operation/worker process/resource token、
完整 Snapshot component model、所有未来权限注册和多等级背压平台。
禁止迁移设置、TTS、Tools、工作室和其他产品功能。

Phase 3：
允许先冻结最终产品 UI 外壳，再迁移基础聊天：真实角色立绘、常驻气泡与输入框、
打字机、右键菜单、同一 Tauri App 内的普通设置窗口宿主、角色包可见表现能力、
Core 恢复和 legacy Qt → Tauri v2 → legacy Qt 数据兼容门禁；必须以真实 Sakura
Assistant 完成 Architecture Validation Slice，不得用 Fake Core 代替。
WP-3-03 可以使用确定性 Fake Core 驱动表现状态，但正常验收必须读取真实角色包，
不能继续使用自绘测试立绘或把可折叠工具栏当作产品 UI。
WP-3U-01/02 只允许提前接入设置窗口宿主、角色与外观相关的窄配置；未迁移能力的
设置页必须隐藏或明确禁用，不能伪装为已经可用。禁止借此提前迁移 TTS、Memory、
Tools、MCP、插件、截图、主动观察、完整首次设置或工作室。
```

显式顺序例外：WP-3-01 在 WP-1C-04 后立即执行，早于 Phase 1D/2。其允许范围只有无 Qt Assistant Adapter、当前角色/Session/基础 Provider 构造和真实 readiness；不得借提前执行接入聊天、Operation、Tools、Memory、MCP、插件或 TTS。这样先产生真实产品消费者，再由后续最小 IPC WP 服务它。

Phase 4–7 必须按 Work Package 清单和产品功能等价台账逐项激活，不得把多个既有产品能力合并成一次“大迁移”。

进入 WP-1P-02 前必须固定 Windows x64、macOS arm64、Linux x64 的 Rust target、WebView、构建工具、包格式和 bundled Python 来源。可漂移的 `stable`、未锁定依赖或“使用本机已有版本”不能作为可重复验收基线；某工具当前不属于 Work Package 前置时，应明确写为非前置，而不是隐式安装或扩大范围。

## G-003：可选范围变更 one-in, one-out

阶段开始后，新增的可选产品能力不能直接叠加到原范围。新增一项同等级可选能力时，必须：

1. 明确说明新增原因。
2. 更新允许列表和退出条件。
3. 明确延期或删除另一项同等级可选工作。
4. 获得项目负责人批准。

以下内容不参与 one-in, one-out，也不得为了腾出范围而删除：

- 数据安全和兼容修复。
- 崩溃、死锁、进程泄漏和无法退出修复。
- 当前退出条件必需的测试和诊断。
- ADR 已声明的安全边界。
- 为恢复原定业务语义所需的缺陷修复。

新增必要修复导致工作量明显变化时，应暂停并重新拆分 Work Package，而不是牺牲可靠性门禁。未获批准的新增产品需求只能进入 backlog。

## G-004：Commit 单一目的

本项目不为 Work Package 创建 PR。PR 单一目的原则改为 commit 和 Work Package 单一目的原则。

每个 commit 必须有一个主要目的，并能用一句话描述，例如：

- 建立不启动 Python 的最小 Tauri Shell。
- 为 Fake Core 增加 Windows Job Object 回收。
- 实现最小 `system.hello` 握手。
- 增加透明窗口 DPI 验收。

禁止：

- 一个 commit 完成 Runtime v2 基础架构。
- 同时实现 Supervisor、IPC、聊天和设置。
- 重构 Core 并顺便迁移前端。
- 一次性移植 #140 的相关模块。

一个 commit 原则上不得同时重构 Rust、Python 领域逻辑和 WebView UI。明确的垂直功能 commit 可以触及多层，但必须包含真实端到端验收，且各层变化只能服务同一结果。

出现以下情况时必须拆分 commit 或 Work Package：

- 无法在一次审查中解释所有状态变化。
- 同时引入多个互不依赖的新抽象。
- 同时改变协议、业务语义和 UI 行为。
- 无法提供单一回滚路径。
- 测试失败时无法判断属于哪一层。

提交标题使用简洁中文 Conventional Commit，提交正文详细记录：

```text
type(scope): 一句话说明可验证结果

Phase / Work Package:
- Phase 1A / WP-1A-01

背景与原因:
- 为什么当前阶段必须做

主要变更:
- 实际改变了什么

明确非目标:
- 本提交没有实现什么

验证:
- 自动测试
- 故障测试
- 真实应用和人工验收

风险与回退:
- 已知风险
- 独立回退方式
```

## G-005：禁止投机实现

未来可能需要的能力只允许保留最小接口、ADR 或 backlog，不允许提前完成实现，包括但不限于：

- Agent Runtime。
- Named Pipe 或 Unix Domain Socket。
- 已批准 target matrix 之外的新 CPU 架构或额外打包格式。
- 自动 Runtime 修复。
- 完整 schema 代码生成平台。
- Capability Broker。
- 面向未来业务的完整通用 Operation、任务图和 worker process 框架。
- 基础聊天尚未消费的通用 resource token、完整 Snapshot component model 和多等级背压平台。
- 跨进程分布式设置事务。
- 真实 token streaming。
- 高级动画引擎。

只有当前阶段的真实需求无法在不实现该能力的情况下完成时，才能重新申请加入允许列表。

抽象原则：

- 一个当前消费者时优先使用简单接口；Fake Core、测试 fixture 和未来计划不算真实消费者。
- 出现第二个真实消费者，并证明存在相同所有权/故障语义后再提取通用抽象。
- 不为假设中的未来消费者建设框架。
- framing、安全失败、generation、credential、生命周期控制、进程安全、数据安全和平台清理可以按已批准 ADR 提前冻结必要边界。
- 通用 Operation、资源平台、业务优先级、通用 Snapshot/未来消费者模型不能仅以“协议边界”为理由提前完整实现。

## G-006：Python 领域代码冻结原则

Runtime v2 首轮迁移中，现有 Python Assistant 领域代码默认只读。

只有至少满足以下一项真实迁移需要时才能修改：

1. 直接依赖 Qt。
2. 无法运行在受监管子进程中。
3. 阻塞控制通道、取消或关闭。
4. 初始化和释放行为不可确定。

此外，每个领域模块修改必须有测试证明修改前后业务语义等价。确需改变业务语义时，必须作为独立范围说明并获得项目负责人批准。

每个修改现有领域模块的 commit 必须说明：

- 为什么 Adapter/Facade 无法解决。
- 修改前后的业务语义是否变化。
- 对 legacy Qt 回退入口是否有影响。
- 使用什么测试证明没有顺手重写领域逻辑。

## G-007：#140 代码逐文件准入

禁止整提交 cherry-pick 或整体应用 #140。

每个准备复用的文件或模块必须单独记录：

- 复用原因。
- 原始依赖。
- 是否携带旧生命周期假设。
- 是否携带旧状态所有权。
- 需要保留和删除的部分。
- 对应测试。
- 失败时的替代方案。

没有经过准入审查的 #140 代码不得进入 Runtime v2。旧迁移固定以 `feat/tauri-assistant-migration` 的 `190dfafd24f5c5226bff8b4347837b6e45d9a331` commit 作为只读取证和选择性复用来源，不整体 cherry-pick、恢复或复制到当前工作区。

## G-008：证据优先的退出门禁

Work Package 完成不能以“代码已经写完”或“单元测试数量足够”为依据。

每个退出门至少需要：

1. 与改动直接相关的自动化测试。
2. 真实应用冒烟测试。
3. 已知问题清单。
4. 可重复的验收步骤。
5. 回退验证或明确回退步骤。

再根据改动类型追加证据：

- 窗口/UI：对应正式平台的真实 Tauri WebView、DPI/scale、多屏和 IME 验收；Linux 必须区分 X11/Wayland。
- 进程监管：对应正式平台的 Fake Core、真实后代进程、重复启停和强制回收。
- IPC：阻塞、取消、乱序、慢 writer、背压、关闭和协议损坏。
- 用户数据：对应正式平台的共享锁、备份恢复、异常中断和 Qt → Tauri → Qt 兼容门禁。
- 用户体验：真实主题、输入、焦点和错误恢复路径。
- Runtime/打包：开发与发布 locator、包内 Python、CPU 架构、签名/权限和干净环境。

不涉及某类能力的 Work Package 不强制重复无关验收，但不得跳过受影响层级的证据。平台敏感 Work Package 在单个平台通过时只能记录该 backend 的证据，不能据此标记为全局 `accepted`。

成功证据按“实现输入是否变化”复用，而不是按流程阶段机械重跑。已经由相同生产代码、依赖闭包、
fixture 和平台契约证明的层级，不因进入 `stabilizing`、文档状态更新或再次审查而失效。项目负责人
可以在没有已知 P0/P1、没有已知退出条件失败、且未放宽安全契约时，明确接受尚未补齐的非失败型
CI/设备证据风险；该决定必须记录具体缺口和回退方式，后续监控失败只有在可复现且可归因于候选
实现时才重新打开责任 Work Package。

## G-009：阶段 Bug Budget

严重级别定义：

- P0：数据损坏、凭据泄露、安全边界绕过或不可逆用户状态破坏。
- P1：崩溃、死锁、进程泄漏、无法退出、无法恢复、控制通道失效或核心链路不可用。

进入下一个 Work Package 前必须满足：

- 已确认的 P0 为零。
- 已确认的 P1 为零。
- 当前 Work Package 退出条件相关 bug 为零。
- 已知中低优先级问题已记录影响、复现方式和计划处理阶段。
- 不允许用下一阶段功能掩盖当前阶段缺陷。

P2/P3、代码风格建议、未来增强和无法复现或无法归因的推测性风险不阻塞 `accepted`，应进入明确
backlog。CI runner、网络或已知并发抖动只允许同 SHA 重跑或记录环境限制，不授权修改产品代码，
也不触发 whole-WP review。

发现结构性问题时暂停新增功能，当前 Work Package 转入 `stabilizing`。

## G-010：强制稳定化检查点

每个 Work Package 完成生产实现后必须进入独立 `stabilizing` 状态，不立即开始下一功能。

`stabilizing` 是一次有界候选验收，不是独立 reviewer 或重复 whole-WP review 阶段，不设最短持续
时间。候选证据满足、无可归因 P0/P1/退出条件缺陷，或项目负责人按 G-008 明确接受剩余证据风险
后，可以在同一收口批次直接登记 `accepted`。修复真实 blocker 后只重跑覆盖修复的测试和受影响
平台，不强制重新审查未变化的整个 Work Package。

所有 Work Package 都检查：

- 重复执行核心成功路径和失败路径。
- 日志、错误信息和已知问题。
- 资源、任务和临时代码清理。
- 回退步骤可执行。
- 没有实现下一阶段能力。

按影响范围追加：

- 生命周期/进程：重复启动退出、连续失败恢复、句柄和后代进程。
- 窗口/UI：正式平台矩阵的多 DPI/scale、多屏、IME、焦点和显示隐藏。
- 数据：正式平台矩阵的应用锁、备份、原子写入和 legacy Qt 回退。
- IPC：阻塞、取消、乱序、背压、连接关闭和旧 generation。

临时 feature flag 必须删除，或明确记录用途、所有者和最晚移除阶段；仍用于安全 dogfooding 或回退的 flag 不得机械删除。

## G-011：Supervisor 与 IPC 接口冻结点

接口冻结表示进入变更控制，不表示永远不能修改：

- WP-1C-02 完成后只冻结 Python/Rust 的最小 lifecycle 语义草案；Phase 1P 可以把平台实现移入 backend，但不得改变业务所有权。
- Phase 1P 与 WP-1C-04 完成后冻结跨平台 lifecycle 接口、RuntimeLocator 和平台错误分类。
- WP-2-01/02 只冻结首条聊天证明需要的 request/response/event、取消、Gateway 和最小 Snapshot 字段。
- 完整通用 Operation、资源、业务优先级、Snapshot component 和多等级背压契约必须等待对应真实消费者，不随 Phase 2 自动冻结。
- WP-3V-01 通过后，真实 Assistant 已消费且通过故障门的最小 IPC 子集进入变更控制；未被消费的 ADR 方向仍可调整。
- Phase 3 开始后，不得为 UI 便利随意修改生命周期协议。
- 需要破坏性修改时，暂停功能开发、更新 ADR、兼容门禁和测试 fixture。

同一关键接口在一个 Phase 内发生两次以上结构性返工时，必须停止编码并重新进行设计审查。

结构性返工包括字段所有权变化、调用方向变化、状态模型变化和旧调用方无法兼容的协议重写；普通命名修正和内部实现替换不计入。

## G-012：停止条件

出现以下任一情况时，必须停止扩展范围并重新拆分：

- 一个 commit 或 Work Package 无法被独立回滚。
- 一个 Work Package 同时出现三个以上新的核心抽象。
- 一个 Work Package 需要修改允许列表外的领域模块。
- 为修复当前实现，需要提前实现下一阶段产品能力。
- Fake Core 无法稳定复现故障。
- Phase 1A 首选单透明窗口在真实 WebView、物理输入、DPI 或中文 IME 门禁中失败，或只能依赖隐藏 Qt、第二生命周期根、管理员权限和范围外兼容层才能通过。
- macOS、X11 或 Wayland 只能通过静默削减点击穿透、拖动、IME、截图或生命周期能力才能继续。
- 公共 Runtime 继续依赖 `.exe`、WinDLL、Win32 window region、Windows Job 或仓库固定目录，无法抽成受测 backend。
- 同一平台问题在三平台中被不同临时兼容层重复实现，而没有更新 ADR-0004。
- 真实应用行为与契约测试持续不一致。
- 同一个接口连续发生结构性重写。
- 当前阶段 bug 数量继续增加而不是下降。

停止后只能进行：

- 缩小范围。
- 删除实现。
- 补充测试。
- 修正边界。
- 更新 ADR 和 Work Package 记录。

不得通过继续增加兼容层、全局状态和临时补丁绕过问题。

## G-013：分支和提交规则

本项目采用个人开发模式：

- 所有 Runtime v2 生产改动直接提交到 `refactor/tauri-runtime-v2`。
- 不为 Work Package 创建 PR，不要求未参与实现的独立审查者。
- 实施计划不得额外规定 fresh reviewer、修复后 re-review 或 whole-WP clean review 作为状态门；
  项目负责人明确要求的专项审查除外，审查本身也不得使已通过且输入未变化的证据失效。
- 不直接提交或合并到 `dev`；最终进入 `dev` 的方式和时间由项目负责人在 Phase 7 后单独决定。
- 不为了制造整洁历史而丢失有价值的故障和决策记录。
- 不整体 squash 掩盖 Work Package 边界，除非项目负责人明确要求。

每个 commit 必须：

- 使用中文 Conventional Commit 标题。
- 在正文标记 Phase 和 Work Package。
- 列出明确非目标和修改层级/目录。
- 给出实际自动测试、故障测试和人工验收结果。
- 给出风险和独立回退方式。
- 保持可独立理解和回滚。

本地 required checks 按当前阶段和影响范围执行。对应测试设施建立后，其检查从后续相关 Work Package 起成为强制项，包括：

- Rust 测试。
- Python 测试。
- 协议 fixtures。
- Fake Core 故障测试。
- 修改范围和禁止目录检查。
- Windows、macOS、Linux 平台矩阵；Linux GUI 能力额外登记 X11/Wayland。
- RuntimeLocator、共享锁和受控进程树的对应平台真实测试。
- platform foundation workflow 的 push/pull_request path filter 必须对称覆盖 `app/core_host/**`、本轮允许修改的 Python Core/Assistant 领域路径、Core fixtures/tests 和原生 lifecycle harness；Python/Core-only 提交不得因只改 Python 而跳过三平台门。

Phase 7 和任何最终集成到 `dev` 的操作前，必须运行完整 Python、Rust、三平台 WebView E2E、产品功能等价台账和发布验收，并由项目负责人做最终审查。

## G-014：提交审查必须回答的问题

每个生产 commit 的正文或对应 Work Package 记录必须回答：

1. 这个改动属于哪个 Phase、Work Package 和退出条件？
2. 当前阶段为什么必须做？
3. 明确不做什么？
4. 是否修改现有 Assistant 业务语义？
5. 是否增加未来才需要的抽象？
6. 是否可以独立回滚？
7. 哪些失败路径已经真实验证？
8. 是否修改 legacy Qt 可读取的数据？
9. 是否引入新的持久化格式？
10. 提交后获得了什么可验证结果？
11. 是否影响平台 backend、Runtime 路径、窗口、进程、锁、权限或打包？哪些正式平台已经验证？
12. 对应产品功能等价台账中的哪一项？状态为什么可以前进？
13. 哪个近期真实用户场景会被这个改动阻塞？当前真实消费者是谁？

任何问题无法清晰回答时，不应提交该生产改动。

## G-015：跨平台先行与产品功能等价

跨平台和功能等价不是 Phase 7 的清理任务：

- WP-1C-02 后插入 Phase 1P 的历史决策已完成；WP-1C-03 及后续 Work Package 继续强制依赖 WP-1P-06。当前启动点只引用 Work Package 总表。
- 正式基础矩阵固定为 Windows x64、macOS arm64、Linux x64；调整 target 必须更新 ADR-0004、Work Package、CI 和发布台账。
- Windows 已 accepted 的历史 WP 保留为 Windows backend 证据，不撤销其实现价值，也不代表 macOS/Linux 已通过。
- 共享 Supervisor、IPC、Snapshot、数据 schema 和产品语义不得按平台 fork；原生差异只能位于批准的平台 backend。
- 任何已有用户能力都必须登记在 `docs/specs/runtime-v2/product-capability-parity.md`，并分配到具体 Work Package。
- 内部开发分支可以暂时缺功能，但不得把“尚未迁移”改写为“可选”或从台账删除。
- 发布前，台账中的发布必备项必须达到 `parity-accepted` 或存在项目负责人批准的替代设计。

如果某个平台无法保持原交互，必须在实现对应产品能力前完成替代架构评审；不得等到 Phase 7 再以平台限制为由降级。

## G-016：整体路径防扩张与真实消费者门禁

单个 Work Package 符合允许目录和单一目的，不代表整体执行路径没有扩张。如果连续的基础设施 WP 让首个真实产品垂直链持续后移，必须暂停并进行架构验证审查。

出现以下任一条件即触发审查：

- 连续三个以上底层 WP 没有新增真实产品消费者。
- 新抽象只有 Fake Core、测试 fixture 或未来计划作为消费者。
- 新 WP 无法说明它阻塞哪一个近期真实用户场景。
- 第一条真实聊天继续被后移，或 CAP-004 未达到 `architecture-validated` 就开始堆叠通用平台。
- ADR 的方向性内容被误当成当前必须完整实现的内容。
- 一个当前消费者的窄需求被升级为面向 Tools、MCP、Memory、导入或未来 Agent 的统一框架。

触发后必须按顺序：

1. 说明当前缺失的真实消费者和被后移的用户场景。
2. 判断能否先实现更窄、仍满足进程/协议/数据安全的垂直切片。
3. 将非必要泛化后移到出现对应真实消费者的所属 WP。
4. 更新 Work Package 总表、依赖、退出条件、ADR 状态门和产品能力映射。
5. 经项目负责人批准后才继续生产实现。

审查不得删除或放宽 major/minor 协商、required capability、generation credential、stderr 持续排水/脱敏、framing/EOF/deadline/stdout 污染 transport fatal、完整进程树清理、应用锁和数据兼容门禁；也不得用同步阻塞聊天通道换取更短路径。

WP-3V-01 是这项规则的第一次强制收口：CAP-004 未达到 `architecture-validated` 前，不得激活 WP-4-01 或任何完整 diagnostics、Runtime Repair、通用资源/Operation 平台 WP。若验证发现前置生产缺陷，WP-3V-01 退回 `planned`，只允许一个责任 WP 重新进入 `stabilizing`；不得同时修多个 WP 或在验证 WP 内顺手扩张生产范围。

本计划的首轮路径按同一规则自检：WP-1C-04 后立即安排 WP-3-01，新增真实 Assistant Adapter/readiness 消费者；WP-1D-01 又新增用户可见的真实失败/重试消费者，此后到 WP-3-02 之间只有两个底层 IPC WP，因此不触发“连续三个以上底层 WP 无真实产品消费者”的暂停条件。

## 执行原则摘要

```text
选择一个 Work Package
-> 记录允许列表、非目标和退出门
-> 小步实现并提交单一目的 commit
-> 运行相关自动测试、故障测试和真实验收
-> 进入 stabilizing，清零 P0/P1 和退出条件 bug
-> 记录已知问题与回退方式
-> 标记 accepted
-> 才能开始下一个 Work Package
```

平台敏感工作还必须经过：

```text
共享契约
-> Windows/macOS/Linux backend 实现与自动门禁
-> 对应平台真实应用验收
-> 产品功能等价台账更新
-> 才能全局 accepted
```

个人开发不降低证据标准，只移除不适用的 PR 和独立审查流程。详细 commit 历史承担变更说明、回溯和阶段 Review 的职责。
