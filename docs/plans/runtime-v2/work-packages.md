---
kind: plan
status: active
audience: maintainer
source_of_truth: self
active_work_package: WP-4-06
updated: 2026-08-17
---

# Sakura Runtime v2 Work Package 拆分与执行清单

> 路线图状态来源：本文第 2 节
> 工作分支：`refactor/tauri-runtime-v2`
> 主计划：`docs/archive/plans/runtime-v2/2026-07-14-tauri-python-core-v2.md`
> 历史治理资料：`docs/archive/plans/runtime-v2/delivery-governance.md`
> 旧迁移取证源：`feat/tauri-assistant-migration` / `190dfafd24f5c5226bff8b4347837b6e45d9a331`

本文把 Runtime v2 Phase 0–7 拆成便于规划、验证和回退的 Work Package。它维护路线图顺序与状态，但
不是代码修改许可，也不限制调查或修复根因时涉及的模块。产品发布能力范围以
`docs/specs/runtime-v2/product-capability-parity.md` 为准；长期行为和技术选择分别以 Spec 与 ADR 为准。

Phase 4–7 保留发布能力映射和暂定编号。开始一项能力前应明确目标、真实消费者、验证和回退，但这些
内容用于规划与 Review，不形成文件 allowlist 或 Harness gate。

2026-08-02 产品方向修订：Legacy Qt 从受支持用户回退调整为迁移期实现参考、数据 parser/oracle 和隔离
验收工具。历史 Work Package 中关于真实 Qt、双入口和回退的文字保留为当时的事实证据，不再形成当前
产品承诺；活跃和未来 WP 不得用可运行 Qt 入口代替 Runtime v2 能力迁移。WP-7-03 确认 Qt 不再承载任何
未迁移能力并批准删除清单，WP-7-04 的正式工件不得包含 Legacy Qt 桌宠入口、实现或用户回退说明。

## 1. 路线图使用方式

- 状态只使用 `planned`、`active`、`stabilizing`、`accepted`。
- 表中依赖和先后顺序表达计划关系，不是修改代码的权限令牌；为定位和修复根因可以跨模块或跨 WP 调查。
- `active`/`stabilizing` 表示当前路线图焦点。其他必要修复或仓库维护可以独立进行，不要求创建 task JSON
  或切换状态。
- 候选进入 `stabilizing` 后运行受影响能力的自动测试和真实设备验收；人工结果可以决定路线图是否进入
  `accepted`，但不会改变 Product Harness 已执行 case 的 PASS/FAIL。
- 历史 WP 中的 allowlist、base_ref、activation、`check/verify`、`manual_pending` 等文字只记录当时流程，
  自 [ADR-0021](../../adr/0021-product-harness-outcome-verification.md) 起不再形成当前规则。
- 历史 WP 中的真实 `data/` 零变化摘要只证明当时候选，不是通用禁写规则；当前安全边界以相关 Spec、
  数据兼容测试和仓库 `AGENTS.md` 为准。
- WP-1A、WP-1B、WP-1C-01/02 的既有 `accepted` 保留为原 Windows 范围内的历史结论；ADR-0004 生效后，平台敏感工作必须完成正式三平台矩阵才能称为全局 accepted。
- WP-1C-02 已以 `a06e1dada66b02474f3d65d4124f31094cda5e9e` 完成实现、验证和 accepted 闭环；随后插入的 WP-1P-01 至 06 也已全部 accepted。
- WP-1C-03 及之后的产品结论建立在 WP-1P-06 的跨平台基础上；已有 Windows 证据不能替代相关三平台验证。
- 基础设施只建设到足以支持第一条可靠的真实 Assistant 聊天垂直链。WP-1C-04 后立即执行 WP-3-01，让真实 Assistant Adapter/readiness 先消费 bundled Core lifecycle；随后最小 Router、Snapshot、取消和恢复继续由它驱动验证。
- 第二个真实消费者出现前，不冻结不必要的通用 Operation、资源、业务优先级、Snapshot component 或未来消费者抽象；方向性 ADR 内容不自动成为当前实现门禁。
- 设置功能从 WP-3U-02 起按 `docs/specs/runtime-v2/settings-incremental-migration.md` 逐域纵向迁移；拥有用户可配置能力的后续 WP 必须同时声明对应设置 feature、保存/生效/回退边界，不再等待 WP-5-02 集中恢复全部页面。

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
| WP-1P-01 | 跨平台 target、接口与错误分类冻结 | WP-1C-02 | accepted |
| WP-1P-02 | 三平台 RuntimeLocator 与 bundled Python 布局 | WP-1P-01 | accepted |
| WP-1P-03 | Windows/POSIX 共享应用锁 backends | WP-1P-02 | accepted |
| WP-1P-04 | Windows/macOS/Linux 受控进程树 backends | WP-1P-03 | accepted |
| WP-1P-05 | 三平台窗口交互、IME 与原生诊断 backends | WP-1P-04 | accepted |
| WP-1P-05A | macOS Runtime v2 窄范围基础纠正稳定化 | WP-1P-05 | accepted |
| WP-1P-06 | 三平台最小 Shell + Core lifecycle 和 CI 总门禁 | WP-1P-05 | accepted |
| WP-1C-03 | 协议协商、stderr 排水和故障 transport | WP-1P-06 | accepted |
| WP-1C-04 | bundled Python 端到端与 lifecycle 接口冻结 | WP-1C-03 | accepted |
| WP-3-01 | 无 Qt Assistant Adapter 与真实 readiness | WP-1C-04、WP-1P-05A | accepted |
| WP-1D-01 | 最小生命周期可见性与安全重试 | WP-3-01 | accepted |
| WP-2-01 | 最小并发 request/response/event Router | WP-1D-01 | accepted |
| WP-2-02 | 最小聊天取消、Gateway 与 Snapshot 边界 | WP-2-01 | accepted |
| WP-3-02 | 无 UI 的真实聊天 Core 垂直链 | WP-3-01、WP-2-02 | accepted |
| WP-3-03 | 固定产品 UI 与真实角色表现基线 | WP-3-02 | accepted |
| WP-3U-01 | 同一 Tauri App 的右键菜单与设置窗口宿主 | WP-3-03 | accepted |
| WP-3U-02 | 角色包可见能力与外观设置联动 | WP-3U-01 | accepted |
| WP-3S-01 | 供应商与模型设置纵向链 | WP-3U-02 | accepted |
| WP-H-01 | Agent Development Harness Foundation | WP-3S-01 | accepted |
| WP-3-04 | 真实聊天接入已冻结桌宠 UI | WP-H-01 | accepted |
| WP-3-05 | Core 崩溃恢复与 UI 重新水合 | WP-3-04 | accepted |
| WP-3-06 | Legacy 数据参考 → Tauri v2 → 参考 oracle 兼容门禁 | WP-3-05 | accepted |
| WP-3V-01 | Runtime v2 Assistant Architecture Validation Slice | WP-3-06、WP-2-01 | accepted |
| WP-4-01 | Memory 能力等价 | WP-3V-01 | accepted |
| WP-H-02 | Harness 删除型减负 | WP-4-01 | accepted |
| WP-H-02A | Harness 短超时输出测试确定化纠正 | WP-H-02 | accepted |
| WP-3-03A | 跨平台桌宠动态表面与精确命中纠正 | WP-3-03、WP-1P-05A、WP-H-02、WP-4-01A | accepted |
| WP-3-03B | Windows Composition 实时玻璃 PoC | WP-3-03A | accepted |
| WP-3-03C | Windows 输入栏实时高斯玻璃产品化 | WP-3-03B | accepted |
| WP-3-03D | Windows HostBackdrop 输入栏液态折射 PoC | WP-3-03C | planned |
| WP-3-03E | macOS 输入栏原生高斯与液态玻璃 | WP-3-03C、WP-4-04 | accepted |
| WP-4-01A | Memory 启动预热与设置窗口恢复纠正 | WP-4-01、WP-H-02A | accepted |
| WP-4-01B | Memory 与 Mem0 LLM 解耦 | WP-4-01A | accepted |
| WP-4-02 | Tools、Operation 与 Action ID 确认 | WP-H-02、WP-3-03A、WP-4-01A | accepted |
| WP-4L-01 | Runtime v2 迁移可观测性基础 | WP-4-02 | accepted |
| WP-4-03 | MCP 生命周期与工具调用等价 | WP-4L-01 | accepted |
| WP-4L-02 | 人类可读运行日志与 Prompt Trace | WP-4-03、WP-4-01B | accepted |
| WP-4-04 | Python 插件能力等价 | WP-4L-02 | accepted |
| WP-4-05 | TTS、播放与音频设备门禁 | WP-4-04 | accepted |
| WP-4-06 | 截图、受控资源与平台权限 | WP-4-05 | active |
| WP-4-07 | 自动观察、主动互动、提醒与任务 | WP-4-06 | planned |
| WP-4-08 | Phase 4 组合稳定化与资源回收 | WP-4-07 | planned |
| WP-5-01 | 设置仓库与剩余外观/布局缺口收口 | WP-4-08 | planned |
| WP-5-02 | 设置迁移关闭清单与首次设置编排 | WP-5-01 | planned |
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
| WP-7-02 | 三平台真实 Tauri WebView E2E 与 deferred 设备硬门禁 | WP-7-01 | planned |
| WP-7-03 | 产品功能等价与数据兼容总审查 | WP-7-02 | planned |
| WP-7-04 | 三平台打包、签名、更新与干净安装 | WP-7-03 | planned |
| WP-7-05 | 长时间运行、休眠恢复与故障注入 | WP-7-04 | planned |
| WP-7-06 | 最终发布审查与进入 dev 决策 | WP-7-05 | planned |

#### WP-3-03B：Windows Composition 实时玻璃 PoC

项目负责人插入授权（2026-08-13）：暂停尚待人工验收的 WP-4-01B，插入一个不进入默认产品路径的
Windows 技术验证包，回答透明 Tauri/WebView2 窗口能否承载 Windows Composition host backdrop，
并保持 WebView 内容、输入命中、窗口拖动和失败降级。WP-4-01B 的候选与自动证据保留，待本 PoC
结束后再恢复；本授权不构成 WP-4-01B 人工验收或 accepted。

```text
状态：accepted（2026-08-13 项目负责人验收通过）
前置条件：WP-3-03A accepted
base_ref：395b319ce7ffa74bffafbaeeefd02e023c441438
范围：Windows 原生 Composition host backdrop 最小视觉层、显式 PoC 开关、气泡/输入框透明叠加、自动检查、文档与实机验收记录
required profiles：docs、runtime-v2-shell、runtime-v2-window-surface
任务契约：harness/tasks/WP-3-03B.json；不创建 activation
非目标：默认启用、Legacy Qt、截图循环、WDA_EXCLUDEFROMCAPTURE、完整 Liquid Glass、跨平台实现、设置迁移、发布承诺
```

架构决策见 `docs/adr/0015-windows-composition-host-backdrop-glass.md`，行为与技术 Gate 见
`docs/specs/runtime-v2/WP-3-03B-windows-composition-glass-poc.md`，实施和回退见
`docs/plans/runtime-v2/WP-3-03B-windows-composition-glass-poc.md`。PoC 只能由显式环境变量开启；
初始化失败必须保留透明/半透明 WebView 基础路径。自动门通过后只进入 `stabilizing`，实时背景、拖动、
点击、DPI、截图与失败降级必须由项目负责人实机观察，Agent 不填写人工验收结论。

2026-08-13，项目负责人明确确认 WP-3-03B 验收通过并授权标记为 `accepted`；候选、自动门和验收声明
边界见 `docs/records/audits/WP-3-03B-AUTOMATED-VALIDATION.md`。按插入授权恢复此前已完成自动门的
WP-4-01B 为 `stabilizing`，继续等待其独立人工验收；本结论不接受 WP-4-01B 或后续工作包。

同日，项目负责人随后明确确认 WP-4-01B 验收通过，并批准把该暂停任务的固定 `base_ref` 前移到
WP-3-03B 已验收收口提交以处理插入冲突。契约修订提交后，`harness check WP-4-01B` 通过，最终
`harness verify WP-4-01B` 为 14/14 自动 case 通过、0 failed、0 blocked；WP-4-01B 据此标记为
`accepted`，并恢复此前已有候选与自动证据的 WP-4L-02 为 `stabilizing`，继续等待其独立人工验收。

#### WP-3-03C：Windows 输入栏实时高斯玻璃产品化

项目负责人插入授权（2026-08-13）：在 WP-3-03B 已 accepted 的技术结论上，把实时玻璃收敛为输入栏
产品能力，并暂停已经整合的 WP-4-04 候选。WP-4-04 代码、测试和证据保留，不因本次插入回滚。

```text
状态：accepted（2026-08-13 项目负责人验收通过）
前置条件：WP-3-03B accepted
base_ref：1e2f2f9bb57645a964f0a71c417a9de9ae686129
范围：输入栏纯色/高斯设置、Appearance v3、Windows input-only HostBackdrop backend、旧版视觉复刻、自动与实机验证
required profiles：docs、runtime-v2-shell、runtime-v2-window-surface
任务契约：harness/tasks/WP-3-03C.json；不创建 activation
非目标：气泡玻璃、Legacy Qt、截图模拟、辅助 HWND、macOS/Linux 毛玻璃、强度/tint 设置、插件与角色资源修改
```

行为规范、架构决策、实施与自动证据分别见
`docs/specs/runtime-v2/WP-3-03C-windows-input-gaussian-glass.md`、
`docs/adr/0017-windows-input-gaussian-glass-productization.md`、
`docs/plans/runtime-v2/WP-3-03C-windows-input-gaussian-glass.md` 与
`docs/records/audits/WP-3-03C-AUTOMATED-VALIDATION.md`。自动门通过后只能进入 `stabilizing` 并返回
`manual_pending`；项目负责人完成视觉验收前，Agent 不得填写或声称 accepted。

2026-08-13，最终实现提交 `8b581b1a` 的 `harness verify WP-3-03C` 为 8/8 自动 case 通过、0 failed、
0 blocked；项目负责人在最新独立候选运行后明确确认“可以,没问题, 切换也正常”。该声明关闭本包人工
视觉 Gate，WP-3-03C 据此标记 accepted。实际自动结果、候选路径、截图边界和未倒填的 150% DPI 项见
`docs/records/audits/WP-3-03C-AUTOMATED-VALIDATION.md`。WP-4-04 继续保持 planned；在负责人另行明确
批准其固定 `base_ref` 单向前移并恢复之前，本验收不自动重启 WP-4-04。

#### WP-3-03D：Windows HostBackdrop 输入栏液态折射 PoC

项目负责人插入授权（2026-08-13）：在 WP-3-03C 已 accepted 的实时高斯输入栏基础上，验证无需桌面
截图循环的 HostBackdrop 离散边缘折射。执行期间暂停 WP-4-04；其插件代码、测试和证据原样冻结，
不回滚已整合内容。本包不修改设置契约或产品默认值。

```text
状态：planned（2026-08-14 项目负责人要求搁置界面优化）
前置条件：WP-3-03C accepted
范围：Windows input-only 单 GPU 液态折射、鲜粉分步诊断、失败保持液态模式且关闭高斯替代层、自动与实机技术 Gate
建议自动验证：docs、runtime-v2-shell、runtime-v2-window-surface
非目标：正式设置项、气泡液态、截图/DXGI 捕获、辅助 HWND、逐像素位移 shader、跨平台实现、插件与角色资源修改
```

架构决策、行为 Gate、实施与验证记录分别见
`docs/adr/0018-windows-host-backdrop-discrete-liquid-refraction.md`、
`docs/specs/runtime-v2/WP-3-03D-windows-input-liquid-refraction-poc.md`、
`docs/plans/runtime-v2/WP-3-03D-windows-input-liquid-refraction-poc.md` 与
`docs/records/audits/WP-3-03D-AUTOMATED-VALIDATION.md`。PoC 只能由显式环境变量开启；关闭时必须与
WP-3-03C 行为一致。相关自动验证通过后可以作为 `stabilizing` 候选；项目负责人完成动态桌面、拖动、
DPI 和边缘覆盖视觉验收后，再决定是否在路线图中标记 accepted 或规划产品化设置包。

2026-08-14，项目负责人明确要求当前界面优化先搁置并继续推进主线。WP-3-03D 的代码、测试、自动证据
和未提交工作树状态原样保留，状态退回 `planned`；本次暂停不构成视觉验收或 `accepted`，也不授权再次
启动曾造成 DWM 事故的候选。路线图焦点切回 WP-4-04。

#### WP-3-03E：macOS 输入栏原生高斯与液态玻璃

项目负责人插入授权（2026-08-15）：WP-4-04 验收通过后，要求把此前输入栏视觉能力适配到 macOS，
优先使用系统公开 API；macOS 26 以下不支持液态时必须在设置中置灰锁定。

```text
状态：accepted（2026-08-16 项目负责人验收通过）
前置条件：WP-3-03C、WP-4-04 accepted
范围：平台无关输入视觉协调层、NSVisualEffectView 高斯、NSGlassEffectView 液态、逐模式 capability、文档与测试
建议自动验证：docs、runtime-v2-shell、runtime-v2-window-surface
非目标：气泡/整窗玻璃、设置窗口玻璃、macOS 13–15 自绘液态、截图/Metal 捕获、恢复 WP-3-03D、角色或用户数据修改
```

行为、架构和实施分别见
`docs/specs/runtime-v2/WP-3-03E-macos-input-native-glass.md`、
`docs/adr/0022-macos-native-input-glass.md` 与
`docs/plans/runtime-v2/WP-3-03E-macos-input-native-glass.md`。Appearance 保持 v3；Settings capability v2
按模式发布可用性。自动门和 macOS 26 实机检查通过后只能进入 `stabilizing`，负责人视觉验收前不得标记
`accepted`。WP-3-03D 继续保持 `planned`。

负责人验收记录（2026-08-16）：项目负责人在确认当前候选与界面微调后明确回复“是的都没问题”。同一
候选重新运行 `docs`、`runtime-v2-shell` 与 `runtime-v2-window-surface`，分别为 2/2、6/6 与 3/3 case
通过，0 failed；其中前端完整测试为 163/163。WP-3-03E 据此标记为 `accepted`。本记录不补写负责人
未逐项声明的平台或设备步骤；原始声明与自动证据见
`docs/records/audits/WP-3-03E-AUTOMATED-VALIDATION.md`。

`WP-1P-05A` 已 accepted，范围、允许目录、故障矩阵、真实 macOS 验收和独立回退见
`docs/specs/runtime-v2/WP-1P-05A-macos-corrective-stabilization.md`。`WP-3-01` 已于 2026-07-26 完成并
accepted；其设计、实施计划、允许列表、接受证据和回退见对应独立文档及第 9 节记录。WP-1D-01
已于 2026-07-26 accepted，随后完成的窗口交互/可见性纠正记录见第 10 节。WP-2-01 已按第 11 节
和 `docs/specs/runtime-v2/WP-2-01-minimal-concurrent-router.md` 完成实现、稳定化和候选验收；WP-2-02
已按 `docs/specs/runtime-v2/WP-2-02-minimal-chat-boundary.md` 完成实现、跨平台 CI 纠正、真实 Windows
lifecycle 候选验收并 accepted；WP-3-02 已按
`docs/specs/runtime-v2/WP-3-02-headless-real-chat-core.md` 完成实现、故障与资源回收门禁、本地完整回归和
同一 SHA 的 Windows/macOS/Linux platform workflow，并于 2026-07-26 accepted。WP-3-03 的首个
Fake Core 候选曾进入 `stabilizing`，但项目负责人在接受前明确要求先冻结最终产品 UI、使用真实角色资源、
让气泡与输入框常驻并移除功能切换栏；原候选因此不再满足产品退出门。WP-3-03 已按修订后的
`docs/specs/runtime-v2/WP-3-03-fake-core-pet-chat-presentation.md` 退回 `active` 完成纠正，随后于
2026-07-27 经项目负责人验收为 `accepted`。WP-3U-01 已在同日完成实现、稳定化和项目负责人
Windows 手动验收并标记 `accepted`；100%/150% DPI 真实设备证据由项目负责人按 G-008 明确接受为
非失败型证据风险并登记至 WP-7-02。WP-3U-02 已于 2026-07-27 在依赖满足后激活，并在同日以候选
`078c18df` 完成首轮生产实现后进入 `stabilizing`；其实际允许目录、故障矩阵、数据写入边界、回退命令、
计划提交和 DPI 延期决定见独立文档。本地完整 Harness/Python/frontend/Rust/legacy host 门禁、
Sakura/N.A.V.I. Windows 真实候选、隔离目录保存失败恢复和后续设置退出生命周期纠正均已通过；最终
生产候选为 `796db179454542f4a2a7900471a290f57b439ad5`，自动证据包括 canonical frontend 90 passed、
Rust 207 passed/23 ignored 和 Runtime v2 桌面壳 Harness 6/6 cases、138 项测试，且没有扩大忽略列表。
项目负责人于 2026-07-29 确认该最终候选的 Windows 2025 x64、macOS 15 arm64、Ubuntu 24.04 x64
三平台验收及最终实机组合验收完成，并明确授权标记为 `accepted`；P0、P1 和退出条件缺陷为零。
WP-3S-01 已在依赖满足后完成激活、数据门和生产实现，并于 2026-07-31 由项目负责人明确验收通过；
验收声明的精确记录见第 12 节和对应 record。WP-H-01 作为仓库基础设施步骤插入 WP-3S-01 与
WP-3-04 之间，完成实现、本地自动门和远端 CI 后于 2026-07-31 由项目负责人明确验收通过；对应声明
见 `docs/records/audits/WP-H-01-OWNER-ACCEPTANCE.md`。WP-3-04 完成实现、最终自动门和负责人实机验收后，
于 2026-08-02 由项目负责人明确验收通过；声明见
`docs/records/audits/WP-3-04-OWNER-ACCEPTANCE.md`。WP-3-05 随后完成实现、自动门和 Windows 实机验收，
并于 2026-08-02 由项目负责人明确验收通过；声明见
`docs/records/audits/WP-3-05-OWNER-ACCEPTANCE.md`。WP-3-06 完成实现、自动兼容门与负责人直接启动
Runtime v2 EXE 的人工验收，并于 2026-08-02 标记为 `accepted`；声明见
`docs/records/audits/WP-3-06-OWNER-ACCEPTANCE.md`。WP-3V-01 的依赖现已满足，并按冻结任务契约激活。

WP-4-01 的最终 Memory 候选已于 2026-08-08 由项目负责人明确验收通过；同一 SHA 的 Test 与
Windows/macOS/Linux 平台 workflow 全绿，声明见
`docs/records/audits/WP-4-01-OWNER-ACCEPTANCE.md`。随后插入 WP-H-02 作为 WP-4-02 的前置基础设施
减负步骤；它只能删除或简化 Harness 治理层，不得修改产品代码。WP-H-02 accepted 前不得激活 WP-4-02。

WP-1P-04 至 06 的 accepted 证据范围是 CI platform foundation；macOS/X11/Wayland 真实设备窗口、IME、多屏和 compositor 体验仍由 WP-7-02 承担，不能把状态列扩写为第五种状态。

### 2.1 旧顺序到新顺序迁移表

| 旧 WP | 新归属 | 调整 |
|---|---|---|
| WP-1C-03 | WP-1C-03 | 保持原位且边界不变：协议协商、credential、stderr 排水和 transport fatal；不接入业务 |
| WP-1C-04 | WP-1C-04 | 保持原位并收窄为三平台 bundled Python 与真实 Core lifecycle；不扩大协议抽象 |
| WP-3-01 | WP-3-01，移至 WP-1C-04 后 | 立即引入首个真实 Assistant 消费者，只适配角色/Session/Provider/readiness，不接聊天或通用平台 |
| WP-1D-01 | WP-1D-01 | 收窄为 startup/initializing/ready/failed/Core crashed、diagnostics 文本、retry 和 exit |
| WP-1D-02 | WP-1D-01 的必要文本；其余移至 WP-5-06 | 基础聊天前不建设 Runtime Repair 页面、通用日志浏览或自动修复 |
| WP-1D-03 | WP-1D-01 的最小安全 retry；其余移至 WP-5-06 | 仅保留同一 Supervisor 路径的清理、重试与退出门 |
| WP-2-01 | WP-2-01 | 收窄为独立 reader/writer、pending map、交错 event/response、有界队列和 generation 失效 |
| WP-2-02 | WP-2-01 的必要 control 隔离；其余按消费者移至 WP-4-01/02/03/08 | 不先建三级优先级或通用 worker process 框架 |
| WP-2-03 | WP-2-02 的聊天唯一终态/取消；通用 Operation 移至 WP-4-02 及后续真实消费者 | 先验证 `chat.send`/`chat.cancel`，不冻结未来任务平台 |
| WP-2-04 | WP-2-02 | Gateway 只为固定聊天 allowlist 建立，后续 command 权限由所属功能 WP 扩展 |
| WP-2-05 | WP-2-02 的最小聊天 Snapshot；资源 token 移至 WP-4-06，完整 component model 移至对应消费者 | 截图、音频、导入和所有未来 component 不阻塞聊天 |
| WP-2-06 | WP-2-01/02 的有界队列、terminal 不丢和安全断开；完整 progress/多等级背压移至 WP-4-08 或 WP-6-05 | Envelope 只冻结真实聊天已证明需要的字段 |
| WP-3-02 至 WP-3-06 | 编号保留，前置改为已提前的 Adapter 和最小 IPC 链 | 真实聊天不再等待完整 Phase 1D/2 |
| 无 | WP-3U-01 | 在真实聊天接 UI 前，把右键菜单和旧设置前端迁入同一 Tauri App；只建立窗口宿主和能力门控 |
| WP-5-02 的设置窗口宿主 | WP-3U-01 | 窗口生命周期和入口提前；页面 feature 随对应能力 WP 迁移，首次设置与关闭清单仍由 WP-5-02 收口 |
| WP-5-02 的角色外观子集 | WP-3U-02 | 角色名、初始消息、主题、立绘映射/切换和窄外观设置提前；角色选择、历史分页与完整 Session 等价仍留在 WP-5-03 |
| WP-5-01/02 的供应商与模型子集 | WP-3S-01 | 在真实聊天接 UI 前完成 Provider/模型 get、validate、原子保存、网络探测、受控 Core restart 和 Qt 回读 |
| WP-5-02 的页面集中迁移 | 对应能力 WP | Memory、Tools、MCP、插件、TTS、截图、主动互动等设置随 WP-4-01 至 07 逐域开放；WP-5-02 只做关闭清单和首次设置编排 |
| 无 | WP-3V-01 | 新增组合架构验证门；通过后 CAP-004 才可标记 `architecture-validated` |

## 3. Phase 0：冻结与基线

### WP-0-01：legacy Qt、工具链和验收环境基线

激活记录：

```text
状态：active
开始日期：2026-07-15
初始允许目录：docs/records/baselines/runtime-v2/；仅允许在本文更新 WP-0-01 状态与验收记录
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
允许目录：docs/records/baselines/runtime-v2/；tests/fixtures/runtime_v2/wp_0_02/；tests/unit/test_wp_0_02_data_contract.py；docs/adr/0003-runtime-v2-data-compatibility.md；.gitignore 中仅限跟踪该脱敏角色夹具的精确反向规则；仅允许在本文更新 WP-0-02 状态与验收记录
明确禁止目录：main.py；app/；desktop/；plugins/；data/；runtime/；characters/；third_party/；tools/mcp/；现有用户数据 schema；旧迁移分支代码；WP-0-03、WP-0-04 及后续 Work Package 生产实现
验收环境：当前 Windows 开发机；项目 .\runtime\python.exe；只读盘点真实仓库与 data/；所有写入/故障注入仅在 temp/runtime-v2-wp-0-02/ 的脱敏夹具副本执行；不安装依赖、不启动 legacy Qt/Tauri、不调用外部服务
关联 ADR：ADR-0003（Phase 1A 共享应用锁输入；Phase 3 双向数据兼容门禁输入）
计划提交：docs(runtime): 建立用户数据与共享应用锁契约
回退命令：git revert <WP-0-02-commit>；不得删除、恢复或改写真实 data/ 和用户资源
```

稳定化记录：

```text
状态：stabilizing
自动测试：docs/records/baselines/runtime-v2/run_wp_0_02_baseline.ps1 连续三次通过；每轮定向 pytest 4 passed；最终轮 1.27s
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
允许目录：docs/records/baselines/runtime-v2/；仅允许在本文更新 WP-0-03 状态与验收记录
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
允许目录：docs/records/baselines/runtime-v2/WP-0-04-architecture-review.md；docs/plans/runtime-v2/work-packages.md；仅在关闭实际冲突时窄改 docs/archive/plans/runtime-v2/2026-07-14-tauri-python-core-v2.md、docs/plans/runtime-v2/delivery-governance.md 和 docs/adr/0001-runtime-v2-process-supervision.md、0002-runtime-v2-ipc.md、0003-runtime-v2-data-compatibility.md
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
允许目录：desktop/frontend/ 下的共享命中区域纯逻辑、真实输入控件、IME/focus 状态机和 Node 可执行测试；desktop/src-tauri/ 下的共享命中几何、Win32 HWND 区域、拖动后锚点/DPI/工作区修正、Rust 测试和构建配置；desktop/tests/ 下仅限 WP-1A-03 Windows 真实交互验收脚本；docs/archive/plans/runtime-v2/2026-07-20-wp-1a-03-hit-drag-focus.md；本文仅更新 WP-1A-03 状态与验收记录
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

激活记录：

```text
状态：active
开始日期：2026-07-22
前置提交：a06e1dada66b02474f3d65d4124f31094cda5e9e（WP-1C-02 accepted）；920eb8188（跨平台规范与产品功能等价门禁）
允许目录：desktop/rust-toolchain.toml 仅限增加三个正式 target；desktop/src-tauri/src/platform/ 仅限无行为 target、trait、错误 DTO 和 contract tests；desktop/src-tauri/src/main.rs 仅限声明 compile-only platform module；docs/specs/runtime-v2/WP-1P-01-platform-contract.md；本文仅更新 WP-1P-01 状态与证据
明确禁止目录：main.py、legacy_qt_main.py、start*.bat；app/、plugins/、data/、runtime/ 内容、characters/、third_party/、tools/mcp/；desktop/frontend/；Cargo.toml/Cargo.lock 与新增依赖；shared_instance.rs、managed_process_tree.rs、window_interaction.rs、window_geometry.rs、core_supervisor.rs、core_host_protocol.rs、core_host_runtime.rs、Fake/真实 Core 验收与现有调用路径；.github/workflows/；WP-1P-02 及后续 backend、locator、CI 或产品能力实现
验收环境：当前 Windows 11 23H2 build 22631.4890 x64；Rust/Cargo 1.96.0；当前 native target x86_64-pc-windows-msvc；Tauri 2.11.3；WebView2 实际 150.0.4078.83；不安装或升级依赖，不以当前 Windows 编译冒充 macOS/Linux 证据；本 WP 只要求当前平台 compile/test、三平台 target/环境冻结和可审查契约
关联 ADR：ADR-0001（跨平台受控进程树不改变 Supervisor）；ADR-0003（共享锁语义和数据前置）；ADR-0004（正式矩阵、平台 backend 和跨平台先行）
计划提交：feat(runtime): 冻结跨平台服务契约与错误分类
退出条件：三平台最低环境、Rust target、WebView、包格式与 Python source ID 明确；五类 trait object-safe 且 compile-only contract tests 通过；公共/平台依赖图、稳定错误表、CI/实机责任和逐文件迁移清单完成；当前 Windows 生产路径、Supervisor、IPC、Snapshot、Assistant 和用户数据语义零变化
故障测试：未知 Rust triple 不被误识别为正式 target；native error number 不改变稳定错误码；category code 唯一；共享锁 identity 固定；development Runtime 不允许隐式选择；五类 trait 同时完成 object-safety 编译证明
已知风险：macOS/Linux 尚无 concrete backend 或实机证据；Python 工件 SHA-256、golden install tree 和 locator 属于 WP-1P-02；现有非 Windows Unsupported 分支要到对应后续 WP 才能删除
独立回退方式：整体 git revert WP-1P-01 accepted 提交，删除 compile-only platform 契约和本规范，恢复 WP-1C-02 accepted 后的生产实现；不回退 a06e1dada/920eb818，不触碰 runtime/、data/ 或用户进程
```

Accepted 记录：

```text
状态：accepted
验收日期：2026-07-22
修改范围：desktop/rust-toolchain.toml 登记 Windows x64、macOS arm64、Linux x64 三个正式 target；新增 compile-only platform facade、五类 object-safe backend trait、公共 target/runtime/process/diagnostics DTO、12 类稳定错误与四类 retry advice；main.rs 只声明未接线模块；新增 WP-1P-01 target/环境/Python source/依赖方向/错误/证据责任/迁移规范。未移动或修改任何 Windows backend、Supervisor、IPC、Snapshot、Python Core 或产品调用路径
契约测试：新增 8 项 Rust 可执行测试，覆盖三个稳定 target ID/triple、当前正式 target 识别、五类 trait object safety、共享锁 identity、显式 development Runtime、错误 category 唯一、native code 与稳定码隔离及诊断序列化；定向测试 8 passed
自动测试：cargo fmt --check 通过；完整 cargo test --locked 为 83 passed、13 ignored fixture、0 failed；Debug cargo build --locked 与 Release cargo build --release --locked 均成功且无 warning；git diff --check 通过
平台证据边界：当前仅在 Windows 11 23H2 build 22631.4890 x64、Rust/Cargo 1.96.0、Tauri 2.11.3、WebView2 150.0.4078.83 上完成 native compile/test；macOS/Linux 本 WP 只冻结 target、最低环境和后续证据责任，没有 concrete backend、native compile 或实机结果，未以 Windows 或 compile-only 冒充
依赖与范围：Cargo.toml/Cargo.lock 和依赖零变化；只修改激活记录允许的 toolchain、compile-only platform module、main.rs module declaration 和两份规范；没有修改 .github workflow、发布流程、legacy Qt、app/、plugins/、desktop/frontend/、runtime/、data/、characters/、third_party/ 或 tools/mcp/
业务与数据：没有启动 Shell、legacy Qt 或真实 Assistant；没有改变 Supervisor/generation/restart budget、framing、CoreReadiness、Snapshot、产品功能或用户数据 schema；工作区没有 data/ 或 runtime/ 版本控制变化
退出条件：三平台最低环境、Rust target、WebView、首个包格式与精确 Python source ID 已冻结；五类契约、调用方向、稳定错误表、CI/实机责任和 Windows 逐文件迁移清单完整；P0/P1=0，退出条件相关缺陷=0
已知限制：macOS/Linux backend 和 CI 尚未实现；Python archive SHA-256、golden install tree 与唯一定位属于 WP-1P-02；共享锁、进程树、窗口/IME/diagnostics 和三平台总门依次属于 WP-1P-03 至 06；现有非 Windows Unsupported 分支在对应 WP accepted 前仍存在
独立回退方式：整体 git revert WP-1P-01 accepted 提交，删除 compile-only platform 契约和本规范并恢复单 target toolchain 声明；保留 WP-1C-02 及跨平台治理文档，不触碰 runtime/、data/ 或用户进程
关联提交：本 WP accepted 提交（feat(runtime): 冻结跨平台服务契约与错误分类）
```

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

激活记录：

```text
状态：active
开始日期：2026-07-22
前置提交：21c2aaf9（WP-1P-01 accepted）
允许目录：desktop/src-tauri/src/platform/runtime_locator.rs、mod.rs、target.rs 和 contracts.rs 中仅限 locator 实现所需的 manifest/target/runtime DTO；desktop/src-tauri/runtime-layouts/ 三平台精确 source/golden manifest；core_host_runtime.rs 与 phase_1c_core_host_acceptance.rs 仅限改为消费 RuntimeLayout；desktop/tests/windows_core_host_acceptance.ps1 仅限删除显式 Python 注入；shared_instance.rs 仅限把 Windows-only test module 收窄到 Windows 以允许 POSIX native test build；scripts/runtime_v2_archive.py 与 tests/unit/test_runtime_v2_archive.py 仅限 CI/build archive 下载校验；.github/workflows/runtime-v2-platform-foundation.yml 仅限三平台 locator/native compile 门；docs/specs/runtime-v2/WP-1P-01-platform-contract.md、WP-1P-02-runtime-layout.md 与本文状态/证据
明确禁止目录：main.py、legacy_qt_main.py、start*.bat；app/ Python Core/Assistant 实现；plugins/、data/、runtime/ 内容、characters/、third_party/、tools/mcp/、desktop/frontend/；Cargo.toml/Cargo.lock 与新增依赖；shared instance 生产锁行为；managed_process_tree.rs/Job/进程组；core_supervisor.rs、core_host_protocol.rs、Snapshot/IPC 语义；window_geometry.rs、window_interaction.rs；legacy package/release workflow；WP-1P-03 及后续 backend 或产品能力
验收环境：本机 Windows 11 23H2 x64、Rust/Cargo 1.96.0、Tauri 2.11.3、仓库 CPython 3.12.8；上游三个固定归档只下载到系统临时目录计算/核对 size+SHA-256 后删除；本机 WSL 为 Ubuntu 24.04.4 x64 但没有 Rust/Cargo，不能登记为 Linux native Rust 证据；macOS arm64 证据必须来自 workflow 的真实 Apple Silicon runner
关联 ADR：ADR-0004（RuntimeLocator、正式 target、无 PATH fallback、三平台证据）；ADR-0001（只替换 Core spawn 的路径来源，不改 Supervisor/进程树）；ADR-0003（不读写共享 data）
计划提交：feat(runtime): 实现三平台 RuntimeLocator 与固定布局
退出条件：三个精确 archive source/size/SHA-256 和 development/packaged layout 冻结；locator 对三 target 选择唯一 Python/Core 并验证 manifest、canonical containment、permission 和 PE/Mach-O/ELF architecture；Core Host 启动消费 RuntimeLayout；Windows/macOS/Linux native workflow 同提交全绿；普通公共调用无 .exe、PATH、target/debug 或隐式 cwd 回退
故障测试：mode/root 混用、相对 root、target/build 不匹配、Runtime/manifest/Python/Core 缺失、manifest 非法或 identity 被改、资源根移动、Python 无执行权限、损坏 header、错误 CPU/格式、canonical path 逃逸、归档 size/hash 不匹配、模糊 asset/source 漂移
已知风险：native workflow 在当前未推送分支尚未执行；macOS arm64 runner 配额/可用性是外部前置；POSIX 锁、进程树和窗口 backend 分别属于 WP-1P-03 至 05，不能在本 WP 补写
独立回退方式：整体 git revert WP-1P-02 实现提交，恢复 compile-only RuntimeLocator trait 和 Phase 1C 显式 Windows Python 参数；保留 WP-1P-01、现有 Windows backend、Core Host lifecycle 和用户数据，不删除 runtime/ 或 data/
```

Accepted 记录：

```text
状态：accepted
本地 TDD：首轮 locator 定向测试 4 passed、4 failed；失败精确暴露 MacOs serde 会生成错误的 mac-os-arm64 ID，以及 Windows canonical \\?\ 前缀不能与未 canonical resource root 直接比较。修正为三个显式 serde platform ID，并只比较 canonical root 后，8/8 locator 定向测试通过
source 完整性：从 Python.org 3.12.8 和 Astral python-build-standalone 20250106 的固定 HTTPS asset 读取元数据并完整下载到系统临时目录；三个 byte length/SHA-256 与 runtime-layout manifest 一致；新 archive verifier 又分别完成三工件真实下载校验。macOS/Linux 的 python/bin/python3 均指向 python3.12，canonical 目标真实 header 分别为 Mach-O cffaedfe + CPU 0c000001 和 ELF64 little-endian + machine 3e00，与 locator 判定一致；临时下载均已删除，工作区没有归档或 Runtime 内容变化
本地自动测试：cargo fmt --check 通过；platform contract/golden 为 16 passed、1 ignored staged integration、0 failed；显式执行真实 Windows staged Runtime locator 为 1 passed；完整 cargo test --locked 为 91 passed、14 ignored fixture、0 failed；Python archive verifier 为 2 passed；Debug/Release cargo build --locked 均成功且无 warning；PowerShell、三份 JSON manifest、三 job workflow YAML 和 git diff --check 全部通过
接线边界：CoreHostRuntime::launch 只消费 RuntimeLayout；Phase 1C 验收删除 SAKURA_PHASE_1C_PYTHON；framing、initialize、Snapshot、deadline、Job、Supervisor 和用户数据语义未改；shared_instance 只有 test cfg 收窄，无生产锁修改
真实 Windows 验收：首次复跑在业务链已成功关闭后暴露验收 summary 仍读取已删除的 $Python 变量，脚本因此退出 1；修正为由 Rust 写出 runtime-layout evidence 并把 locator/manifest 文件纳入 stale-EXE 清单。最终 windows-locator-ready-final 为 status=passed：真实 Tauri Window 可见，runtimeTarget=windows-x64、runtimeMode=explicit_development、sourceId=cpython.org/3.12.8/windows-embed-amd64，Core hello/ready/两次 health/protocol shutdown 成功，根退出码 0；登记 9 个进程身份、最终残留 0，临时 Runtime/WebView 目录残留 0；真实 data/ 121 文件、1,046,428,570 bytes，before/after SHA-256 均为 e4982a382a9668de39276ce1d3203d9f47c18b69e9b1aef8787ffbd1eb0fe1ca
原生平台证据：GitHub Actions run 30018844932 对提交 5c0ef64b6c25f5554ceb4dc4072ab98a8e29f369 完整成功；RuntimeLocator (windows-x64)、RuntimeLocator (macos-arm64)、RuntimeLocator (linux-x64) 三个 job 在同一 run 全绿。每个 job 均通过真实 runner architecture 断言、精确 Python 归档 size/SHA-256 校验、正式 layout/platform tests、staged repository RuntimeLocator ignored integration 的显式执行和 native Tauri Shell 编译
首次 CI 修正：初始提交 889a48d1 的 macos-14-xlarge 因账户计费限制无法分配，Linux 则在 generate_context! 读取缺失 icons/icon.png 时失败；修正提交 5c0ef64b 改用 GitHub 标准 macos-15 Apple Silicon runner、从既有 icon.ico 等价生成 icon.png，并收窄 Windows-only import。稳定化范围据此补充 icon.png 和 managed_process_tree.rs 的纯 cfg/import 整理，均不改变产品行为、进程树语义或视觉设计
范围与数据：没有修改 Cargo 依赖、legacy Qt、Assistant、插件、前端、生产 data/runtime 内容、锁/进程树/窗口 backend 或 legacy release workflow；没有启动真实 Assistant 或执行 schema migration
P0/P1：WP-1P-02 范围内为 0，全部退出条件已关闭。Draft PR 的普通 Test workflow 仍有 7 个 Unit 和 12 个 legacy UI 测试失败，分别源于 Linux 执行 Windows ctypes.WinDLL 路径，以及测试仍从已瘦身的 main.py 导入 legacy Qt 符号；这些不是 RuntimeLocator native workflow 的 accepted 条件，但属于分支级未关闭门禁，不得误报为全量测试通过或在最终合并前忽略
下一步：WP-1P-03 现可独立激活，开始 Windows named mutex 与 POSIX advisory lock backend；普通 Test workflow 红灯需在对应锁 backend/legacy parity 测试接线中关闭
独立回退方式：git revert WP-1P-02 实现提交；不回退 21c2aaf9，不触碰用户 runtime/data
关联提交：889a48d1（实现）；5c0ef64b（首次 native CI 修正）；本 WP accepted 记录提交
```

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

激活记录：

```text
状态：active
开始日期：2026-07-23
前置提交：d7248da3（WP-1P-02 accepted）
允许目录：app/core/instance.py 仅限 legacy Python Windows/POSIX 共享锁适配；legacy_qt_main.py 仅限平台中性锁错误提示；desktop/src-tauri/src/shared_instance.rs、platform/contracts.rs 和 main.rs 仅限 InstanceLockBackend、Windows named mutex、POSIX advisory lock 与启动前锁接线；Cargo.toml/Cargo.lock 仅限 Unix 原生锁所需的已锁定 libc 直接依赖；tests/unit/test_selfcheck.py、test_wp_1a_04_shared_mutex.py、test_migration_runner.py、tests/integration/test_wp_1a_04_entries.py 与 tests/ui/test_pet_window.py 仅限跨平台锁契约和 legacy_qt_main 测试入口修正；新增 POSIX 双入口 fixture/acceptance；.github/workflows/runtime-v2-platform-foundation.yml 仅限共享锁变更触发路径、Linux 依赖安装有界化和同分支旧 run 取消；ADR-0003、WP-1P-03 规范和本文状态/证据
明确禁止目录：data/、runtime/、characters/、plugins/、Assistant/Core/IPC/Snapshot/Supervisor/进程树/窗口交互实现、desktop/frontend/、legacy package/release workflow；不得把 legacy Qt 生命周期塞回 main.py；不得用 skip/xfail、普通文件存在、PID 文本或 stale 猜测替代真实 OS lock
验收环境：本机 Windows 11 x64 保留已 accepted named mutex 回归；GitHub windows-2025 x64、macos-15 arm64、ubuntu-24.04 x64 原生 job；POSIX 路径与打开/权限/冲突语义由 Rust/Python golden tests 固定，真实双入口证据分别在 macOS/Linux native runner 产生
关联 ADR：ADR-0003 Phase 1P POSIX 应用锁技术门；ADR-0004 共享应用锁平台基础
计划提交：feat(runtime): 实现 Windows POSIX 共享应用锁后端
退出条件：普通 Unit/UI 恢复全绿；Windows named mutex 无回归；macOS/Linux Rust/Python 使用同一 canonical lock path、0600 regular file 和非阻塞 exclusive advisory lock；双向冲突、普通文件残留、正常/强杀释放、路径/权限/API fatal 与锁前 data 零变化通过；Linux prerequisite 安装有分钟级 timeout/retry 且旧 run 可取消
```

允许能力：

- 保留现有 Win32 mutex 为 Windows backend。
- 为 macOS/Linux 实现同路径、同打开模式、同 advisory lock 语义的 Rust/Python backends。
- 增加平台锁路径 golden fixture、冲突/权限/fatal 错误和正常/强杀释放测试。

明确禁止：

- 不用普通文件存在、PID 文本或 stale 猜测代替 OS lock。
- 锁文件不得位于共享 `data/`；冲突入口不得先写日志、配置或 migration。

退出证据：三个平台的 production Rust/Python backend 分别通过双向冲突、正常释放、双方强杀释放、API/权限 fatal、无人持锁但普通文件存在和锁前入口顺序测试；真实 Tauri Shell + Core、legacy Qt 回退入口与全部后代排水后的最终释放仍由 WP-1P-06 验收，不以本 WP 的 backend 子进程测试冒充。

独立回退：回退 POSIX backends 和公共适配，恢复 Windows backend；不得删除真实锁文件或用户数据。

Accepted 记录：

```text
状态：accepted
验收日期：2026-07-23
实现提交：ef5539a6（Windows/POSIX backend、legacy 测试入口、规范和 CI）；6800f72e（Unix 环境读取器生命周期推断）；71c3039c（跨语言探针 PYTHONPATH/stdout pipe）
本地 Windows：tests/unit 965 passed、6 skipped；tests/ui 379 passed；影响面定向 320 passed、3 skipped；cargo test --locked 92 passed、14 ignored fixture；shared_instance Windows 4 passed；cargo fmt、py_compile、Debug/Release build、git diff --check 全部通过
普通 CI：Test run 30025831268 对 71c3039c 全绿，Unit tests 2m12s、UI tests 4m08s；前两轮最新实现 SHA 的普通 Test 同样成功，原 7 个 Unit 与 12 个 legacy UI 失败已关闭
原生平台 CI：pull_request run 30025831299 对 71c3039c 全绿；macos-15 arm64 1m57s、ubuntu-24.04 x64 2m38s、windows-2025 x64 3m23s，三边均通过 architecture、固定 Runtime、format、platform contracts、native shared_instance tests、staged RuntimeLocator 和 Tauri Shell build；push run 30025828101 独立重复全绿
POSIX 锁证据：macOS/Linux 均真实执行 Rust/Python 同路径 golden、双向冲突、正常释放、普通文件残留、Python holder 强杀后 Rust 重获、Rust holder 强杀后 Python 重获、0700/0600 与单硬链接检查；未用 skip/xfail 替代
CI 故障收敛：旧卡死 run 30019843652 已由 concurrency 自动取消；新 Linux apt step 19 秒成功且受 8 分钟 step、三次外层重试和每次 timeout/kill-after 约束；首次原生 run 暴露 Unix Fn 生命周期编译错误，第二次暴露探针 import/broken-pipe，均修正后由第三次同矩阵全绿验证
范围与数据：没有修改 data/、runtime/、characters/、plugins/、Assistant/Core/Supervisor/IPC/Snapshot、进程树、窗口交互、frontend 或 legacy package/release workflow；legacy Qt 生命周期未回填 main.py；用户未跟踪 .superpowers/ 未暂存
边界：本 WP 接受 backend 与 composition-root 获取顺序，不声称完成真实三平台 Shell + Core/legacy Qt 全生命周期排水；后者仍是 WP-1P-06 的强制退出条件，ADR-0003 的 Phase 3 数据兼容总门也未开始
P0/P1：WP-1P-03 范围内为 0，退出条件相关缺陷为 0
独立回退方式：依次 git revert 71c3039c、6800f72e、ef5539a6；不删除普通 POSIX lock file、data/sakura.lock、Qdrant lock 或任何用户数据，不回退 WP-1P-01/02
```

### WP-1P-04：Windows/macOS/Linux 受控进程树 backends

状态：`accepted`（CI platform foundation，2026-07-24）

前置提交：`9d079a4d`（WP-1P-03 accepted）

独立规范：`docs/specs/runtime-v2/WP-1P-04-managed-process-tree.md`

允许目录、明确非目标、公共生命周期顺序、guardian containment、故障矩阵、三平台证据责任、
退出条件和回退方式以独立规范为准。本 WP active 期间 WP-1P-05/06 保持 planned。

主要结果：保留 Windows Job Object，补齐 macOS/Linux session/process group、整树终止、wait 验证和身份安全边界。

实现提交：`1aa02e5`；accepted 记录随本计划更新提交。最新 Draft PR HEAD 为
`1aa02e591335d7ebc43d50b2b3533f60d8edbf1b`，原生 platform foundation push/pull runs 为
`30057738510` / `30057739993`，Unit/UI run 为 `30057739984`，均全绿。

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

状态：`accepted`（CI platform foundation，2026-07-24）

独立规范：`docs/specs/runtime-v2/WP-1P-05-window-diagnostics.md`

实现提交：`3e23285`；accepted 记录随本计划更新提交。最新 Draft PR HEAD 为
`3e23285f90c40cd45d6817918d9a4fdf8aebb127`，原生 platform foundation push/pull runs 为
`30066486490` / `30066488599`，Unit/UI run 为 `30066488685`，均全绿。Linux run 包含
Xvfb window backend 合同测试；macOS/X11/Wayland 真实设备体验保持 deferred。

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

### WP-1P-05A：macOS Runtime v2 窄范围基础纠正稳定化

独立规范：`docs/specs/runtime-v2/WP-1P-05A-macos-corrective-stabilization.md`。

主要结果：在不扩大产品能力的前提下，纠正 macOS 默认启动、透明 Shell 与 native drag 完成时机，
使用户拖动后的物理立绘锚点成为后续状态切换的唯一位置来源。

强制前置：WP-1P-05 accepted。WP-3-01 必须等待本 WP accepted，不能把尚未验证的 macOS
窗口基础带入真实 Assistant Adapter。

独立回退：只回退本 WP 的激活、实现与 accepted 记录；保留 WP-1P-05/06、WP-1C-03/04 的
既有 accepted 证据和 Windows Win32 region/move-loop 实现。

### WP-1P-06：三平台最小 Shell + Core lifecycle 和 CI 总门禁

状态：`accepted`（CI platform foundation，2026-07-24）

独立规范：`docs/specs/runtime-v2/WP-1P-06-shell-core-lifecycle.md`

激活提交：`abfe0fe`；实现提交：`d331919`；修正提交：`35f5a30`、`f71cc68`、
`ee3a39e`、`077dab8`、`b88e744`。最新实现 HEAD `b88e744918c0d84548fbdd43df8b17a0e00a4797`
的 push/pull_request platform runs `30068988807` / `30068990391` 与 Unit/UI run
`30068990399` 全绿。

主要结果：把 WP-1P-02 至 05 组合为持续门禁，三个平台都能从正式 locator 启动 Shell 与最小 Core 并完成有界关闭。

允许能力：

- 建立 Windows/macOS/Linux CI、平台测试脚本、golden fixtures 和有界真实验收入口。
- 组合共享锁、RuntimeLocator、窗口 backend、受控进程树和 WP-1C-02 的 hello/initialize/health/Snapshot/shutdown。
- 记录平台工具链、WebView、Python、CPU、session/compositor 和包布局。

明确禁止：

- 不接入 WP-1C-03 协议扩展、真实 Assistant、聊天或 Phase 2+ 能力。
- 不以 compile-only、mock、单元测试或 Windows 结果替代另一个平台的真实生命周期证据。

故障测试：Runtime 缺失、锁冲突、spawn/pipe 失败、hello/initialize 卡死、损坏 stdout、忽略 shutdown、根崩溃并遗留后代、窗口关闭、重复两轮和失败后恢复。

退出证据：三个平台完成真实 native Shell 的 `lock held -> RuntimeLocator -> managed Core ->
hello/initialize/health/Snapshot -> protocol shutdown -> full tree exited -> lock reacquirable`，并覆盖
第二入口冲突、Shell 强杀后的 OS 保险回收与恢复轮；backend tree/pipe/fd/handle 清理、Core PID
退出、隔离临时目录删除和 `data/`/`runtime/` 内容清单前后相同。ADR-0004 已更新为
`Technically Validated for CI platform foundation`，不等于完整产品 Accepted。

独立回退：回退组合门禁和 CI 接线，保留每个平台已独立验证的 backend；后续 WP 的当前状态只见第 2 节总表。

## 8. Phase 1C 续：协议与 bundled Runtime 冻结

### WP-1C-03：协议协商、stderr 排水和故障 transport

激活记录（2026-07-24）：状态为 `active`。独立规范见
`docs/specs/runtime-v2/WP-1C-03-protocol-transport.md`；允许目录、非目标、协议版本与 capability 结构、
generation credential 生命周期、stderr 有界排水/脱敏、故障矩阵、三平台证据责任、timeout、
资源上限、退出条件和独立回退均以该规范为准。前置 WP-1P-06 已 accepted；同一时间没有其他
`active`/`stabilizing` WP；WP-1C-04 的激活范围见
`docs/specs/runtime-v2/WP-1C-04-bundled-core-lifecycle.md`。

Accepted 记录（2026-07-24）：最新实现 HEAD `af79255` 的 Unit/UI run `30074854468`、
pull_request platform run `30074854406` 和 push platform run `30074851836` 全绿；三平台均完成
正式 RuntimeLocator/ManagedProcessTree/shared-lock/Core lifecycle、协议协商、generation credential、
stderr 排水/脱敏、故障 fixtures、完整资源清理和锁立即重获。实现与 CI 修正提交、测试计数、
数据零变化、故障矩阵和独立回退见 `docs/specs/runtime-v2/WP-1C-03-protocol-transport.md`。WP-1C-03
登记为 `accepted`；随后激活 WP-1C-04，其范围和回退见
`docs/specs/runtime-v2/WP-1C-04-bundled-core-lifecycle.md`。

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

Stabilizing 记录（2026-07-24）：实现 HEAD `7d4067f` 的 push platform run `30091500680`、
pull_request platform run `30091504687` 和 Unit/UI run `30091504697` 全绿；Windows x64、macOS
arm64、Linux x64 均完成固定 archive、development/packaged RuntimeLocator、真实 bundled Core
lifecycle/fault matrix、连续 generation、完整资源清理、共享锁重获和保护资源摘要门禁。首轮
macOS/Linux 只读摘要暴露并关闭 test fixture bytecode 写入，未放宽 timeout 或断言。最终 accepted
仍要求状态/ADR/总表文档最新 HEAD 自身的三平台、Unit、UI 全绿与最终审查；WP-3-01 不激活。

Accepted 记录（2026-07-24）：stabilizing 文档 HEAD `18a3cab` 的 push platform run `30091910794`、
pull_request platform run `30091915123` 和 Unit/UI run `30091915140` 全绿；PR #147 保持 Draft、
merge state CLEAN、8/8 checks 成功。最终审查确认 WP 白名单外 tracked diff、用户保护目录 tracked
diff、精确 Shell/Core 进程、验收/staging 临时目录和 P0/P1 均为 0；固定 archive、三平台
development/packaged RuntimeLocator、真实 lifecycle/fault matrix、连续 generation、完整资源释放、
锁立即重获和前后摘要证据完整。实现、CI 修正、逐平台摘要与独立回退见
`docs/specs/runtime-v2/WP-1C-04-bundled-core-lifecycle.md`；WP-3-01 保持 planned，未在本次激活。

主要结果：在 Windows x64、macOS arm64、Linux x64 使用目标 bundled Python 启动真实 Core Host，完成真实进程树、hello、initialize、readiness、Snapshot、health 和 shutdown，并冻结仅供 lifecycle 使用的最小接口。

强制前置：WP-1C-03 accepted；必须通过 WP-1P-02 的三平台 `RuntimeLocator` 和 WP-1P-04 的进程树 backend，不得恢复硬编码 `runtime/python.exe`。

允许能力：

- bundled Python 定位、环境构造和 release 资源路径。
- 真实 Core Host lifecycle、Core crash、完整树回收、锁重获和 generation 资源清理。
- Phase 1C lifecycle 端到端测试和协议 golden fixtures。
- lifecycle 接口文档与变更控制记录。

明确禁止：

- 不接入聊天和 Assistant 领域服务。
- 不为未来 Named Pipe、Unix Domain Socket 或代码生成平台提前实现抽象。
- 不扩展协议为通用业务平台，不建设 Router、Operation、业务优先级或资源系统。

退出证据：

- 开发和 release 环境均使用目标 Python 完成全链冒烟。
- lifecycle fixture 可由 Rust 和 Python 共同读取。
- 正常、Core crash 和强制回收后，完整进程树、pipe/thread 和 generation 资源残留为零，应用锁可立即重获。
- `data/`、角色、配置和历史没有非预期变化。
- ADR-0002 完成 Phase 1C 的 `Technically Validated` 前置证据。
- 后续破坏性 lifecycle 修改必须暂停功能开发并更新 ADR/fixtures。

独立回退：恢复到 WP-1P 已验证的开发 RuntimeLocator/Fake Core 路径，不影响 legacy Qt；不得回退为 Windows-only 硬编码路径。

## 9. Phase 3 提前切片：首个真实 Assistant 消费者

### WP-3-01：无 Qt Assistant Adapter 与真实 readiness

激活记录（2026-07-25）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
前置：WP-1C-04 accepted；WP-1P-05A accepted 提交 be3bb34；设计 HEAD b6f343a
设计规格：docs/specs/runtime-v2/WP-3-01-qt-free-assistant-adapter-readiness.md
实施计划：docs/archive/plans/runtime-v2/2026-07-25-wp-3-01-assistant-adapter-readiness.md
独立计划审查：.superpowers/sdd/wp-3-01-plan-fast-review.md；最终 Critical/Important/Minor 均为 0，Approved
允许生产路径：app/config/{visual_effect.py,core_config_reader.py,character_loader.py,models.py,model_slots.py}；app/core_host/{assistant_adapter.py,server.py,__main__.py}；app/llm/api_client.py；app/core/chat_pipeline.py；app/agent/{__init__.py,runtime.py,memory_recall.py}；app/ui/{theme.py,window_backdrop.py}；desktop/src-tauri/src/{core_host_runtime.rs,core_supervisor.rs,phase_1c_core_host_acceptance.rs}
允许测试/fixture/CI：tests/unit/test_core_host_*.py；tests/integration/test_core_host_*.py；tests/unit/test_agent_runtime.py；tests/integration/test_chat_pipeline.py；tests/fixtures/runtime_v2/wp_3_01/**；tests/unit/test_runtime_v2_platform_workflow.py；.github/workflows/runtime-v2-platform-foundation.yml
允许文档：本总表、上述设计规格与实施计划；后续仅以 docs-only 提交登记 stabilizing/accepted 证据
明确禁止：app/core/bootstrap.py、app/core/app_context.py、app/core/extensions.py、ResourceManager、Memory/curator、builtin/desktop Tools、app/agent/mcp/**、app/plugins/**、plugins/**、app/voice/**、history/storage/runtime events/visual observation、main.py、legacy_qt_main.py、desktop/frontend/**、Router/Gateway/Operation/chat Rust/WebView、third_party/**、tools/mcp/**、任何 package manifest/lockfile、新依赖、Provider 网络与真实聊天
数据写入政策：data/** 是正常可写运行时状态，允许任务范围内的产品写入；验收审计场景声明的预期/禁止写集，不要求沿用激活前整目录 byte/mtime/hash 基线。不得清理、截断、恢复或删除无关用户数据；破坏性故障注入使用隔离临时根，characters/**、runtime/** 仅在任务明确涉及时修改
接口：空生产 core.initialize；懒加载单一 AssistantAdapter/CoreConfigReader；真实未运行 Session；五字段 currentCharacterSummary；activeInteractionSummary=null；全部 readiness retryable=false；generation credential 仅经既有 framed IPC；Rust shutdown successful-write 起共享 5000ms，3000ms graceful 包含其中
故障矩阵：严格配置/版本/角色/Provider shape、fallback 与坏可选包优先级、secret/generic serializer、禁止域 import/fail-if-called、重复 initialize/shutdown/EOF/writer/close/race/old worker、连续 generation、一个/多个后代、crash/强杀/共享 deadline 和完整资源归零；逐项见设计规格与实施计划
验收环境：本机 macOS arm64 bundled runtime；GitHub windows-2025 x64、macos-15 arm64、ubuntu-24.04 x64；Python/Cargo 使用 PYTHONDONTWRITEBYTECODE=1；不安装依赖
CI：设计 SHA b6f343a 的 PR platform run 30122282238 与 Unit/UI run 30122282057 成功；docs-only push 未命中 platform push path filter。生产提交必须由对称 push/PR filters 触发三平台，并显式执行新增 core_host pytest
计划提交：按实施计划 Task 1-6 分别执行 TDD、独立任务复审和单一目的中文 Conventional Commit；完成后另做 docs-only stabilizing 与 accepted
回退：先停止并验证所有 generation/受控树/pipe/thread/temp/锁归零，再按 Task 6→1、激活提交逆序 git revert；恢复 WP-1C-04 fake readiness；不删除、恢复或改写既有用户日志/cache/migration/Qdrant/plugin data
```

接受记录（2026-07-26）：

```text
状态：accepted
实施范围：激活提交 5878f61bf；Task 1-6、稳定化修复及政策同步截至 8063f2066
三平台证据：Task 6 候选 ea32cf823 的 Runtime v2 platform workflow push run 30164771596 与 PR run 30164833465 全绿；Windows x64、macOS arm64、Linux x64 均显式执行 core_host pytest 和原生 Shell/Core lifecycle
自动测试：ea32cf823 本地 Core Host 177 passed、frontend 18 passed、Rust 150 passed/23 ignored；c630575a4 本地 Runtime Python 189 passed、tests/unit 1106 passed/6 skipped、Rust RuntimeLocator/Phase 1C/CoreHostRuntime 定向及完整 cargo test 153 passed/23 ignored；cargo fmt 与 git diff --check 通过
安全与资源：ready 零 Provider 网络调用、无 Qt/禁止领域 import；配置与 fixture 写集受控；正常、crash、close throw/block、强制 recovery、一个/多个 TERM-ignoring 后代均由受控树接管，root/后代/pipe/fd/handle/thread/temp 归零，锁可立即重获
稳定化修复：c630575a4 冻结 PyYAML import artifact，移除 CI 对受验 Runtime 的 pip/site-packages/_pth 修改，并补齐真实 fault matrix、连续 generation、stale identity 与 expired-deadline recovery；8063f2066 将 data 全目录零变化纠正为按所有权审计预期/禁止写集
项目负责人接受的剩余风险：c630575a4 自身尚无原生三平台 workflow 结果；它没有已知失败且本地相关与完整测试全绿。推送后的同 SHA 三平台运行作为发布监控，不再触发重复 review；仅可复现且可归因的 P0/P1 或退出条件回归暂停后续 WP 并重新打开 WP-3-01
非目标：未增加聊天、Router、Gateway、Operation、Memory、MCP、插件、TTS、UI、Provider 网络调用或第二生命周期根
回退：先停止并验证全部 generation、受控树、pipe/thread/temp 和锁归零；按 8063f2066、c630575a4、Task 6→1、5878f61bf 逆序 git revert；不删除、恢复或改写用户 data、角色、runtime、日志、cache 或 migration 工件
```

主要结果：`app.core_host` 通过薄 Adapter/Facade 使用现有 Sakura Assistant 领域服务，建立当前角色、Assistant Session、Chat Pipeline 和基础 Provider，并表达真实 ready/setup_required/degraded。它在 WP-1C-04 后立即执行，先验证 bundled Core lifecycle 能承载真实 Assistant 初始化和确定性释放；不等待 Router 或聊天协议。

架构选择见 `docs/adr/0005-runtime-v2-headless-assistant-adapter.md`；本节只维护 Work Package 范围、
实施和验收事实。

允许能力：

- 读取现有角色与 Core 配置。
- 构建基础 Provider 客户端，不在启动阶段发起远程网络验证。
- 建立最小 Assistant Session 和公开角色 Snapshot。
- 为真实迁移需要增加的无 Qt Adapter 和等价性测试。
- 使用现有 lifecycle hello/initialize/readiness/Snapshot/health/shutdown 验证真实 Adapter，不新增聊天 command。

明确禁止：

- 不重写 AgentRuntime、Memory、插件、MCP、TTS 或配置领域。
- 不创建新的巨型 `application.py`，不重新聚合全部 Assistant 逻辑。
- 既有领域代码只在直接依赖 Qt、不能在受监管子进程运行、阻塞控制/取消/关闭或初始化/释放不确定时才可修改。
- 每个领域修改必须说明 Adapter/Facade 为何不足、业务语义是否改变、legacy Qt 影响和等价性测试；语义变化须独立批准。
- 不接入聊天 UI、聊天 Router、cancel/Gateway 或通用 Operation。

退出证据：

- 无角色、无有效 Provider 和首次配置未完成进入 setup_required，不触发重启。
- 尚未进入所属能力 WP 的可选组件不阻止基础 Session，也不得为填充通用 Snapshot 而提前初始化；本 WP 实际接入的可选读取失败才按既有语义进入 degraded。
- Core Host 导入和运行路径不加载 PySide6 或 Qt UI。
- Adapter 初始化期间 health/shutdown 仍按 lifecycle deadline 处理；正常退出、初始化失败和 Core 强杀后完整树及 Adapter 资源归零。
- 三平台 workflow 必须由 `app/core_host/**` 及本 WP 允许的 Python/Core 领域路径触发，Python/Core-only 提交不得绕过平台门。

独立回退：回退 Assistant Adapter，Core Host 退回假组件 readiness；保留 WP-1C-04 bundled lifecycle、协议安全和三平台进程清理门禁。

## 10. Phase 1D：最小开发与故障可见性

### WP-1D-01：最小生命周期可见性与安全重试

激活记录（2026-07-26）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置：WP-3-01 accepted；激活基线 291a859a7558bc43fa52c1df85c4dd82d75d1af2
允许生产目录：desktop/frontend/；desktop/src-tauri/src/（仅 Shell lifecycle 投影、既有 Supervisor 意图接线和有界退出）
允许测试/文档：desktop/frontend/tests/；desktop/src-tauri/src/ 内联 Rust 测试；tests/unit/ 中仅 Runtime v2 lifecycle/secret 定向测试；本总表
明确禁止目录：.superpowers/；main.py；legacy_qt_main.py；data/；characters/；runtime/；third_party/；tools/mcp/；app/；plugins/；desktop/src-tauri/Cargo.toml；desktop/src-tauri/Cargo.lock；desktop/frontend/package.json；.github/workflows/
验收环境：当前 Windows x64 开发机；仓库既有 bundled Runtime、Node/npm 与锁定 Cargo 依赖；不安装或更新依赖；不修改 manifest/lockfile；platform workflow 仅在生产提交命中既有 paths 且同 SHA 已存在远端 job 时跟踪
状态边界：Shell 只消费 SupervisorState、当前 generation 与既有 Core readiness/Snapshot；不拥有、不改写且不创建第二真相源；旧 generation 与非递增 revision 事件默认忽略
故障矩阵：缺 Runtime、spawn 失败、initialize 卡死、setup_required/degraded/failed、Core crash、自动 backoff/restarting、重复 retry、旧 generation/revision、退出/窗口关闭竞态、秘密扫描与完整资源归零
退出条件：全部 lifecycle/readiness 组合有表驱动投影；失败路径无空白窗口；retry 只提交 LifecycleIntent::Retry 并由既有串行 Supervisor 合并，旧 generation 完整清理前不创建新 generation；setup_required/degraded/failed 不新增自动 retry；diagnostics 只含稳定状态/code、必要版本和批准日志位置；正常/失败/retry/exit 后 Shell/Core/后代/pipe/thread/temp 残留为零
计划提交：docs(runtime): 激活 WP-1D-01 最小生命周期可见性；feat(runtime): 增加最小生命周期状态与安全重试
独立回退：先退出 Shell 并验证全部 generation、受控进程树、pipe/thread/temp 归零；单独 revert 实现提交以恢复 WP-3-01/WP-1C-04 底层 lifecycle 与原 fatal exit，再单独 revert 本激活提交；不清理、恢复或改写用户数据
```

接受记录（2026-07-26）：

```text
状态：accepted
关联提交：激活 be770c73；实现 7a52a2dd
RED/GREEN：前端先以 ERR_MODULE_NOT_FOUND 证明 lifecycle 投影缺失；Rust 先证明 Running generation 的 Retry 没有 StopGeneration；实现后 frontend 22 passed，Rust 159 passed/23 ignored，platform workflow pytest 4 passed
状态矩阵：表驱动覆盖 7 个 SupervisorState × 7 个 readiness 输入；Shell 明确显示 startup、initializing、ready、setup_required、degraded、failed、Core crashed、restarting
状态所有权：Rust 发布既有 SupervisorState、当前 generation 和既有 Core Snapshot readiness/revision；WebView 只投影并忽略旧 generation、错 identity 和旧 revision，不拥有或改写 Supervisor/CoreReadiness
Retry/退出：Retry 只提交 LifecycleIntent::Retry；Running 发出一次串行 StopGeneration，Stopping 合并重复意图，Spawning 忽略重复意图；旧 generation 的树、pipe、waiter/Snapshot owner 完成清理后才创建新 generation；Exit、窗口关闭和 AppShutdown 共用同一 worker；既有 retry budget/backoff 与 5000ms shutdown deadline 未改
Diagnostics：只含稳定状态、CORE_* 稳定 code、Desktop/Core/Protocol 版本和 Sakura application logs 逻辑位置；序列化白名单与秘密测试拒绝 credential、API Key、Prompt、Provider endpoint、模型、私有配置、异常 repr 和用户私有路径
故障与资源：缺 Runtime、重复 Retry、旧 generation/revision、真实 Core 两代、initialize hang、spawn/crash、退出竞态和秘密门均通过；真实 Shell-Core lifecycle harness 通过，Shell/Core 精确进程及验收 temp 残留为 0，受保护 data/characters/runtime 摘要不变
平台证据：本地 Windows x64 完整 frontend/Rust/真实 lifecycle 全绿；实现命中既有三平台 workflow paths，但按本次“不推送”要求未创建 7a52a2dd 的远端 Windows/macOS/Linux jobs。本次指令明确只有可复现、可归因 P0/P1 或退出条件回归阻断，故将该非失败型证据缺口作为已知风险接受；推送后只跟踪同 SHA 明确可归因失败
真实 UI：Windows 应用控制确认真实 Shell 窗口创建；随后检测到用户输入并按安全规则停止争抢，未取得截图。初始 HTML 默认可见、frontend 投影测试与真实 lifecycle harness 已证明失败不依赖 Core 才显示
已知问题：除未推送 SHA 的远端三平台结果和因用户输入中止的截图外，无已知 P0/P1、退出条件缺陷、秘密泄露或资源残留
非目标：未实现 Runtime Repair/下载/安装/替换、通用日志浏览、设置、聊天、Router/Gateway/Operation、Memory、MCP、插件、TTS、新 diagnostics 窗口或后续 WP 生产代码；manifest/lockfile/依赖/协议/Snapshot schema/readiness code/重试预算均未变
回退：先请求 Exit 并确认 Shell/Core/后代/pipe/thread/temp 归零；单独 revert 7a52a2dd 恢复 WP-3-01/WP-1C-04 底层 lifecycle 和 fatal exit，再单独 revert be770c73 恢复 planned；不清理、恢复或改写用户数据
```

接受后纠正记录（2026-07-26）：

```text
状态：accepted（不重新打开功能范围）
关联提交：fd62aa7bd
纠正范围：Windows 原生命中区域为气泡、输入栏和状态条使用带 2px 抗锯齿保护的圆角 region，移除会被 region 硬裁剪的外投影；最小可见性探测改为 220ms 可感知隐藏间隔，并在 timer 创建/主线程调度失败时恢复可见
验证：Windows 圆角透明点/命中点实机断言通过；窗口交互定向测试 7 passed；frontend 22 passed；release build、cargo check、cargo fmt --check 与 git diff --check 通过
边界：未修改 Core、Supervisor、IPC、角色资源、用户数据、Python Runtime、立绘矩形命中或 WP-2 生产能力；因此 WP-1D-01 accepted 结论保持有效
继承测试债务：desktop/tests/windows_pet_interaction_acceptance.ps1 仍有 WP-1A 时期“Shell 不得启动 Python”的过时断言；当前 Shell 按 WP-1C/3-01 正常启动受控 Core，该断言不能作为 WP-2-01 失败证据。WP-2-01 生产实现前必须用单独 test-only 提交移除该旧断言，同时保留 Core/Python 后代身份登记、退出后全部后代归零和 data 预期/禁止写审计
回退：revert fd62aa7bd 可恢复矩形 region、外投影和即时可见性恢复；若回退重新引入已复现的真实交互/可见性缺陷，应将拥有该缺陷的前置 WP 重新置为 stabilizing，而不是用 WP-2 实现掩盖
```

主要结果：在第一条真实聊天前，开发者和用户能理解 lifecycle 失败并安全 retry 或 exit；本 WP 不建设完整 Runtime Repair。

允许能力：

- startup、initializing、ready、failed、Core crashed 和 retry 状态。
- 受控 diagnostics 文本、必要版本/日志位置、显式 exit。
- retry 复用唯一 Supervisor 意图：先清理旧树和 pending lifecycle waiter，再创建新 generation。
- 状态组合和旧 generation UI 事件过滤。

明确禁止：

- 不实现自动 Runtime 修复。
- 不实现在线下载/替换 Runtime、通用日志浏览平台或覆盖所有未来故障的完整诊断 UI。
- 不接入聊天、设置或真实角色业务；不建立第二套重启路径。

退出证据：

- Core 缺失、启动失败、初始化卡死和崩溃均不会产生空白窗口，且错误不泄露 credential、API Key、完整 Prompt 或私密配置。
- 连续 retry 合并为一个有效意图；旧 generation 清理完成前不创建新 generation。
- UI 路由不反向成为 Supervisor 或 CoreReadiness 真相源。

独立回退：回退最小状态/重试 UI，保留底层 Supervisor、Core Host 和 fatal exit；完整 diagnostics/Runtime Repair 留到 WP-5-06 或后续经批准 WP。

## 11. Phase 2：最小可靠聊天 IPC 基础链

### WP-2-01：最小并发 request/response/event Router

激活记录（2026-07-26）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置：WP-1D-01 accepted；激活基线 fd62aa7bd39dcb2a0a40367667d03abb14595196
独立规范：docs/specs/runtime-v2/WP-2-01-minimal-concurrent-router.md
允许生产目录：desktop/src-tauri/src/core_host_protocol.rs；desktop/src-tauri/src/core_host_runtime.rs；可新增 desktop/src-tauri/src/core_host_router.rs；desktop/src-tauri/src/main.rs 仅允许模块声明/现有 lifecycle owner 接线；desktop/src-tauri/src/shell_lifecycle.rs 仅允许 generation Router 所有权、失效和清理接线；app/core_host/protocol.py；app/core_host/server.py；可新增 app/core_host/router.py
允许测试/夹具：上述 Rust/Python 模块内联测试；tests/unit/test_core_host_protocol.py；tests/unit 中新增 Router 定向测试；tests/integration/test_core_host_lifecycle.py；tests/integration 中新增 Router 真实 Host 测试；tests/fixtures/runtime_v2/wp_2_01/；desktop/tests/windows_pet_interaction_acceptance.ps1 只允许纠正过时的 no-Python 断言，不改变窗口交互步骤和通过阈值；本总表、独立规范和 ADR-0002 的 WP-2-01 验证记录
明确禁止：desktop/frontend/；app/core_host/assistant_adapter.py；app/agent/；app/memory/；app/plugins/；plugins/；main.py；legacy_qt_main.py；data/；characters/；runtime/；third_party/；tools/mcp/；Cargo.toml/Cargo.lock；package.json/lockfile；.github/workflows/；WP-2-02 Gateway/cancel/Snapshot 扩展及任何真实 chat.send/chat.cancel 生产接线
协议边界：新增 event wire kind 必须使用新的 protocol minor/capability 协商，不能静默改写已冻结的 2.1 语义；不增加 sequence、通用 Operation、resource token、通用 priority 注册表或完整 progress 模型
资源边界：Rust/Python reader、writer、pending/event registry 和领域 fixture 执行槽全部有界；容量为命名常量并有饱和测试；任何 pipe I/O 不得持有 pending/owner 全局锁；response 和 terminal-shaped fixture event 不得被可丢消息挤出
故障矩阵：并发乱序 response、event/response 交错、重复/未知 identity、旧 generation/credential、pending 上限、writer/event queue 饱和、慢/失败 writer、半帧/EOF/stdout pollution、阻塞 sleep/文件 I/O fixture、health/shutdown 抢占、deadline、窗口退出/retry/Core crash 和完整资源归零
验收环境：本机 Windows x64 使用仓库锁定 Node/Cargo/runtime Python；不安装/更新依赖。Rust/Python golden 和真实 Host 定向门本地执行；生产路径命中既有 platform workflow 时，只有推送后同 SHA 的 Windows/macOS/Linux 结果可作为原生平台证据
提交顺序：先单独修正继承验收脚本，再按 TDD 完成协议 minor/event、Rust Router、Python Router、真实故障门；生产实现完成后登记 stabilizing，候选验收通过再登记 accepted；不得在本 WP 内开始 WP-2-02
独立回退：先使当前 generation 失效并请求 AppShutdown，确认 Shell/Core/后代、reader/writer/dispatcher、pending waiter、pipe/fd/handle 和 temp 全部归零；按相反顺序回退 Router/协议/测试提交，恢复 WP-1D-01 的串行 lifecycle transport；不清理、恢复或改写用户数据
```

稳定化记录（2026-07-26）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
实施提交：8f1cca3a9（更新窗口验收 Core 预期）；b7c405923（protocol 2.2/event 与 Python Router）；213b2af85（Rust Router）；0d7c5e751（并发 handle、故障与资源门禁）
协议与并发：2.1 lifecycle request/response 保持兼容；2.2 event 仅在 transport.concurrent-router capability 协商后启用；共享 Rust/Python fixture 同结论；真实 Python Host 同 generation 两个并发 in-flight health waiter 均按 id 返回
有界与故障：Rust pending/writer/event、Python dispatch/writer/event/fixture 队列和 4 个 fixture 槽均为命名有限容量；覆盖反向 response、event/response 交错、重复/未知 id、错 name、旧 credential、pending/event/writer/fixture 饱和、慢/失败 writer、半帧、EOF、stdout pollution 和关闭竞态
控制隔离：注入式 sleep 与阻塞文件读取 fixture 期间 health 先返回；shutdown 走既有 3000ms graceful/5000ms total deadline，非协作任务仍由受控进程树强制回收
自动测试：.\runtime\python.exe Core Host 定向 181 passed；cargo test --locked 172 passed/23 ignored；frontend 22 passed；cargo build --locked、cargo fmt --check、git diff --check 和 Windows 验收脚本 PowerShell parser 通过
资源与安全：原有真实 Core crash、Retry、连续 generation、Exit、stderr flood、stale credential、stdout pollution、慢/忽略 shutdown 和完整树 finalization 门禁全绿；reader/writer/pending/pipe/thread/handle/temp 由同一 generation owner 清理；credential、私有异常内容和路径不进入公共 response/event
已知问题：本地 Windows 候选已完成；未推送当前 SHA，因此没有同 SHA Windows/macOS/Linux platform workflow 结果，本记录不把该非失败型证据缺口冒充三平台证据
回退：先使当前 generation 失效并执行 AppShutdown，确认完整树和 Router 资源归零；按 0d7c5e751、213b2af85、b7c405923、8f1cca3a9 逆序 revert；不触碰用户数据
```

验收记录（2026-07-26）：

```text
状态：accepted
关联提交：8f1cca3a9、b7c405923、213b2af85、0d7c5e751、e8c27bd1、75ecf205
自动测试：.\runtime\python.exe Core Host 定向 181 passed；cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked 172 passed/23 ignored；npm test --prefix desktop/frontend 22 passed；cargo build --locked、cargo fmt --check、git diff --check、PowerShell parser 全部通过
故障测试：反向 response、event/response 交错、重复/未知 id、错 name、旧 generation/credential、pending/event/writer/fixture 饱和、慢/失败 writer、半帧、EOF、stdout pollution、阻塞 sleep/文件读取、health/shutdown 抢占、Core crash、Retry、Exit 和连续 generation 全部有界通过
真实应用验收：真实 Python Host 通过 2.2 capability hello、两个并发 in-flight health waiter、shutdown；既有 Shell lifecycle/Retry/Exit、受控进程树、stderr 排水、完整 generation 资源清理回归全绿；Windows 窗口脚本完成语法检查，保留 Core/Python 后代登记、退出归零和 data 审计
安全与范围：writer 内部编码失败显式 fail closed 且清理 pending；未接入真实聊天、Assistant、Gateway、cancel、Snapshot 扩展、UI、第二 Core、第二 writer、无限队列或用户数据；credential、API Key、Prompt、异常 repr 和私有路径不进入公共 envelope/事件
已知问题：当前 SHA 未推送，未产生同 SHA Windows/macOS/Linux platform workflow 结果；这是非失败型原生平台证据缺口，不改变本地候选 accepted 结论，推送后仅跟踪同 SHA 可归因失败
回退：先使当前 generation 失效并执行 AppShutdown，确认 Shell/Core/后代、reader/writer/dispatcher、pending waiter、pipe/fd/handle 和 temp 归零；按 0d7c5e751、213b2af85、b7c405923、8f1cca3a9 逆序 revert；不触碰用户数据
```

重新打开记录（2026-08-03）：WP-3V-01 的真实 Windows 组合验收以快速本地 Provider 稳定复现
`chat.completed` 被跨队列提前到 `chat.started` 之前；Gateway 正确拒绝乱序终态后，UI 只收到 started
并永久等待。相同数据集和响应直接驱动 bundled Python Core 时保持
`started -> completed -> response`，因此唯一责任归属 Rust `CoreHostRouter` 的普通/关键双队列读取顺序。
项目负责人已批准按 WP-3V-01 冻结停止条款重新打开本 WP。稳定化范围只允许
`core_host_router.rs` 内的最小有界顺序修复和内联回归测试，不修改协议、Gateway、Core、产品 UI、
用户数据或 Provider 时序；任务契约为 `harness/tasks/WP-2-01.json`，activation 为
`harness/activations/WP-2-01/0001.json`，基线 `d47cd414ce37249ca94cd93812deb6cacfeacc8f`。
失败事实与归因记录见 `docs/records/audits/WP-3V-01-ROUTER-ORDERING-DEFECT.md`。

重新稳定化候选记录（2026-08-03）：实现提交 `fab46beb` 以共享单调序号合并普通/关键事件队头，
保持 wire order，同时用显式计数保证缓存队头仍占用原命名容量。TDD 回归证明快速
`chat.started -> chat.completed` 不再重排，Router 10/10；完整 Rust 单线程 239 passed/24 ignored；
WP-3V-01 真实 Windows 组合验收通过（4 次 Provider 请求、1 次 Core 强杀、唯一取消终态、新代水合、
Legacy oracle 回读、敏感证据和进程残留均为 0）；Harness required profiles 四项全绿，自动项
20 passed/0 failed，保持负责人复核 pending。首次默认并行完整 Rust 的 3 个共享 Windows mutex
失败不涉及 Router，精确清理遗留测试树后以稳定单线程模式全绿。当前不标记 accepted，也不恢复
WP-3V-01；负责人验收后另做状态与 activation 提交。

重新验收记录（2026-08-03）：项目负责人明确确认 `fab46beb` 的 Router 顺序稳定化验收通过，批准
本 WP 重新标记 accepted 并恢复 WP-3V-01。接受范围仅为已审计的 wire-order 合并与容量计数修复；
不扩大协议、Gateway、队列或产品功能。最终候选证据沿用上述 Router 10/10、Rust 单线程
239 passed/24 ignored、真实 Windows 组合验收通过、Harness 自动项 20 passed/0 failed 和进程残留 0。
若后续再次复现终态超越 started、容量突破或资源残留，必须重新打开本 WP，不在验证包中规避。

主要结果：为一个真实聊天消费者建立不阻塞 lifecycle 的最小 Router；不建设通用任务调度平台。

允许能力：

- Rust 独立 stdin writer、独立 stdout reader 和 pending request map。
- Python 独立 reader、dispatcher 和有界单 writer queue；领域任务不能直接写 stdout。
- 多个 in-flight request、乱序 response、event/response 交错和 request identity 校验。
- 新 generation 建立时旧 request、response、event 和 pending waiter 立即失效。
- control 与聊天形状的阻塞 fixture 隔离，health 和 shutdown 不等待聊天任务。
- 队列有界，并为 response/terminal event 保留不可被非终态消息挤出的容量。

明确禁止：

- 不接入真实 Assistant、设置、TTS、Tools 或截图。
- 不实现完整 control/interactive/background 三级优先级、通用 worker process 或任务图。
- 不实现通用 Operation、Gateway、resource token 或完整 progress 背压平台。

退出证据：

- 并发响应乱序和事件交错不串请求。
- reader、writer 和 pending registry 均有界且可清理。
- 旧 generation response 不能完成当前 waiter。
- 阻塞 sleep/I/O 聊天 fixture 期间 health/shutdown 可响应；非协作任务仍由进程树 deadline/强杀兜底。
- terminal response/event 在队列压力和关闭竞态中不丢失。

独立回退：恢复到最小 lifecycle transport，不影响 Supervisor。

### WP-2-02：最小聊天取消、Gateway 与 Snapshot 边界

主要结果：只为第一条聊天垂直链冻结可授权、可取消、可重新水合的最小外部边界。

允许能力：

- 固定 Gateway allowlist：`chat.send`、`chat.cancel`；未知 command 默认拒绝。
- 固定聊天事件：`chat.started`、`chat.completed`、`chat.failed`、`chat.cancelled`。
- 每次聊天由 Rust 分配唯一 request/operation identity；Rust 注入 generation、credential、deadline 和协议字段。
- WebView 只能持有 Rust 返回的取消 handle，不能伪造 generation、request ID、deadline、priority 或 credential。
- 聊天每次最多一个终态；完成/失败/取消竞态、重复取消和晚到事件幂等。
- 窗口关闭、Core restart 和 generation 切换清理 pending waiter；取消不阻塞 health/shutdown。
- 最小 Snapshot 只包含 generation、revision、readiness、当前角色公开摘要、当前聊天交互摘要和 UI 重新水合所需最小状态。

明确禁止：

- 不建设面向 Tools、MCP、Memory、导入或未来任务的完整 Operation 平台。
- 不冻结通用 priority 注册表、所有未来 component 类型或通用 Snapshot component model。
- 不实现截图、音频、角色导入资源、通用 resource token、schema 代码生成或完整多等级背压。
- 不接入真实 Assistant 领域代码；本 WP 使用窄 Fake Core/fixture 验证边界。

退出证据：

- send/cancel/complete、send/cancel/fail、重复取消和晚到旧 generation 事件均只有一个可见终态。
- 未知 command、错误窗口、伪造字段和超限 payload 默认拒绝，且安全失败不妨碍诊断和退出。
- 最小 Snapshot revision/generation 失配触发完整重取；Rust 不推导或修改 Python 业务对象。
- 队列压力下 chat 终态、cancel response、health 和 shutdown 不丢失。

独立回退：禁用聊天 Gateway/取消/Snapshot 扩展，保留 WP-2-01 Router 和只读 lifecycle 状态。

验收记录（2026-07-26）：

```text
状态：accepted
关联提交：f8c9cd22、157dcc11、6c36a1a、96787830、17d296a6
自动测试：Core Host/Python 定向 190 passed；locked Rust 177 passed/23 ignored；frontend 22 passed；build、fmt、diff-check 和 workflow 契约通过
故障测试：聊天取消/完成/失败竞态、唯一终态、terminal 顺序、generation/handle 失效、队列压力和五字段 Snapshot 全绿；Rust Router 写前拒绝旧 generation/credential 后健康 Core 可正常关闭
真实应用验收：Windows Shell + bundled Core normal/crash/reacquire、锁冲突、readiness 2.1 兼容矩阵、2.2 Snapshot 和 native fault matrix 115.9 秒通过；characters/data/runtime 摘要零变化
CI：6c36a1a 的 PR platform run 30190007246 捕获三平台临时根 canonical/分隔夹具问题；96787830 修复，17d296a6 取消功能分支 push 与 PR 的重复平台运行；新 HEAD 三平台结果作为推送后监控
已知风险：新 HEAD 尚无同 SHA 三平台结果；若出现可归因 P0/P1 或退出条件回归则重新打开本 WP，不以本地 Windows 结果冒充其他平台
非目标：未接真实 Assistant 聊天、历史、UI、streaming、Tools、Memory、MCP、插件、TTS 或通用 Operation
回退：停止并清空当前 generation 全部 Router/Gateway/进程资源后，按 96787830、157dcc11、f8c9cd22 逆序 revert；17d296a6 独立回退；不触碰用户数据
```

## 12. Phase 3：产品 UI 外壳与基础聊天垂直链

### WP-3-02：无 UI 的真实聊天 Core 垂直链

主要结果：让真实 Sakura Assistant 成为 Router、取消、Snapshot 和 generation 边界的首个真实消费者；通过 Rust acceptance harness 完成无 UI 聊天链。

激活记录（2026-07-26）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3-01 accepted；WP-2-02 accepted
独立设计：docs/specs/runtime-v2/WP-3-02-headless-real-chat-core.md
允许边界：AssistantSession.pipeline、确定性 Provider、角色级 ChatHistoryStore、WP-2-02 RealChatBoundary/Gateway/取消/Snapshot、Rust 无 UI acceptance
明确禁止：desktop/frontend、聊天 UI、TTS、Tools/确认、Memory、MCP、插件、截图/视觉、主动事件、streaming、通用 Operation/resource token、第二 Core 或第二生命周期根
数据政策：history 是本 WP 允许的 append-only 产品写入；破坏性故障仅使用隔离临时根，回退不得删除或改写用户 history
回退：停止并确认当前 generation/operation/Provider/Router/进程树/IPC 资源归零，逆序 revert WP-3-02，恢复 WP-2-02 fixture-only 与 WP-3-01 readiness
```

稳定化候选记录（2026-07-26）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
自动测试：Python 全量 1717 passed、15 skipped；前端 22 passed；locked Rust 179 passed、23 ignored；Rust debug build、fmt、diff-check 通过
故障测试：Provider 状态/连接/格式/兼容回退、取消竞态、history、shutdown/EOF 与唯一终态矩阵通过
真实应用验收：Windows Shell + bundled Python Core lifecycle harness 通过，受保护 characters/data/runtime 摘要不变
已知问题：无可复现 P0/P1；等待本候选同 SHA Windows/macOS/Linux platform workflow
回退步骤：停止并确认 generation/operation/Provider/Router/进程树/IPC 资源归零，按 7c691962、116e64f7、452343e9 逆序 revert；不删除或改写 history
关联提交：452343e9、116e64f7、7402b9d7、7c691962
```

验收记录（2026-07-26）：

```text
状态：accepted
实现候选：b835ef2ca66a33f98eb0b4339c1ccb51abcd5e91
自动测试：完整 Python 1718 passed/15 skipped；Core Host 平台门禁 220 passed；frontend 22 passed；locked Rust 179 passed/23 ignored；py_compile、fmt、diff-check 通过
故障与资源回收：Provider/格式/取消/history/shutdown/EOF/唯一终态全矩阵通过；纯净打包 Core lifecycle/fault matrix 与资源树只读摘要通过；Windows/macOS/Linux 原生 Shell + bundled Core、受控进程树、pipe、共享锁和 RuntimeLocator 全部归零/通过
CI：同一候选 SHA 的 Runtime v2 platform foundation run 30200669759 成功，Windows x64、macOS arm64、Linux x64 三 job 全绿；Test run 30200669763 成功
缺陷关闭：30194387837 捕获执行期 PySide6 泄漏，30200020224 捕获打包 hello 前业务启动图泄漏；55913158 与 b835ef2c 分别修复，最终三平台运行覆盖并关闭
P0/P1：零；无剩余可复现退出条件缺陷、数据污染或范围扩张
非目标：未接聊天 UI、TTS、Tools/确认、Memory、MCP、插件、截图、主动事件、streaming 或通用 Operation/resource token；WP-3-03 保持 planned
回退：确认 generation/operation/Provider/Router/writer/Core 进程树/IPC/锁资源归零后，按 b835ef2c、55913158、7c691962、116e64f7、452343e9 逆序 revert；不删除、截断、恢复或改写 history
关联提交：452343e9、116e64f7、7402b9d7、7c691962、793d3ea9、c1387d44、55913158、b835ef2c
```

允许能力：

- `chat.send`、`chat.cancel`、`chat.started`、`chat.completed`、`chat.failed`、`chat.cancelled`。
- 真实 Chat Pipeline、基础历史写入和可恢复 Provider 错误。
- 自动测试使用确定性 fake/local Provider，人工验收使用已有开发配置。

明确禁止：

- 不实现 TTS、Tools 确认、截图、主动事件和 token streaming。
- 不将跳过打字机映射为 Core cancel。

退出证据：

- 正常回复、网络错误、格式错误、取消和 Core shutdown 均有唯一终态。
- Provider 网络不可达只影响请求，不改变 Core readiness 为启动失败。
- History 失败时仍可聊天并返回 degraded/不保存提示所需状态。
- 强制终止或 shutdown 不阻塞 health/control，且完整 Core 进程树和 IPC 资源归零。

独立回退：关闭真实 chat command，保留 Assistant readiness 和 Core Host。

### WP-3-03：固定产品 UI 与真实角色表现基线

产品方向纠正（2026-07-26）：原 Fake Core 候选已经完成自动矩阵和 Windows WebView 冒烟，但其
自绘测试 SVG、可收起 composer、bubble auto-hide、idle/bubble/composer/expanded 几何切换和常驻
功能栏与项目负责人确认的最终产品方向冲突。该候选证据保留为技术历史，不再具备 accepted 资格；
WP-3-03 曾退回 `active`，在同一编号内修正产品 UI，并于 2026-07-27 最终验收为 `accepted`。独立实施与验收文档见
`docs/specs/runtime-v2/WP-3-03-fake-core-pet-chat-presentation.md`。

```text
历史候选：27bad4d5；激活：cb348249
历史证据：frontend 44 passed；Python 1719 passed、15 skipped；locked Rust 179 passed、23 ignored
纠正原因：测试立绘、可折叠输入和功能栏不是最终产品形态，不能用该候选继续做视觉验收
当前状态：accepted（2026-07-27 项目负责人最终验收通过）
允许目录：desktop/frontend/**、desktop/src-tauri/src 中角色表现资源与固定窗口/命中所需窄模块、desktop/src-tauri/tauri.conf.json、app/core_host 中当前角色公开表现 DTO 的无 Qt 窄扩展、相关 tests、本文和独立 spec；characters/** 只读
明确禁止：修改角色包源文件、真实 chat Gateway 接线、设置窗口、角色切换持久化、TTS、Memory、Tools、MCP、插件、截图、主动互动、工作室、用户数据写入、通用资源平台
回退：关闭窗口并清理全部表现 timer；回退当前产品 UI 修正后仍可回到历史候选，不删除或改写用户数据
```

最终实现候选记录（2026-07-26 至 2026-07-27）：

```text
状态：accepted（生产实现、本地自动矩阵和 2026-07-27 项目负责人最终验收完成）
实现：固定 816×680 产品包络、常驻气泡/输入、当前真实角色公开表现 DTO、受控同源 PNG 资源协议、debug-only 隐藏验收场景、Fake Core 确定性聊天表现
自动测试：frontend 57 passed；Core Host Python 214 passed；locked Rust 185 passed/23 ignored；cargo fmt、locked build、git diff-check 通过
资源与故障：Sakura/N.A.V.I. 真实 manifest、全部 portrait key、非法 key、traversal、symlink escape、错误扩展/MIME/大小/decode、资源变化、旧 generation；normal/slow/error/long/multi/restart、IME reducer、focus、close、lifecycle、固定几何与命中回归全绿
Windows 候选：100% DPI 下正常产品 N.A.V.I. 冷启动和真实资源链通过；debug-only Sakura/N.A.V.I. 不同宽高比、long 内滚动、multi 表情、error、restart、slow cancel、Unicode 中文输入、reduced motion 和关闭已观察；正常 accessibility tree 不暴露验收控件
已关闭缺陷：protocol minor 2 最小 Snapshot 漏传 characterPresentation；敏感字段扫描误拒绝公开 themeTokens；两项均由正常产品冷启动发现并补回归
最终验收：此前保留的 Windows 系统级 DPI、真实中文 IME、人工鼠标拖动和 Sakura/N.A.V.I. 实机视觉门由项目负责人最终验收结论关闭
工具环境：用户级 npm 启动器缺少 npm-cli.js；已直接执行 package script 对应的 node --test tests/*.test.js 并完整通过
回退：先关闭窗口并确认 Fake Core/typewriter/portrait timer、Core/WebView 后代、pipe 和临时资源归零；独立回退当前角色 DTO/资源协议，再回退固定 UI 与 Fake Core 表现层；不触碰 characters/**、用户配置、history 或其他 data
```

主要结果：在一个固定透明窗口包络内，使用当前真实角色包展示常驻气泡、常驻输入框、默认立绘、
初始消息和主题；确定性 Fake Core 只驱动思考、错误、取消、重连、打字机和真实表情立绘切换。

允许能力：

- 当前真实角色的公开表现摘要和受控立绘资源读取。
- 真实立绘加载、表情切换和简单交叉淡入。
- 常驻 composer 的发送/取消，以及错误和重连状态。
- WP-1D-01 的 startup/initializing/ready/failed/Core crashed/retry/exit 表现。
- 完整回复后的 WebView 打字机和立即跳过动画。
- 固定窗口包络、气泡内部有界滚动、真实主题、DPI、IME 和固定锚点视觉验收。

明确禁止：

- 不接入真实 Provider 或修改 Python Assistant。
- 不接入右键菜单或设置窗口；它们属于 WP-3U-01。
- 不保存角色、主题或布局配置；角色外观窄子集属于 WP-3U-02，其余按设置增量迁移规范归入对应能力 WP。
- 不实现 Live2D、复杂 Canvas、局部模糊或高级动画引擎。

退出证据：

- Fake Core 可以稳定驱动成功、慢响应、错误、取消和重启 UI 状态。
- 默认画面使用真实角色资源；气泡和输入框始终可见，产品 DOM 不存在 composer toggle 或常驻功能栏。
- 所有聊天/生命周期状态保持同一原生窗口包络、立绘锚点、气泡和输入框位置。
- 长文本只在气泡内部滚动；正常画面不显示 Fake Core 标签、几何读数或测试按钮。
- 跳过打字机不发送 Core cancel，CSS 动画不阻塞输入、取消或关闭。

独立回退：回退当前产品 UI 修正，保留 Phase 1A Shell、窗口技术门和历史 Fake Core 表现代码；
不触碰角色源资源或用户数据。

### WP-3U-01：同一 Tauri App 的右键菜单与设置窗口宿主

主要结果：移除桌宠常驻功能栏，通过右键桌宠打开由 Rust 管理的产品菜单，并把现有设置前端迁为
同一 Tauri App 内唯一的 `settings` 普通窗口；独立设计见
`docs/specs/runtime-v2/WP-3U-01-same-app-settings-window.md`，架构选择见
`docs/adr/0006-same-app-settings-host.md`。

允许能力：

- 右键菜单中的显示/隐藏、设置和退出等已具备生命周期所有者的项目。
- 唯一 settings 窗口的创建、聚焦、关闭、未保存拦截和主应用退出联动。
- 复用 `tools/settings-tauri/frontend/**` 的视觉与页面结构，并建立单一规范源。
- 设置能力清单；未迁移页面隐藏或明确禁用。

明确禁止：

- 不再启动 `sakura-settings` 独立进程，不引入第二 Tauri 生命周期根。
- 不把旧设置 Rust stdio HostRpc 或 `app/ui/tauri_settings.py` 的 PySide6 运行时带入 Core。
- 不在本 WP 保存角色、Provider、TTS、Memory、MCP、插件或其他业务配置。

退出证据：右键菜单可重复打开；设置窗口重复打开只聚焦已有实例；设置窗口关闭不退出桌宠/Core；
主应用退出统一关闭设置窗口和 Core；三平台构建和 Windows 真实焦点/IME/最小化门通过。

独立回退：移除右键菜单和 settings 窗口注册，恢复 WP-3-03 固定产品 UI；不恢复常驻功能栏，
不删除旧独立设置工具或修改用户配置。

### WP-3U-02：角色包可见能力与外观设置联动

主要结果：优先迁移角色包中用户直接可见的能力，并让 Phase 3 设置窗口的角色与外观页真实可用；
独立设计见 `docs/specs/runtime-v2/WP-3U-02-character-visible-capabilities.md`。

允许能力：

- 角色名、初始消息、角色主题、默认立绘、表情映射和立绘切换。
- 当前角色外观页的读取、校验、预览、应用/保存和取消回滚。
- 角色选择控件在本 WP 必须隐藏或明确禁用；运行中角色切换仍由 WP-5-03 完成。
- 复用并抽取旧设置中无 Qt 的 DTO、校验与兼容保存语义。

明确禁止：

- 不提前开放 TTS、Memory、Tools、MCP、插件、截图、主动互动、完整首次设置或 Studio。
- 不保存 `current_character_id`，不实现角色切换或 Session 重建。
- 不直接从 WebView 写 `data/**` 或 `characters/**`，不向 WebView 暴露裸本地路径。
- 不在旧 Assistant 对象图上逐字段修改角色。

退出证据：至少使用 Sakura 与 N.A.V.I 两个真实角色包验证不同宽高比、主题、初始消息和表情切换；
设置保存失败原子回滚；取消恢复打开前预览；旧 generation 的立绘资源/回调不能覆盖当前 UI；legacy Qt
仍可读取批准的兼容外观配置。

独立回退：关闭角色外观页的保存命令，设置窗口退回能力门控壳；保留 WP-3-03 的
当前角色只读展示，不删除或恢复用户数据。

### WP-3S-01：供应商与模型设置纵向链

激活记录（2026-07-29）：

```text
状态：active（激活时为当前唯一 active/stabilizing Work Package）
前置条件：WP-3U-02 accepted
独立设计：docs/specs/runtime-v2/WP-3S-01-provider-model-settings.md
允许目录：app/config/provider_model_settings.py；app/core_host/provider_settings.py、server.py；app/llm/api_client.py；desktop/src-tauri/src/core_host_runtime.rs、product_shell.rs、shell_lifecycle.rs、main.rs；desktop/frontend/settings/capability-shell.js、provider-model-runtime.js、settings.js；tests/fixtures/runtime_v2/wp_0_02/dataset/data/config/api.yaml；对应 Python/frontend/Rust 测试、Harness 注册；ADR-0003、WP-0-02 baseline、本节和 settings incremental spec 的状态/证据记录
明确禁止目录：characters/**、data/** 真实用户数据、third_party/**、tools/mcp/**、plugins/**；除 api.yaml Provider/模型当前 schema 外不得写 system_config.yaml、characters.yaml、mcp.yaml、plugins.yaml、Memory、TTS 或 Runtime v2 ui.json
数据政策：只在 system_config.yaml.config_version == 4 且 api.yaml 为合法当前 mapping 时，允许 Python 配置域保留 unknown fields、未修改 secret bytes 和非目标域后单次原子替换 api.yaml；写入包含 api_profiles、聊天/视觉 model_slots、当前聊天槽的 llm 兼容镜像与已批准生成参数；损坏/旧/未来 schema 一律只读；故障注入只用隔离临时根
验收环境：Windows 真实 Tauri/WebView2（中文 IME、关窗、取消、受控 restart、重新打开）；runtime/python pytest；canonical frontend node:test；locked Rust test/fmt；同一候选 SHA Windows/macOS/Linux 公共门禁
故障矩阵：Provider 字段/重复 ID/槽引用、密钥 keep/replace/clear、损坏/旧/未来 schema、权限/temp/replace、网络成功/认证失败/超时/取消/关窗、旧 generation、重复保存、Core crash/restart 失败、secret scan、Qt -> v2 -> Qt 回读
关联 ADR：ADR-0007 feature 级纵向迁移；ADR-0003 Phase 3 api.yaml Provider/模型兼容写；ADR-0001 Supervisor 受控重建与退出 deadline；ADR-0002 generation/request identity 与窄 command
计划提交：docs/test 数据门；feat Python 配置领域；feat Core/Rust Gateway；feat canonical frontend；test/验证与稳定化记录
回退：先把 providers.*、model.* feature 改为 read_only/unavailable 并停止新探测，等待在途请求唯一终态和 Core/临时文件归零，再逆序 revert WP-3S-01 提交；不删除、恢复或重写用户 api.yaml
```

稳定化记录（2026-07-29，本地工作树候选，尚未绑定提交 SHA）：

```text
实现结果：capability schema v2、公开 Provider DTO、credential keep/replace/clear、聊天/视觉模型槽、整域校验和一次原子替换、legacy llm 兼容镜像、有界可取消探测、受控 Core restart、新 generation 回读均已接通；memory_curation 与非目标域保持未开放
数据证据：旧/未来/损坏 schema 失败安全；unknown top/provider/model/model-slot 与非目标 TTS 保持；未修改 secret bytes 保持；Qt service -> v2 -> Qt service 回读通过；原子替换失败返回 CONFIG_SAVE_FAILED 且原文件不变
生命周期证据：探测在 Tauri blocking worker 执行，不阻塞主事件线程；取消、关窗、旧 generation、重复保存串行化、restart 后重新绑定新 identity 已有自动门；真实 Core Provider get/save 往返测试通过
验收阻断回归：新增但未完成模型列表的非当前 Provider 曾使 Core 全局进入 PROVIDER_SETUP_REQUIRED，并连带丢失角色发布；现仅以实际聊天槽的可解析性决定 Provider 启动状态，固定“有效聊天槽 + 空模型 Provider 草稿 + N.A.V.I.”仍 READY；新 Core generation 会通知主桌宠重载角色，角色 DTO 不可用的冷启动也会跳过无效占位图片等待并 reveal 可恢复错误态；首次修复复验暴露 WebView reload 从 revision 1 重新计数、被保留旧 revision 的原生布局会话拒绝，现从原生已应用 revision 的下一值继续，真实 Windows 整页 reload 前后均确认 N.A.V.I. 名称、主题和立绘可见
自动门：Python unit Harness 1182 passed/6 skipped（temp/harness/20260729T163423Z-unit.json）；canonical frontend 99 passed；locked Rust 210 passed/23 ignored；smoke Harness 2/2 cases、25 tests（temp/harness/20260729T163442Z-smoke.json）；runtime-v2-shell Harness 7/7 cases、166 tests（temp/harness/20260729T170408Z-runtime-v2-shell.json）；cargo check --locked、cargo fmt --check、git diff --check 通过
真实验收待办：Windows Tauri/WebView2 中文 IME、Provider 增删改、已保存密钥占位/替换/显式清除、模型检测/连通性、保存时受控 restart、新 generation 回读、探测中关窗、失败恢复与重新打开；随后固定候选 SHA 并通过 Windows/macOS/Linux 公共门禁
状态结论：实现和本地自动门完成，因真实 Windows 与同 SHA 三平台证据尚未由项目负责人确认，保持 stabilizing，不标记 accepted
```

验收记录（2026-07-31）：

```text
状态：accepted
项目负责人结论：项目负责人在当前开发会话中明确声明“WP-3S-01 我亲自验收通过了”，并授权直接开始后续 Harness 改造
证据边界：保留下方 stabilizing 记录中的既有自动证据；本记录不补写负责人未提供的设备组合、CI run ID 或候选 SHA 细节
P0/P1：项目负责人验收结论未报告新的 P0/P1 或退出条件缺陷
后续：允许按总表激活 WP-H-01；仍不得开始 WP-3-04，直至 WP-H-01 accepted
回退：如后续出现可复现且可归因于 WP-3S-01 的缺陷，按治理规则重新打开该责任 WP；不得以 WP-H-01 掩盖产品缺陷
```

主要结果：把设置迁移从一次性 Phase 5 任务改为持续纵向交付，并首先开放真实聊天直接依赖的
“供应商”和“模型”页面；完整设计、数据安全门、feature 级 capability、故障矩阵和回退见
`docs/specs/runtime-v2/settings-incremental-migration.md` 第 6 节；架构选择见
`docs/adr/0007-incremental-settings-feature-migration.md`。

强制前置：WP-3U-02 accepted。激活前还必须更新 ADR-0003 与 WP-0-02 的 Phase 3 配置写入允许集合，
冻结 `data/config/api.yaml` 当前 schema 的 unknown-field preservation、未来 schema 只读、密钥不回显和
Qt -> v2 -> Qt 夹具；不得以当前旧设置服务“已经能写”为由绕过。

允许能力：

- Provider 公开配置、配置完成状态、模型目录和模型槽的最小 DTO。
- Provider 新增/编辑/删除、密钥保持/替换/显式清除和模型槽逐字段校验。
- 有界且可取消的模型列表/连通性操作；使用已保存密钥时不把密钥返回 WebView。
- Python 配置领域的原子保存、明确 change plan 和 Supervisor 受控 Core restart。
- Provider 缺失导致 `setup_required` 时聚焦对应设置页；不扩展为完整首次设置或 Studio。
- section 级 capability 向 feature 级门控演进；未知 feature 失败安全禁用。

明确禁止：

- 不整体迁移 `app/ui/tauri_settings.py`、旧 Rust stdio HostRpc 或 Qt/线程/进程宿主。
- 不提前接入 Memory、Tools、MCP、插件、TTS、截图、主动互动、角色切换、Studio 或导入导出。
- 不建立跨 `api.yaml`、`system_config.yaml`、`characters.yaml` 和 Runtime v2 `ui.json` 的保存事务。
- 不把密钥写入 manifest、Snapshot、event、response echo、普通日志、错误详情或证据工件。
- 不提前冻结通用 Operation；模型列表和连通性是本设置域的窄 command。

退出证据：Provider/模型完整字段矩阵、密钥语义、原子失败、权限、损坏/未来 schema、网络成功/认证
失败/超时/取消、设置关窗、旧 generation、受控 restart 和重新打开一致性通过；legacy Qt 创建配置 ->
v2 读取/修改 -> legacy Qt 回读通过且未知字段/未修改 secret bytes 保持；Windows 真实设置输入与同一候选
SHA 三平台公共门禁通过；P0/P1、credential 泄漏、请求/进程/临时文件残留为零。

独立回退：禁用 `providers.*`/`model.*` feature 并退回只读，停止新的 Provider 网络探测，回退
Gateway/Core Adapter/canonical frontend 接线；不删除、恢复或重写用户现有 `api.yaml`。

### WP-H-01：Agent Development Harness Foundation

激活记录（2026-07-31）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3S-01 已由项目负责人明确验收并标记 accepted
契约准备提交：642c1b005550e41e3b16838e086c8c5ff2d13e44
base_ref：895adb987bab5c3a4adf26e43794addde12ae342（激活提交；契约内该字段在后续 bootstrap 记账提交固定）
允许目录：harness/**；tests/unit/test_harness_*.py；AGENTS.md；本 WP 的 ADR/Spec/Plan/Devdoc/record；docs/plans/runtime-v2/** 的治理与本节；两个既有 workflow 的增量 Harness 接线
明确禁止目录：app/**；desktop/** 产品实现；plugins/**；runtime/**；tools/mcp/**；data/**、characters/**、third_party/** 受保护
验收环境：仓库 runtime Python；stdlib；临时 Git 仓库；本地 docs/smoke/unit；现有 Node/Rust 环境；GitHub Actions 既有平台环境
关联 ADR：ADR-0008
计划提交：test(harness) 失败矩阵；feat(harness) 任务门禁；ci(harness) 当前分支自测；docs/record 收口
bootstrap：新命令尚不存在，首次实现前使用现有 docs、smoke 和定向 pytest；命令转绿后关闭该例外
回退：逆序回退 CI、CLI/checks、tests 与激活记录，保留原 list/run profiles，不触碰产品代码或真实数据
```

插入理由：本 WP 是仓库基础设施，不是 Sakura 产品能力。它依赖 WP-3S-01 accepted，并在 WP-3-04 前
建立机器可执行开发门禁。生产实现必须遵守上述冻结范围。

稳定化记录（2026-07-31）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
实现提交：eac7890eadd08309d91c0edbe2ccc65b303d9b3c（核心与测试）；6ce3a46a6022e7db2bd062534a022420f32d73e1（CI 与启用态文档）
RED/GREEN：新增测试首次因 harness.git_state 不存在在收集阶段退出 2；实现后定向矩阵 39 passed
干净仓库 verify：preflight 12/12 检查通过；scope 无越界、禁止、受保护、依赖、测试删除或契约变化；docs 2/2、smoke 3/3、unit 575 passed/1 skipped；报告 schema v1 有效；退出码 3，仅人工审查 pending
扩展回归：runtime-v2-shell 7/7 cases 通过，包括 frontend 68、Provider/模型 25、Rust 角色外观 8、角色表现 8、产品窗口 7、几何 16、交互 15
本地工作树故障证据：预存未跟踪 .codex/environments/environment.toml 被列为 out-of-scope，verify 在 0.223 秒内退出 1 且 profiles 为空；没有删除、忽略或扩大契约
CI：当前分支 push 已配置专用 Harness v1 job；远端 Actions 尚未运行，不把配置存在记录为远端通过
人工验收：冻结契约和 bootstrap 例外关闭仍待项目负责人审查；Agent 不代填通过，不标记 accepted
P0/P1：自动矩阵未发现；人工门和远端 CI 结果待确认
回退：逆序 revert 6ce3a46、eac7890；保留 list/run、既有 profiles 和产品测试，不触碰产品代码或真实数据
```

主要结果：把 ADR、Spec、Work Package、AGENTS.md、Tests 与现有 profile runner 串为确定性的任务流程：

```text
解析当前 Work Package
-> preflight 契约与依赖
-> check Git 范围、受保护路径和依赖
-> required profiles 与自动验收
-> 人工验收汇总
-> 原子 JSON 报告
-> CI 重验同一规则
```

允许目录：`harness/**`；`tests/unit/test_harness_*.py`；`AGENTS.md`；本 WP 的 ADR/Spec/Plan/Devdoc/
record；`docs/plans/runtime-v2/**` 中仅限治理、索引和本节；`.github/workflows/test.yml` 与
`.github/workflows/runtime-v2-platform-foundation.yml` 中仅限增量 Harness job、分支/path filter 和在
既有环境准备后调用相同 profile。精确清单以激活时冻结的 `harness/tasks/WP-H-01.json` 为准。

明确禁止目录与能力：`app/**`、`desktop/**` 产品实现、`plugins/**`、`runtime/**`、`tools/mcp/**`；
`data/**`、`characters/**`、`third_party/**` 全部受保护；不实现 WP-3-04 或任何产品能力；不新增依赖、
远程服务、数据库、通用工作流、多 Agent 调度，不删除或弱化既有测试和三平台门禁。

依赖：WP-3S-01 accepted；ADR-0008；WP-H-01 spec；现有 Harness runner/manifest/profile 报告契约。

自动验收：现有 `list/run` 和 profile 报告兼容；current/preflight/check/verify 单元与临时 Git 仓库矩阵；
schema、Work Package 表、四类 Git 变化、依赖、冻结契约、原子报告、退出码、编码/空格/Unicode 路径；
docs、smoke、unit 以及环境允许时 runtime-v2-shell；当前分支 push 的专用 Harness 自测 job。

故障测试：畸形/冲突契约、未知字段/schema、坏文档/profile/base ref、零或多个 current、依赖未 accepted、
staged/unstaged/untracked/committed 越界、删除/重命名、受保护路径、未授权 manifest/lock、契约放宽、
profile timeout/失败、报告 replace 失败和窄控制台编码。前置失败不得继续昂贵 profile。

人工验收：项目负责人审查冻结契约、一次性 bootstrap 例外已关闭、CI 与本地使用同一规则；Agent 只能
记录 pending/实际结论，不得代填通过或擅自标记 accepted。

回退：先回退 CI 任务入口，再回退 verify/check/preflight/current 和任务模块，保留原有 runner、
`suites.json`、profile、pytest/Node/Rust/实机验收与 JSON profile 报告；不触碰产品数据或角色资源。

与后续关系：WP-H-01 accepted 后，WP-3-04 才可激活，并必须使用任务契约执行 preflight/check/verify。
本 WP 不提供绕过 WP-3S-01 人工/三平台验收的理由，也不替代任何产品 Work Package 的退出门。

最终验收记录（2026-07-31）：项目负责人在当前开发会话中明确确认 WP-H-01 验收通过，并授权标记后
开始 WP-3-04。精确声明与证据边界见
`docs/records/audits/WP-H-01-OWNER-ACCEPTANCE.md`；总表据此标记 `accepted`。

### WP-3-04：真实聊天接入已冻结桌宠 UI

激活记录（2026-07-31）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-H-01 已由项目负责人明确验收并标记 accepted
base_ref：4d3e34fc10ad770847694c9203f8e562c182d9f2（2026-08-02 第五次契约修订继续沿用原始基线）
允许文件：冻结于 harness/tasks/WP-3-04.json；限真实聊天 WebView/Rust 桥、逐段/启动/立绘/当前会话回复导航及自适应控制面板纠正、聊天 timing 与字幕语言设置、real_chat 窄错误投影、相关测试和治理文档
明确禁止：除 app/core_host/real_chat.py 的错误公开投影外其余 app/** 业务语义、legacy Qt UI、插件/TTS/Tools/Memory/MCP/截图/主动互动/历史/Studio、依赖文件；data/**、characters/**、third_party/** 受保护
required profiles：docs、smoke、core-host、runtime-v2-shell、python-full
验收环境：仓库 runtime Python、Node、locked Cargo；Windows 真实 Tauri/WebView2 与开发配置；同一候选 SHA 三平台公共门
关联 ADR：ADR-0002、ADR-0003、ADR-0006、ADR-0007
计划提交：独立修订契约后，先冻结语言即时刷新、回复导航、菜单焦点及气泡/输入栏原子自适应失败测试，再按独立实现提交关闭并补验证证据
回退：先取消并排水活动聊天，禁用真实桥、chat.presentation_timing 与 chat.subtitle_language，按独立提交逆序回退；不删除或改写 history/配置
```

主要结果：把 WP-3-02 的真实聊天 Core 链接入已经由 WP-3-03/3U-01/3U-02 冻结的产品 UI，形成
第一条真实产品垂直链；本 WP 只替换聊天数据源，不重新设计桌宠 DOM、布局或视觉语言。

允许能力：

- 真实输入、发送、思考、完成、错误和取消。
- 真实回复段的 portrait/tone 投影到 WP-3U-02 已完成的立绘表现。
- 真实回复逐段清屏显示，等待时保持立绘，启动问候 reveal 后播放一次，按旧 Qt 时序恢复无闪烁交叉淡入。
- 等待回复时按旧 Qt 时序显示点号动效，输入框显示角色名思考状态；移除“立即显示”控件。
- Provider HTTP 错误显示状态码和经过 allowlist/脱敏的诊断字段，不泄漏原始响应体与凭据。
- 字幕语言切换立即刷新当前可见段；移除气泡关闭按钮，恢复当前会话回复上下回看并联动立绘。
- 鼠标右键打开菜单不自动聚焦第一项；键盘打开继续保留完整焦点导航。
- 回复轨道不改变相同字幕的气泡高度；气泡/输入栏自适应测量不提前改变可见子项，DOM 与原生矩形同次
  提交，输入扩缩时发送按钮保持底部锚定、完整可见且无几何过渡撕裂。
- 右键菜单恢复 `zh`/`ja` 字幕选择，默认中文并原子保存到 Runtime v2 `ui.json`。
- 最小受控 Gateway、聊天 identity/取消和 UI 状态映射。
- 只为真实聊天 UI 已直接消费的气泡、输入和打字机字段开放对应设置 feature，并完成读取、保存、
  生效、失败恢复和重新打开闭环；不得改变固定窗口包络。

明确禁止：

- 不加入 TTS、Tools、截图、主动互动、历史窗口和工作室。
- 不新增设置视觉体系或改变 WP-3U-01/02 已冻结的窗口与角色表现语义；未被真实聊天消费的交互控件
  继续禁用。
- 不为 UI 便利破坏 lifecycle 或基础 Envelope。

退出证据：

- 使用已有开发配置完成真实聊天和取消。
- UI 与 Core 的终态一致，晚到旧 generation 事件不改变当前界面。
- 真实主题、长文本、IME 和目标 DPI 验收通过。
- 除真实数据暴露的缺陷修复外，WP-3-03 的截图与几何基线保持不变。

独立回退：切回 Fake Core UI 演示路径，保留真实 headless chat、固定产品 UI、设置窗口宿主和角色表现能力。

### WP-3-05：Core 崩溃恢复与 UI 重新水合

激活记录（2026-08-02）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3-04 已由项目负责人明确验收并标记 accepted
base_ref：0ad1a1af3922d9263dac45fb0320d655e18c3a08
允许文件：冻结于 harness/tasks/WP-3-05.json；限现有 Supervisor/Shell lifecycle/Gateway generation barrier、同 WebView 恢复协调、隔离故障验收和治理文档
明确禁止：Python Assistant/Core/Provider/history、legacy Qt、插件/TTS/Tools/Memory/MCP、角色与真实数据、依赖文件、DOM/样式重设计；data/**、characters/**、third_party/** 受保护
required profiles：docs、smoke、core-host、runtime-v2-shell、python-full
验收环境：仓库 runtime Python、Node、locked Cargo；Windows 真实 Tauri/WebView2 与开发配置；同一候选 SHA 三平台公共门
故障矩阵：idle/active/settled Core 强杀、pipe 丢失、旧代 response/event/cancel/Snapshot/resource、Snapshot/角色延迟、恢复期草稿/IME、连续崩溃预算、retry/shutdown 竞态与完整资源回收
关联 ADR：ADR-0001、ADR-0002、ADR-0003
计划提交：激活后先冻结窗口存活、旧代失效、草稿/完成回复所有权、完整 Snapshot 水合和重复崩溃失败测试，再以独立实现提交关闭，最后补自动与实机证据
回退：停止新 send 并退出确认 Core 树归零，逆序禁用 UI 水合和新增 publication；保留既有 Supervisor/generation 屏障、WP-3-04 真实聊天、history 与配置
```

完成记录（2026-08-02）：

```text
状态：accepted
最终实现候选：f53a42d3885b3d98d9ace37ce164a49d45655635
自动验收：frontend 105 passed；locked Rust 232 passed / 24 ignored；cargo fmt、git diff check、docs/smoke/core-host/runtime-v2-shell/python-full required profiles 全部通过；Harness verify 23 passed / 0 failed / 3 manual pending
人工验收：项目负责人完成真实 Windows Tauri/WebView2 的 idle/active/settled Core 强杀、恢复期中文/日文 IME、连续崩溃预算耗尽、手动重试、下一轮聊天、完整退出和零相关进程残留，并确认同一候选证据与回退边界通过
数据与安全：data/**、characters/**、third_party/** 零变化；不恢复跨 generation 模型任务，不自动重发，不误杀无关 Python 或 Tauri Shell
验收记录：docs/records/audits/WP-3-05-AUTOMATED-VALIDATION.md；docs/records/audits/WP-3-05-OWNER-ACCEPTANCE.md
回退：停止新 send 并确认 Core 树归零，逆序禁用 UI 水合和新增 publication；保留 Supervisor/generation 屏障、WP-3-04 真实聊天、history 与配置
```

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
- 旧请求、聊天 handle、Snapshot 和事件全部失效。
- UI 明确区分已完成内容、已中断请求和仍保留的本地草稿。
- 重复崩溃受 restart budget 控制且无进程树残留。

独立回退：保留崩溃诊断但禁用自动 UI 水合，退回显式重新开始交互。

### WP-3-06：Legacy 数据参考 → Tauri v2 → 参考 oracle 兼容门禁

激活记录（2026-08-02）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3-05 已由项目负责人明确验收并标记 accepted
base_ref：93c75ba9803618d2d9fde6e99ebb152ffc176a6b
允许文件：冻结于 harness/tasks/WP-3-06.json；限全局数据写入门、现有 history/provider/UI 仓库、生产共享锁、真实 Qt/Tauri 隔离验收、相关测试和治理文档
明确禁止：破坏性 migration、Memory/Qdrant/SQLite、Tools/MCP/插件/TTS、角色/真实数据、依赖、默认配置 backfill 和通用 app-root 后门；data/**、characters/**、third_party/** 受保护
required profiles：docs、smoke、core-host、runtime-v2-shell、python-full
验收环境：仓库 runtime Python、Node、locked Cargo；系统临时目录内的 WP-0-02 脱敏副本；Windows 真实 Legacy 参考进程/Tauri/WebView2；同一候选 SHA 三平台公共锁/数据门
故障矩阵：reference→Tauri→reference、双向锁冲突、current/old/missing/future/corrupt schema、backup/temp/flush/replace/中断、v2 私有未来 schema、正常/强杀退出、进程与 manifest 清场
关联 ADR：ADR-0001、ADR-0002、ADR-0003
计划提交：先冻结数据清单与真实双入口失败测试，再补最小版本/结构写入门和验收接线，最后串行运行双入口、全量自动门与负责人实机验收
回退：停止 v2 共享数据写入并退回只读使用，逆序回退 WP-3-06 接线；不删除、恢复、重命名或修复任何用户文件，不把产品入口切回 Legacy Qt
```

契约修订记录（2026-08-02）：

```text
负责人批准：确认 1e157909 的产品方向修订，批准作为 WP-3-06 新契约内容
最新有效修订：kind=contract_revision；sequence=3；supersedes=0002
实现差异 base_ref：93c75ba9803618d2d9fde6e99ebb152ffc176a6b（按 Harness 契约保持初始实现锚点）
契约冻结：1e157909 的获批内容由最新 activation 提交冻结；不把方向提交误作实现差异起点
方向：Legacy Qt 仅作为迁移期实现参考、数据 parser/oracle 和隔离测试；不再是用户回退或可见 UI 验收对象
最终边界：全部能力迁入 Runtime v2 并通过 Phase 7 总门后，删除 Legacy Qt 桌宠入口、实现和发布引用
验收调整：可见人工验收只针对 Runtime v2；真实 Legacy 参考进程往返继续作为自动数据兼容证据
```

`0002` 曾把负责人批准提交误填为实现差异 `base_ref`，首次重新预检以
`CONTRACT_ACTIVATION_HISTORY` 拒绝；`0003` 仅纠正 Harness 锚点语义，不改变已批准产品方向或实现范围。

完成记录（2026-08-02）：

```text
状态：accepted
产品实现候选：ed16b7385；获批方向基线：1e157909；最终自动验证审计收口：ca36dfc1
自动验收：真实 Windows reference → Tauri → reference 进程门通过；只改变 fixture history 与 Runtime v2 ui.json；双向锁冲突、未来/损坏只读、保存失败、强杀重获、零相关进程残留通过；required profiles 全绿；Harness verify 23 passed / 0 failed / 3 manual pending
人工验收：项目负责人直接启动真实 Runtime v2 EXE，确认兼容历史、角色/配置行为和基础聊天，并明确批准三项人工门、同一候选证据、脱敏 manifest、独立回退与 Phase 7 Legacy Qt 删除边界
数据与安全：自动往返只使用系统临时目录脱敏 fixture；验收根已删除；data/**、characters/**、third_party/** 无产品变更；Legacy Qt 仅作迁移参考/oracle，不是可见 UI 或用户回退入口
验收记录：docs/records/audits/WP-3-06-AUTOMATED-VALIDATION.md；docs/records/audits/WP-3-06-OWNER-ACCEPTANCE.md
回退：停止 Runtime v2 共享数据写入并退回只读使用，保留所有用户数据；不得切回 Legacy Qt；其实现仅在全部能力迁移并通过 Phase 7 后删除
```

主要结果：证明 v2 dogfooding 不会破坏现有角色、配置、历史、Memory 和冻结的迁移前数据基线；不建立 Legacy Qt 产品回退能力。

允许能力：

- 使用 WP-0-02 夹具执行双向兼容测试。
- Tauri 读取现有数据并写入 Phase 1–3 明确允许的兼容数据。
- 备份失败、临时写入失败、异常中断和未来 schema 安全状态测试。

明确禁止：

- 不执行破坏性 schema 迁移。
- 不以保留 Qt 源码或静态解析测试代替真实参考进程往返；也不要求 Legacy Qt UI 可见。

退出证据：

```text
Legacy 参考进程创建/修改数据并退出
-> Tauri 获取同一应用锁并完成基础聊天
-> Tauri 退出且所有写入任务结束
-> Legacy 参考进程重新获取应用锁并由冻结 oracle 读取兼容数据
```

- 两个入口同时启动时只有一个成功持锁。
- v2 专属配置不改变 Qt 行为。
- 不支持的未来 schema 进入 diagnostics/只读安全状态。
- 本 WP 产生 ADR-0003 的真实迁移 oracle 往返证据；须经 WP-3V-01 组合纵向复验后才能关闭该技术门。
- 人工可见 UI 只验收 Runtime v2；Legacy Qt 不再是用户验收对象，并在 Phase 7 删除。

独立回退：停止 v2 共享数据写入并退回只读使用，保留用户数据；不得把用户切回 Legacy Qt。

## 13. Phase 3V：Assistant 架构验证硬门禁

### WP-3V-01：Runtime v2 Assistant Architecture Validation Slice

激活前冻结契约（2026-08-02）：

```text
状态：planned；只有 WP-3-06 accepted 后才能由独立 activation 提交切换为唯一 active Work Package
前置条件：WP-3-01 至 WP-3-06、WP-3U-01/02、WP-3S-01 全部 accepted
真实消费者：现有 Runtime v2 桌宠 UI、Rust Gateway/Supervisor、bundled Python Core、无 Qt Assistant Adapter、Chat Pipeline、history 仓库和共享应用锁
允许文件：.github/workflows/runtime-v2-platform-foundation.yml；desktop/src-tauri/src/main.rs 中仅限 debug/test acceptance 接线；新增 desktop/src-tauri/src/wp_3v_01_assistant_architecture_acceptance.rs；desktop/frontend/tests/**；新增 desktop/tests/windows_wp_3v_01_assistant_architecture_acceptance.ps1；新增 tests/fixtures/runtime_v2/wp_3v_01/** 和 tests/integration/test_wp_3v_01_assistant_architecture.py；harness/suites.json、harness/README.md、harness/tasks/WP-3V-01.json、harness/activations/WP-3V-01/0001.json；ADR-0002/0003/0004、产品能力台账、Runtime v2 spec 索引、技术 README、本计划和 audits 记录/索引
明确禁止：修改 app/**、现有 Rust Supervisor/Gateway/transport/data production 模块、frontend 产品代码、legacy_qt_main.py、main.py、依赖/lockfile/tauri.conf、plugins/**、runtime/**、tools/mcp/**；data/**、characters/**、third_party/** 受保护
协议冻结输入：现有 hello/initialize/readiness、chat.send/chat.cancel、chat started/completed/failed/cancelled、health/shutdown、generation、credential、最小 Snapshot/角色/历史字段；不得新增通用 Operation、resource token、任务优先级或未来 component
数据政策：自动场景只写系统临时目录中的脱敏 fixture；真实 Provider 场景只允许现有兼容 history append，验收前后生成 path/length/mtime/SHA-256 manifest；credential、API Key、完整 Prompt 和私密配置不得进入输出或证据
验收环境：同一候选 SHA 的 Windows x64、macOS arm64、Linux x64 locked 公共门；Windows 真实 Runtime v2 EXE/WebView2 与已有开发 Provider 配置；bundled Python，不允许 Fake Core 或直接 Python 调用替代跨边界链路
故障矩阵：真实完整回复、取消唯一终态、聊天/取消期间 health/shutdown、Core 强杀与新 generation 水合、旧代 response/event/Snapshot/cancel 丢弃、Provider 失败、协议/credential 错误、锁冲突/重获、正常/强杀退出、完整进程/pipe/thread/handle/fd/临时根清场、manifest 和 secret scan
required profiles：docs、smoke、core-host、runtime-v2-shell、python-full
人工验收：负责人直接启动当前 Runtime v2 EXE，以已有开发配置完成一次真实 Provider 回复和取消，复核强杀恢复、历史追加、退出零残留、脱敏证据及同一候选三平台结果
回退：删除/回退本 WP 的 acceptance 接线、脚本和证据，不回退任何已 accepted 生产 WP，不删除或改写用户数据；发现生产缺陷时 WP-3V-01 退回 planned，只重新打开唯一责任 WP
```

激活记录（2026-08-02）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3-06 已由项目负责人明确验收并标记 accepted
base_ref：5c3cfc59fda1f238c78d1b9b333e4968c46d747c
任务契约：harness/tasks/WP-3V-01.json；activation：harness/activations/WP-3V-01/0001.json
范围：只新增或修改冻结的组合验收设施、同一候选三平台接线、证据和能力台账；不允许修改前置生产实现
停止条件：发现生产缺陷时立即退回 planned，只重新打开唯一责任 WP；不得在本 WP 内顺手修复或放宽门禁
```

停止记录（2026-08-03）：修正验收器自身的窗口监听、启动时序和公开事件字段后，真实 Windows
组合验收稳定暴露 WP-2-01 Rust Router 的跨队列事件重排缺陷。项目负责人明确批准执行冻结停止
条款：本 WP 退回 `planned`，只将 WP-2-01 重新打开为 `stabilizing`；不得在本验证 WP 内修改生产
Router，也不得通过延迟 Provider 或放宽 Gateway 顺序校验制造通过。验收器诊断提交为
`d47cd414`，缺陷证据见 `docs/records/audits/WP-3V-01-ROUTER-ORDERING-DEFECT.md`。WP-2-01 修复
重新 accepted 后，才可在新的 activation 基线重新激活本 WP。

恢复记录（2026-08-03）：项目负责人验收通过 WP-2-01 Router 顺序稳定化并批准恢复本 WP。恢复只
重建任务契约 activation 基线，不改变原冻结范围、自动/人工验收项、Provider 时序或数据边界；恢复后
须在当前候选重新运行 preflight、required profiles、真实 Windows 组合验收及最终 verify。

恢复后稳定化记录（2026-08-03）：实现候选至 `43b9b731`；WP-2-01 依赖已 accepted，第二次
activation/preflight 通过。真实 Windows 组合验收再次通过：4 次 Provider 请求、1 次 Core 强杀、取消
唯一终态、新 generation 水合、Legacy oracle 回读、仅预声明 fixture history 变化、敏感证据/进程残留
均为 0。Harness verify 报告 `temp/harness/20260802T165927Z-WP-3V-01.json` 为 required profiles
5/5、自动项 24 passed/0 failed、人工项 3 pending；完整 Rust 单线程 239 passed/24 ignored，release
build 和 cargo fmt 通过。当前分支比远端 ahead 24，`43b9b731` 尚无实际同 SHA 三平台 workflow；
Harness 的本地 automated 映射不替代该远端证据。负责人仍须完成真实开发 Provider 回复/取消、恢复/
锁/零残留复核以及同 SHA 三平台证据审查；完成前保持 stabilizing，不更新 CAP-004，不标记 accepted。
本地证据详见 `docs/records/audits/WP-3V-01-AUTOMATED-VALIDATION.md`。

主要结果：用真实 Sakura Assistant 领域代码证明 Runtime v2 可以承载第一条可靠产品垂直链，并把 CAP-004 推进到 `architecture-validated`。这是验证 WP，不是新业务实现 WP；Fake Core 不能作为通过证据。

强制前置：WP-3-01 至 WP-3-06、WP-3U-01/02 以及 WP-3S-01 全部 accepted；WP-2-01/02 的最小 Router、
聊天边界和 Snapshot 已由这些真实消费者使用。

必须执行的单一纵向场景：

```text
Tauri/Rust 启动 bundled Python Core
-> Core 加载无 Qt Assistant Adapter
-> 读取当前角色和已有开发配置
-> 构造基础 Provider
-> 发送一条真实聊天请求
-> 获得真实完整回复
-> 发送并完成一次取消请求
-> 使用现有兼容格式追加聊天历史
-> 强制终止 Core
-> 启动新 generation
-> 恢复角色、历史和最小 UI 状态
-> 正常退出
-> 完整进程树和 IPC 资源残留为 0
-> 除明确允许的兼容历史追加外，非预期用户数据变化为 0
-> legacy Qt 重新获取同一应用锁并读取允许写入的数据
```

允许范围：

- Rust acceptance harness、最小调试 WebView 或当前桌宠 UI；不要求完整视觉表现。
- 自动化在三平台使用真实 Adapter/Chat Pipeline 和确定性 local Provider；另以已有开发配置执行至少一次真实 Provider 完整回复，不把静态 fixture 冒充真实聊天。
- lifecycle/IPC/generation/数据 manifest、历史兼容 oracle、进程/pipe/thread/handle/fd 残留和锁重获证据。
- 验证记录、可重复脚本和能力台账更新。

明确禁止：

- 不在本 WP 建设通用 Agent、Capability Broker、任务图、通用 Operation、完整资源平台、自动 Runtime Repair 或新业务能力。
- 不用 Fake Core、直接 Python 单元调用或静态 schema 测试替代上述跨边界场景。
- 不把 Rust/WebView 变成角色、会话、历史或聊天状态的第二真相源。
- 不为使验收通过而执行破坏性 schema 迁移、放宽 credential/generation 校验或绕过进程树清理。

退出证据：

- Windows x64、macOS arm64、Linux x64 使用同一公共 transport/generation 语义完成自动纵向场景；对应真实 WebView 平台证据按 WP-3-04/05 保留，Linux 区分 X11/Wayland 责任。
- 真实 Provider 完整回复、取消唯一终态、Core 强杀、新 generation 水合、正常退出和 legacy Qt 回读全部通过。
- health/shutdown 在聊天与取消期间可响应；旧 generation request/response/event/Snapshot 不影响新 UI。
- 完整进程树、pending waiter、reader/writer、pipe/thread/handle/fd 和临时资源残留为零；共享锁可立即重获。
- 除测试预先声明的兼容历史追加外，`data/`/角色/配置 manifest 没有非预期变化；credential、API Key、完整 Prompt 和私密配置不进入日志、Snapshot 或证据工件。
- CAP-004 记录完整证据并标记 `architecture-validated`；这不等于 `parity-accepted`，也不替代 Phase 7。

稳定化与回退：本 WP 只接受验证设施和证据修正。发现生产缺陷时，停止 WP-3V-01 并把它退回 `planned`，只将拥有该缺陷的一个前置 WP 重新置为 `stabilizing`；修复并重新 accepted 后再激活本 WP。独立回退验证 harness/调试 UI/台账证据即可，不回退已独立 accepted 的生产 WP，不删除或改写用户数据。

负责人验收记录（2026-08-03）：项目负责人确认人工验收通过并批准进入 WP-4-01；接受候选
`dabcd7733548c0aa2953f02578e5e3f79a6200fc` 的同一 SHA 三平台 Runtime v2 foundation 与 Test workflow
均成功。CAP-004 据此推进为 `architecture-validated`，不等于 `parity-accepted`。声明、人工边界与证据见
`docs/records/audits/WP-3V-01-OWNER-ACCEPTANCE.md`。

## 14. Phase 4–7 强制 Work Package

以下编号和发布能力映射保留为暂定执行序列。WP-3V-01 通过后，应按下一个真实产品消费者重新确认依赖；不得仅因 ADR 已记录方向就提前完整实现通用抽象。每个 WP 进入 `active` 前必须补充逐文件允许目录、平台环境、真实消费者、协议字段、故障矩阵、人工步骤和独立回退；不得把相邻行合并成一次“大迁移”。

### Phase 4：Assistant 辅助能力等价

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-4-01 | CAP-008 | Memory 检索、写入、整理、外部存储与降级；同步开放 Memory 设置和记忆管理 feature | 聊天不因 Memory 失败不可用；Qdrant/SQLite/模型资源可回收；设置失败与三平台数据兼容通过 |
| WP-4-02 | CAP-009/010 | 内置 Tools、Action ID 确认和 Tools 设置；在聊天与 Tools 两个真实消费者证明确有共性后提取 Operation | WebView 不能伪造执行参数；设置/取消/超时唯一终态；副作用有确认证据 |
| WP-4L-01 | CAP-025 | Rust 单写者统一 Runtime v2 文件日志，接入 Core stderr 与受控 WebView 诊断 | 全层脱敏、背压不阻塞、轮转/退出有界、崩溃恢复与 operation 关联通过 |
| WP-4-03 | CAP-011 | MCP 配置、设置页运行状态、启动、工具调用与恢复 | MCP 进程属于当前 generation；配置/崩溃/超时/退出零残留；凭据不泄漏 |
| WP-4-04 | CAP-012 | 现有插件 context/event/tool、插件启停/设置/action 和私有数据等价 | 插件加载/卸载、设置失败、错误隔离、反向清理和数据兼容通过 |
| WP-4-05 | CAP-013/014 | TTS、播放、语音设置、设备错误与 audio ADR | 三平台真实音频设备；设置/服务/模型子进程回收；播放失败不拖垮聊天 |
| WP-4-06 | CAP-015 | 手动截图、相关设置及其首个 generation resource token 消费者与平台权限 | 设置与实际捕获一致；多屏/DPI、macOS 权限、X11/Wayland portal、token 失效通过 |
| WP-4-07 | CAP-016/017 | 自动观察、主动互动、提醒、任务调度及其隐私/交互设置 | 设置保存/生效、时区/休眠恢复、重复事件、取消、数据持久化和截图权限通过 |
| WP-4-08 | CAP-008–017 | Phase 4 组合稳定化；只冻结已经有多个真实消费者证明的调度/背压共性 | 长任务不阻塞 control；Memory/MCP/plugin/TTS/screenshot 全资源零残留 |

#### WP-4-01：Memory 能力等价

激活冻结契约（2026-08-03）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-3V-01 已由项目负责人明确验收并标记 accepted；CAP-004 已为 architecture-validated
base_ref：5fc9e56ccc091b8e099935fe5035d76b14be5e03
真实消费者：Runtime v2 真实聊天的 Memory recall/完成轮整理，以及同一 Tauri App 的 Memory 设置和管理页面
所有者：当前 bundled Python Core generation 内唯一无 Qt Memory owner；Rust 只管协商、窗口授权、identity、deadline 和公开 DTO
协议：可选能力 assistant.memory；memory.search/upsert/delete、memory.settings.get/save、memory.model.import/download/cancel；模型任务只发 memory.model.* 唯一终态事件
设置切片：开放 memory.manage、memory.curation、memory.embedding_model、model.memory_curation_slot；整理轮次和 CRUD 位于记忆页，整理槽与 embedding 模型统一位于模型页；其他领域 feature 保持 unavailable
允许目录：逐文件范围以 harness/tasks/WP-4-01.json 为准；新增 Core Memory boundary、Rust Memory Gateway、现有 settings Memory 页面接线、隔离 fixtures/tests/Windows 验收和三平台 workflow
明确禁止：AppContext/bootstrap、Qt worker/tauri_settings/PetWindow、Legacy Qt 产品入口、通用 Operation/Tools/Action ID、MCP/plugin/TTS/screenshot、依赖与 lockfile；data/**、characters/**、third_party/** 受保护
数据：Python 独占既有 Qdrant/SQLite/core_profiles/curation_state；memory.json 保持字节不变；测试只写隔离根，真实写入只来自明确用户动作或完成回复整理
验收环境：bundled Python；Windows 真实 Runtime v2 EXE/WebView2 隔离验收；同一候选 SHA Windows x64、macOS arm64、Linux x64 locked workflow；Legacy 仅 headless 数据 oracle
故障矩阵：Memory loading/failed、embedding 缺失、Qdrant/SQLite/锁/权限/磁盘/原子写故障、损坏/未来数据、CRUD/整理并发、模型 ZIP/下载失败、取消/关窗/Core 强杀/旧 generation、IME 草稿和完整资源清场
required profiles：docs、smoke、core-host、runtime-v2-shell、python-full
人工验收：直接启动当前 EXE，在隔离根完成 IME CRUD、检索影响聊天、一次整理、设置/模型失败恢复、Core 强杀恢复、退出零残留，并审查同 SHA 三平台证据
回退：先禁用四个 Memory feature，停止新任务并退出 Core；只回退 WP-4-01 接线，恢复 DisabledMemory 降级；不删除、恢复、迁移或修复任何用户 Memory/配置/history 数据
设置生命周期：模型槽保存触发 Core restart 后，原设置窗口必须自动重绑定新 generation，保留列表、筛选、选中项、草稿与 IME composition；旧代超时、Router 关闭和 identity mismatch 不得覆盖稳定 UI，也不得要求关闭重开设置
布局基线：沿用既有设置页面的信息层级；记忆统计/筛选紧凑，列表与编辑器稳定双栏，窄窗口不得出现逐字竖排、遮挡或主要编辑区被模型控件挤压
验收缺陷：首次人工验收发现模型控件页面归属、窄窗口排版和 Core restart 后原位重绑定不符合契约；WP 保持 active，修复与复验见 docs/records/audits/WP-4-01-SETTINGS-LAYOUT-AND-GENERATION-DEFECT.md
最终修正：Memory/Core 只直接依赖无 PySide6 的纯资源模块，Qt ResourceManager 仅作 adapter/兼容导出；product/test build 的默认 hello 都声明 Router、Provider Settings、Memory，历史 WP 通过显式 payload 请求 predecessor 拓扑
当前产品拓扑门：非历史回归必须由真实 Core 同时完成 Chat、Provider Settings 与 Memory；后续能力只追加到该门，冻结 staged Runtime 的 predecessor profile 不得冒充当前产品拓扑
Settings 边界：各领域 controller 独占 snapshot、draft、dirty、rebind；Shell 只聚合 dirty 与保存顺序，共享 request 仅为兼容视图且不得被任一 controller 整体替换；全面清理由 WP-5-01 承担
任务契约：harness/tasks/WP-4-01.json；activation：harness/activations/WP-4-01/0006.json（supersedes 0005；最终契约修订）
```

精确行为、DTO、字段上限、数据兼容、资源所有权、人工步骤与非目标见
`docs/specs/runtime-v2/WP-4-01-memory-capability.md`。实现中若发现必须升级依赖、引入新进程、改变
Memory 数据格式或抽取通用 Operation，须停止本 WP 并独立重新审查契约，不得在 active 范围内顺手扩大。

负责人验收记录（2026-08-08）：项目负责人确认人工验收通过，批准把 WP-4-01 标记为 `accepted` 并
激活 WP-H-02。最终候选 `bfa5edc6fdd1b921fce6d366096fa95192f9d878` 的 Test、Windows x64、macOS
arm64、Linux x64 均成功；人工边界与证据见
`docs/records/audits/WP-4-01-OWNER-ACCEPTANCE.md`。该结论不预先通过 Tools 或后续能力。

#### WP-H-02：Harness 删除型减负

激活边界（2026-08-08）：

```text
状态：active（当前唯一 active/stabilizing Work Package）
前置条件：WP-4-01 已由项目负责人明确验收并标记 accepted
base_ref：cd9faf8f828d97b076cca404abfe48119b876ee8
范围：只修改 Harness、对应单元测试、CI 调用、AGENTS 和开发/治理文档；不修改产品代码
任务契约：先以旧 v1 loader 激活，再在实现提交中迁移为五字段 v2
activation：harness/activations/WP-H-02/0001.json；这是仓库最后一个 activation，后续禁止新增
保留边界：changed-set、全局 protected data/characters/third_party、依赖变化、tests 删除、JSON report
删除概念：activation 历史验证、治理文档冻结、per-WP forbidden/protected/dependency policy、验收散文映射、独立 preflight
required profiles（最终 v2）：docs、unit；另按实施计划显式运行 core-host 与 runtime-v2-shell
计划提交：激活与契约、实现与测试、自动验证记录；负责人 acceptance 提交不计入
人工验收：负责人审查删除概念、最终 v2 task/report、保留安全边界与净删除统计
回退：整体 revert refactor 与调用文档，使用 HEAD 中保留的 v1 task/activation 恢复旧 loader；不触碰产品或数据
```

规范见 `docs/specs/runtime-v2/WP-H-02-lean-agent-development-harness.md`，架构决策见
`docs/adr/0009-lean-agent-development-harness.md`，实施与验证顺序见
`docs/archive/plans/agent-development-harness-v2-reduction.md`。自动门通过后只进入 `stabilizing`；负责人单独验收并
标记 `accepted` 后，才可激活 WP-4-02。

自动候选记录（2026-08-08）：实现候选 `eb36dc2262a5159c59a1af120cbe9cde74f2c237` 已完成本地
required profiles、Core/Shell 回归和 GitHub Test；Harness Python 与对应测试净删除 372 行，最终
`verify` 为 3 个唯一 case 全绿、13.769 秒、exit 3 / `manual_pending`。WP-H-02 据此进入
`stabilizing`，等待负责人审查；完整事实见 `docs/records/audits/WP-H-02-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-08）：项目负责人明确声明“可以accepted了”，接受最终 HEAD
`458437b8b212aba813826617d2f44a4d27cb8e84` 的删除结果、保留安全边界与 final-HEAD Test。WP-H-02
据此标记 `accepted`；原始声明和验收边界见 `docs/records/audits/WP-H-02-OWNER-ACCEPTANCE.md`。

#### WP-H-02A：Harness 短超时输出测试确定化纠正

激活边界（2026-08-09）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
前置条件：WP-H-02 accepted
base_ref：817dc9b1909b5f145c95f3e8a37b7d8bcb776af5
根因：Windows 上新 Python 解释器首行输出晚于 20 ms，自测错误地把未产生输出归类为 Runner 丢失输出
范围：只修改 Harness runner、自测、开发/治理文档和本 WP task/record；不修改产品代码或 suite timeout
保持边界：20 ms deadline、timeout 失败、立即终止、pipe 排空、UTF-8 解码、JSON report 与 fail-fast 不变
任务契约：harness/tasks/WP-H-02A.json；不创建 activation
required profiles：docs、smoke、unit；另连续运行 smoke 至少 10 次
人工验收：负责人确认测试确定化没有增加 timeout、隐藏宽限期或放宽失败语义
回退：整体 revert 本包，恢复原自测；不修改 Runtime、用户数据或 WP-4-01A 产品候选
```

WP-4-01A 的 SOCKS Memory 修复已冻结在 `817dc9b1909b5f145c95f3e8a37b7d8bcb776af5`，但最终 verify
暴露本 Harness 自测缺陷。WP-4-01A 暂回 `planned`，待本纠正包 accepted 后再恢复并重跑自己的自动门；
不得把 Harness 修复混入 Memory task allowlist。

自动候选记录（2026-08-09）：实现候选 `38b6043277a4cba6bce4e2021784d061b02bf3d5` 的 Harness runner
12 passed，完整 smoke 连续 10 轮均 3/3 passed，unit 618 passed/6 skipped；最终 `verify` 的 6 个唯一
case 全绿并返回 exit 3 / `manual_pending`。WP-H-02A 据此进入 `stabilizing`，等待负责人确认未放宽
20 ms deadline、timeout 失败或进程终止语义；事实边界见
`docs/records/audits/WP-H-02A-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-09）：项目负责人明确声明“验收通过，进入下一步”，接受上述确定化候选，
确认没有增加 timeout、隐藏宽限期或放宽失败与进程终止语义。WP-H-02A 据此标记 `accepted`，并恢复
WP-4-01A 为唯一 `active` Work Package；原始声明与自动证据合并记录在
`docs/records/audits/WP-H-02A-AUTOMATED-VALIDATION.md`。

负责人 base 修订（2026-08-09）：恢复预检发现 WP-4-01A 的原固定 base 会把已验收的 WP-H-02A 文件
计入 Memory changed-set；负责人明确要求“直接移动固定base”。据此允许本 task 把 base 前移到
WP-H-02A 验收与状态切换提交 `3c984f187ee6e5b8f1549bf96fdf21055f2e66fd`，并以已提交 task 修订
记录。该批准不允许后退、跨历史移动或把 WP-H-02A 专属文件加入 Memory allowlist。

#### WP-3-03A：跨平台桌宠动态表面与精确命中纠正

负责人优先级纠正（2026-08-08）：暂停尚未进入产品实现的 WP-4-02，先修复 Runtime v2 固定透明
大窗口和非 Windows 整窗拦截鼠标的问题。规范见
`docs/specs/runtime-v2/WP-3-03A-cross-platform-pet-surface.md`，架构决策见
`docs/adr/0010-cross-platform-pet-surface.md`，实施顺序见
`docs/plans/runtime-v2/WP-3-03A-cross-platform-pet-surface.md`。

```text
状态：active（当前唯一 active/stabilizing Work Package）
base_ref：3bfec98f2cc55d5676fd92e465d035735fecb73a（负责人批准的插入依赖后单向续基）
主要结果：动态原生内容包络、立绘底部中心稳定锚点、逐像素 alpha 命中和三平台 backend
Linux：X11/XWayland 为完整语义路径；native Wayland 保留精确 input region 并明确全局锚点降级
保护边界：不修改 data/**、characters/**、third_party/**；Windows SetWindowRgn 不得退化
required profiles：docs、runtime-v2-shell、runtime-v2-window-surface；Windows 另跑 runtime-v2-windows-interaction
任务契约：harness/tasks/WP-3-03A.json；不创建 activation
回退：整体回退 surface schema/backend/前端事务，恢复已验收固定窗口实现，不修改用户数据或角色资源
```

自动门通过后进入 `stabilizing`；只有项目负责人完成 Windows、macOS、Linux X11/XWayland 实机验收
后才能标记 accepted。native Wayland 的受限结果必须单独登记，不能冒充完整平台验收。

自动候选记录（2026-08-08）：实现候选 `cda495b43782e6cad3aa83043d99f2e871100ceb` 与验证记录
HEAD `e6e8d6b669215bca73cc9532c036ad6fb6572b5b` 的 required profiles 全绿，`verify` 返回
`manual_pending`，因此 WP-3-03A 进入 `stabilizing`。三平台系统级点击路由和拖动矩阵仍等待负责人
验收，事实边界见 `docs/records/audits/WP-3-03A-AUTOMATED-VALIDATION.md`。

暂停记录（2026-08-09）：负责人实机确认已 accepted 的 Memory 切片仍由打开“记忆”页首次触发本地
模型预载，页面在“正在初始化/正在加载”之间反复切换并出现 `REQUEST_DEADLINE_EXCEEDED`；关闭设置后
立即重开还可能落入已销毁窗口的竞态。该问题归属于 WP-4-01 的启动与设置关窗/重开退出条件，不得在
本 WP 扩大 Python Memory 范围。因此 WP-3-03A 暂回 `planned`，既有候选和验证证据保留；只有
WP-4-01A accepted 后才恢复其剩余三平台实机验收。

恢复记录（2026-08-09）：项目负责人完成 WP-4-01A 的五项 Windows 实机验收并明确确认通过，
WP-3-03A 据此恢复为唯一 `stabilizing` Work Package。既有实现候选和自动证据继续有效，当前只恢复
Windows、macOS、Linux X11/XWayland 的剩余实机验收，不开始新的产品实现。

负责人 base 修订（2026-08-09）：恢复后的固定 base 仍早于已验收的 WP-H-02A、WP-4-01A 与 macOS
纠正提交，导致 Harness 把这些插入依赖计入 WP-3-03A changed-set。项目负责人明确授权“直接修改
base”，据此将 task 的 base 单向前移到本轮告警收口开始前的干净 HEAD
`3bfec98f2cc55d5676fd92e465d035735fecb73a`。本次续基不扩大 allowlist，不允许后退或跨历史移动；
续基后的 changed-set 只保留编译告警收口与对应治理证据。

最终自动门（2026-08-09）：续基与零告警提交 `e8de48e8ec4ae058216a6d289134256b51494cf3`
通过 `harness check WP-3-03A`；最终 `verify` 的 docs、runtime-v2-shell 与
runtime-v2-window-surface 共 8/8 唯一 case 全绿，报告状态为 `manual_pending`、进程退出码为 3。
完整事实见 `docs/records/audits/WP-3-03A-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-09）：项目负责人明确声明“我验收通过了”，随后授权直接修改旧 base 并要求
做好下一步开发准备。该结论接受既有三平台实机验收与本次零告警收口后的自动证据；未提供的设备、CI
run ID 或逐项操作细节不补写。WP-3-03A 据此标记为 `accepted`，允许 WP-4-02 进入 scope-only
范围冻结阶段。

#### WP-4-01A：Memory 启动预热与设置窗口恢复纠正

负责人缺陷纠正（2026-08-09）：恢复 Legacy 与 `MemoryStore.preload` 已声明的产品语义——已安装的本地
embedding 模型必须在当前 Core generation 创建 Memory owner 时立即后台预热，打开设置页只能观察状态，
不得成为首个初始化触发器。同时关闭设置必须终止页面重试；销毁与立即重开的竞态必须创建新的单调窗口
generation，不能静默吞掉打开动作或暴露 transport deadline 原文。

负责人依赖纠正（2026-08-09）：长期记忆固定模型改由 FastEmbed 0.8.0 + ONNX Runtime 1.28.0 加载
`all-MiniLM-L6-v2` 的固定 ONNX revision，不再为 Memory 携带 SentenceTransformer/PyTorch。公开模型名、
384 维和现有 Qdrant 数据契约保持不变；快速接话暂不接入 Runtime v2，本包不迁移其 BGE 链。

```text
状态：accepted
base_ref：3c984f187ee6e5b8f1549bf96fdf21055f2e66fd（负责人批准的暂停恢复前移）
主要结果：Core 启动即非阻塞预热 Memory；页面首读只观察/有界重试；设置窗口关闭后可立即重开；
Memory 推理迁移到固定 FastEmbed/ONNX，干净 Runtime 不再包含 SentenceTransformer/PyTorch
保护边界：不修改 data/**、characters/**、third_party/**；不隐式联网、不重建或修复 Memory 数据
required profiles：docs、smoke、core-host、runtime-v2-shell；另手工运行 python-full 扩大回归
任务契约：harness/tasks/WP-4-01A.json；不创建 activation
回退：整体回退启动预热、页面首读、窗口重开协调和 FastEmbed provider；保留用户 Memory、旧/新模型
缓存、配置和既有 WP-4-01 数据，不重建 Qdrant
```

规范沿用并收紧 `docs/specs/runtime-v2/WP-4-01-memory-capability.md`；实施与故障矩阵见
`docs/plans/runtime-v2/WP-4-01A-memory-startup-settings-recovery.md`。自动门通过后只进入
`stabilizing`；必须由负责人在真实 Windows 候选上确认启动预热、记忆页稳定就绪、加载中关窗、立即重开
和正常退出零残留后，才能标记 accepted 并恢复 WP-3-03A。

最终自动候选记录（2026-08-09）：负责人批准的 base 前移提交 `4eccf32334b008100ad7951aa57ecc4d72cb58dd`
通过 task check；最终 verify 的 docs、smoke、core-host、runtime-v2-shell 共 15/15 唯一 case 全绿，报告
状态为 `manual_pending`。独立 `python-full` 也为 3/3 case 通过。WP-4-01A 据此进入 `stabilizing`，等待
负责人完成上述 Windows Memory 生命周期人工验收；完整事实见
`docs/records/audits/WP-4-01A-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-09）：项目负责人明确声明“WP-4-01A 人工验收 5/5 通过，退出无残留”，
确认真实 Windows 候选的启动预热、记忆页稳定就绪、加载中关窗、立即重开和正常退出零残留全部通过。
WP-4-01A 据此标记为 `accepted`，并恢复 WP-3-03A 的剩余三平台实机验收；原始声明与自动证据合并记录在
`docs/records/audits/WP-4-01A-AUTOMATED-VALIDATION.md`。

#### WP-4-02：Tools、Operation 与 Action ID 确认

Scope-only 激活（2026-08-09）：

```text
状态：active（当前唯一 active/stabilizing Work Package；只允许范围冻结文档）
前置条件：WP-H-02、WP-4-01A 与 WP-3-03A 均 accepted
base_ref：e8de48e8ec4ae058216a6d289134256b51494cf3（负责人批准的前置验收后单向续基）
当前允许范围：仅 ADR、Spec、Plan、audit 和 harness/tasks/WP-4-02.json
required profiles：docs
产品实现门：normative Spec、真实消费者、协议/Action ID、故障矩阵、Journey 和逐路径 allowlist 未冻结前，禁止修改 app、desktop、tests 或 Harness suite
后续契约：scope 审查完成后，以一次已提交 task v2 修订加入产品路径和实际 profiles；本次续基后 base_ref 保持不动
Journey：实现范围冻结时新增 journey-tools，且不得与 broad Python profile 重复收集
任务契约：harness/tasks/WP-4-02.json；不创建 activation
```

本次激活只建立安全的规范准备窗口，不表示 CAP-009/010 已开始实现或任何 Tools 行为已获验收。下一步须
先完成 WP-4-02 的文档预检和 normative Spec；若架构决策改变既有 Operation/Action ID 方向，再独立新增
ADR。产品代码范围必须在已提交 task 修订和 `harness check WP-4-02` 通过后才开放。

范围冻结输入（2026-08-09）：normative 行为、真实消费者、Action ID、原生确认、Tools 设置、故障矩阵、
Journey 和回退已分别冻结在
`docs/specs/runtime-v2/WP-4-02-tools-operation-action-confirmation.md` 与
`docs/plans/runtime-v2/WP-4-02-tools-operation-action-confirmation.md`。本 WP 只开放 `get_current_time` 和
WP-4-01 已验收 Memory boundary 的四个工具；Todo/提醒、截图、MCP、插件、TTS、浏览器/桌面控制继续由
后续责任 WP 承担。工具调用保持为当前聊天 Operation 的子步骤，Action ID 是一次性确认租约；该收敛
遵循 ADR-0002 的既有方向，不新增或改写 ADR。下一提交必须先修订 task v2 allowlist/profiles 并通过
`harness check WP-4-02`，之后才允许产品实现。

自动候选记录（2026-08-10）：实现候选 `0ea4e0baac9eb0c2fbd661485063fdd9a0e1f48b` 的
`harness verify WP-4-02` 为 18/18 自动 case 通过、0 failed、0 blocked，机器状态为
`manual_pending`。Python Journey 22 passed、Rust Journey 7 passed、frontend Journey 4 passed；完整 Rust
回归为 280 passed/24 ignored/0 failed。WP-4-02 据此进入 `stabilizing`，等待真实 Windows 原生确认、
Tools 设置重启重绑定和退出零残留人工验收；在负责人明确验收前不得标记 `accepted` 或激活 WP-4-03。
完整自动证据见 `docs/records/audits/WP-4-02-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-10）：项目负责人在当前开发会话中明确声明“我验收通过了,你标记然后开始”。
负责人接受当前最终候选 `6843dd40e9513d8015acde8db39fe93eedb2a134`，WP-4-02 据此标记为
`accepted`。本记录不补写负责人未提供的设备、步骤或 CI 事实；原始声明和既有自动证据见
`docs/records/audits/WP-4-02-OWNER-ACCEPTANCE.md`。

#### WP-4L-01：Runtime v2 迁移可观测性基础

治理与实现激活（2026-08-10）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
前置条件：WP-4-02 已由项目负责人明确验收并标记 accepted
base_ref：6843dd40e9513d8015acde8db39fe93eedb2a134
范围：Rust 单写者 JSONL 服务、Python Core stderr bridge、受控 WebView diagnostics、现有 Memory/interaction latency 诊断合并、文档与测试
required profiles：docs、runtime-v2-shell、python-full、journey-observability（`python-full` 按 Harness 去重规则替代与其重叠的 smoke/core-host）
任务契约：harness/tasks/WP-4L-01.json；不创建 activation
非目标：日志查看器、日志设置/读取/导出 API、Repair、遥测、Legacy debug.file_enabled 接入
```

规范、架构选择和分阶段回退分别见
`docs/specs/runtime-v2/WP-4L-01-runtime-observability.md`、
`docs/adr/0012-runtime-v2-single-writer-observability.md` 与
`docs/plans/runtime-v2/WP-4L-01-runtime-observability.md`。WP-4-03 改为依赖 WP-4L-01；在本 WP 自动门
全绿并由负责人验收前不得开始 MCP 生产实现。

实现预检补充（2026-08-10）：除 Shell 侧已知的 Memory 专用诊断外，`app/agent/memory.py` 在旧
`memory-initialization.jsonl` 已存在时仍会续写。为履行“旧文件保留但停止续写”和 Rust 单写者契约，
task v2 allowlist 仅补入该生产文件；固定 base 与 required profiles 不变。此修订不授权改写或删除旧日志。

自动候选记录（2026-08-10）：实现候选 `3676d5c723b19ee2158087ad5ed383f6a5a9b07a` 的
`harness verify WP-4L-01` 为 14/14 唯一自动 case 通过、0 failed、0 blocked，机器状态为
`manual_pending`。完整 Rust 回归为 290 passed/24 ignored/0 failed；Python unit、integration 与 Legacy
Qt UI 分别为 645 passed/6 skipped、53 passed/2 skipped 和 24 passed。WP-4L-01 据此进入
`stabilizing`，等待真实 Windows 隔离验收和同 SHA 三平台 Runtime v2 CI；在负责人明确验收前不得标记
`accepted` 或开始 WP-4-03。完整自动证据见
`docs/records/audits/WP-4L-01-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-11）：项目负责人在当前开发会话中明确声明“验收通过,进入下一步”。负责人
接受最终候选 `a3156f3b78177816352eef82004c91b982e24513`，WP-4L-01 据此标记为 `accepted`。本记录不补写
负责人未提供的设备、步骤或 CI 事实；原始声明和既有自动证据见
`docs/records/audits/WP-4L-01-OWNER-ACCEPTANCE.md`。

#### WP-4-03：MCP 生命周期与工具调用等价

治理与实现激活（2026-08-11）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
前置条件：WP-4L-01 已由项目负责人明确验收并标记 accepted
base_ref：a3156f3b78177816352eef82004c91b982e24513
范围：Core generation 私有 MCP 配置与 stdio/SSE session、ToolRegistry/Action ID 调用链、平台桌面 MCP 设置状态、受控进程树清理、文档与测试
required profiles：docs、smoke、core-host、runtime-v2-shell、journey-tools、journey-mcp
任务契约：harness/tasks/WP-4-03.json；不创建 activation
非目标：修改 tools/mcp、Python 插件、TTS、截图 resource token、浏览器、主动调度、提醒、通用 worker 平台
```

规范和分阶段回退分别见
`docs/specs/runtime-v2/WP-4-03-mcp-lifecycle-tool-parity.md` 与
`docs/plans/runtime-v2/WP-4-03-mcp-lifecycle-tool-parity.md`。架构预检确认复用 ADR-0001/0002/0004/
0005/0007，不新增 ADR。任何 WP-4-03 生产修改前必须先运行
`runtime\python.exe -m harness check WP-4-03`；不得修改 `data/**`、`characters/**`、`third_party/**` 或
`tools/mcp/**`。

自动门记录（2026-08-11）：产品候选 `f06392b8e00eb976555a8e455059b8e7312bde34` 的
`harness verify WP-4-03` 为 21/21 自动 case 通过、0 failed、0 blocked，机器状态为
`manual_pending`。WP-4-03 据此进入 `stabilizing`，等待真实 Windows MCP、原生确认允许/拒绝/超时、
Core crash/recovery、设置状态重绑、日志脱敏和退出零残留人工验收；负责人明确验收前不得标记
`accepted` 或激活 WP-4-04。完整自动证据见
`docs/records/audits/WP-4-03-AUTOMATED-VALIDATION.md`。

负责人验收记录（2026-08-12）：项目负责人在当前开发会话中明确声明“我验收了,你来标记然后继续”。
负责人接受当前最终候选 `80764fa55d9dbb69e44f4bd5f634093f44d79010`，WP-4-03 据此标记为
`accepted`。本记录不补写负责人未提供的设备、步骤或 CI 事实；原始声明和既有自动证据见
`docs/records/audits/WP-4-03-OWNER-ACCEPTANCE.md`。

#### WP-4L-02：人类可读运行日志与 Prompt Trace

治理与实现激活（2026-08-12）：

```text
状态：stabilizing（当前唯一 active/stabilizing Work Package）
前置条件：WP-4-03 已由项目负责人明确验收并标记 accepted
base_ref：80764fa55d9dbb69e44f4bd5f634093f44d79010
范围：Rust 单写者人类可读运行日志、私密本地 Agent Trace、最终 Provider payload provenance、回复处理追踪、设置开关、文档与测试
required profiles：docs、runtime-v2-shell、python-full、journey-observability、journey-agent-trace（smoke、core-host 另行运行；task 按 Harness 去重规则由 python-full 覆盖）
任务契约：harness/tasks/WP-4L-02.json；不创建 activation
非目标：日志查看器、目录/清除按钮、远程 telemetry、结构化运行日志 sidecar、聊天历史或请求回放源
```

本 Work Package 以新的 ADR、normative Spec 与实施计划冻结双日志边界。`sakura-runtime.log` 只保存
旧版控制台风格的人类可读运行事件；`sakura-agent-trace.log` 保存按 operation 成块提交的 Prompt/Agent
请求与回复记录。任何正文追踪都必须保留普通用户内容，同时无条件移除凭据与二进制正文；写入失败不得
改变聊天、工具、Core health 或退出结果。

自动门记录（2026-08-12）：实现候选 `cb7066b5c1f3a77d94ff86da5c70cc69f8f4007a` 的
`harness verify WP-4L-02` 为 17/17 唯一自动 case 通过、0 failed、0 blocked，机器状态为
`manual_pending`。WP-4L-02 据此进入 `stabilizing`，等待负责人检查真实运行日志、一次完整工具对话的
Prompt Trace、设置开关和本机隐私边界；负责人明确验收前不得标记 `accepted`。完整自动证据见
`docs/records/audits/WP-4L-02-AUTOMATED-VALIDATION.md`。

可读性验收缺陷（2026-08-12）：负责人检查真实日志后确认普通日志被
`runtime_lifecycle_snapshot` 成功轮询刷屏，且单个 Agent Trace request 因完整工具 schema 和逐消息历史
展开达到 840 行，无法有效阅读。WP-4L-02 据此退回 `active`；修复必须把通用成功命令降到 debug、规范
耗时精度，并在不丢失真实顺序、角色、正文和总量统计的前提下压缩 history 与工具定义展示。事实记录见
`docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

2026-08-12 第二次真实日志复验发现，降噪后的普通日志只剩 Core 请求和少量泛化事件，无法回答一次对话
经过了记忆召回、Prompt 构建、Provider、工具、截图、回复展示和 TTS 中的哪些阶段，也无法用稳定字段定位
失败所在。WP-4L-02 再次退回 `active`；修复必须建立以用户可观察业务里程碑为主的有限事件目录，并以
`op`、`trace`、`call` 串联普通日志与 Agent Trace。缺陷证据追加在
`docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

2026-08-12 项目负责人正常退出真实 Sakura 后，单实例锁相关验收恢复；修复候选的
`harness verify WP-4L-02` 为 17/17 自动 case 通过、0 failed、0 blocked，机器状态为
`manual_pending`。WP-4L-02 据此恢复 `stabilizing`，等待负责人用真实对话、工具、截图与 TTS 复验普通
日志的业务链和定位信息；自动证据见 `docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

可读性修复自动门（2026-08-12）：候选 `4d0bef57142db1742e91443330f64e30fbdfc81a` 将通用 WebView
command 的 started/completed 在 Rust 持久化边界强制降为 debug，规范耗时精度，并把连续 history 与固定
工具 schema 改为保真紧凑展示。23 条短历史、18 个工具的固定合成 request 为 169 行，缺陷现场旧 request
为 840 行；两者正文长度不同，因此只用于验证展示开销已被收紧，不作为同 payload 性能对比。
`harness verify WP-4L-02` 17/17 自动 case 通过，状态为 `manual_pending`。WP-4L-02 恢复
`stabilizing`，等待负责人重新检查真实默认日志和 Trace；本条不构成 `accepted`。

分叉整合前复验（2026-08-12）：日志业务事件链干净候选
`bc643954615304aefdcb9e78b78ebadbbb5e03d2` 的 `harness verify WP-4L-02` 为 17/17 自动 case 通过、
0 failed、0 blocked，状态为 `manual_pending`。WP-4L-02 继续保持 `stabilizing`；远端 WP-4-04 提交暂不
合并，等待负责人先验收真实普通对话、工具、截图、TTS 和 Agent Trace。自动证据追加在
`docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

第三次真实日志验收缺陷（2026-08-12）：负责人检查 `data/logs` 的两份实机日志后确认，普通日志的
Provider、Core IPC、WebView 与未处理异常失败行只剩泛化错误码或“失败”，安全错误原因在 bridge/writer
白名单中丢失，无法仅凭用户提交日志定位；Agent Trace 的缩进 JSON 文档流仍不适合人工浏览。WP-4L-02
据此退回 `active`。修复必须持久化经过严格脱敏和限长的错误类型、Provider code/message、deadline 与
诊断摘要，并把活动 Trace 改为 `====` 包围的 Request/Reply 人类可读块；内部 staging 可继续使用 JSON。
事实记录追加在 `docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

第三次缺陷修复自动门（2026-08-12）：干净候选 `e8abcca20bb5262e96bd6b9e322b9cb3bc75aaa6`
的 `harness check WP-4L-02` 通过；`harness verify WP-4L-02` 为 17/17 自动 case 通过、0 failed、
0 blocked，状态为 `manual_pending`。活动 Agent Trace 已改为人类可读 Request/Reply 文本块，失败日志
已补齐经过白名单、脱敏和限长的 Provider/Core/WebView 诊断以及不同请求期限。WP-4L-02 据此恢复
`stabilizing`，等待负责人再次检查真实失败和正常对话生成的两份日志；本条不构成 `accepted`，远端
WP-4-04 继续保持未合并。自动证据追加在 `docs/records/audits/WP-4L-02-READABILITY-DEFECT.md`。

第三次候选启动复核（2026-08-12）：负责人要求实机检查后，Agent 构建并启动当前 debug 候选，真实
`sakura-runtime.log` 仍出现重复的 `Runtime diagnostic event`、没有首条安全摘要的“Core 输出了异常诊断”
以及丢失 MCP 稳定原因码的“Core 内部诊断”。这些启动现场行仍无法单独支持用户日志排障，WP-4L-02
再次退回 `active`。修复只收口重复 Shell 启动诊断、普通 stderr 首条安全摘要和 MCP 固定事件映射，不把
任意异常对象、凭据或正文放入普通日志。

实机启动修复最终自动门（2026-08-12）：候选 `da7dac617fe4bb8088569544bcf118984e593849`
的 `harness check WP-4L-02` 通过；`harness verify WP-4L-02` 为 17/17 自动 case 通过、0 failed、
0 blocked，状态为 `manual_pending`，Python unit 为 670 passed/6 skipped。WP-4L-02 据此恢复
`stabilizing`，等待负责人在真实候选发送一条对话并检查运行日志业务链与 Prompt/Reply 报告块；本条不构成
`accepted`，WP-4-04 继续保持未合并。

第四次负责人反馈（2026-08-12）：活动 Agent Trace 虽已改成文本块，但结构化回复、模型参数和工具数据仍
显示 JSON 语法。负责人要求可见日志彻底使用人类可读中文层级，不保留 JSON 展示。WP-4L-02 再次进入
`active`；内部 staging 可继续使用 JSON 支持崩溃恢复，但活动日志必须使用中文字段、编号项目、“是/否”
与“无”，同时保留未知字段原名和真实 payload 顺序。自动门重新通过前不得恢复 `stabilizing`，WP-4-04
继续保持未合并。

第四次中文 Trace 自动门（2026-08-12）：候选 `6e1ab145f14eb06f73554ab5e39e1b28bd67dc4c`
的 `harness check WP-4L-02` 通过；`harness verify WP-4L-02` 机器报告
`temp/harness/20260812T150629.305916Z-WP-4L-02.json` 为 17/17 自动 case 通过、0 failed、0 blocked，
状态为 `manual_pending`。活动 Trace 的结构化回复、模型参数和工具数据均改用中文层级文本，新增测试证明
没有 JSON 字段/括号语法、未知字段不丢失、列表顺序及布尔/null 含义保留。WP-4L-02 据此恢复
`stabilizing`，等待负责人实机检查新生成的一次请求和回复；本条不构成 `accepted`，WP-4-04 继续未合并。

第五次负责人实机反馈（2026-08-13）：真实首轮对话在 Memory 仍为 `loading` 时直接完成零条召回，且
MCP 工具注册晚于 Prompt 构建，导致发送给 Provider 的工具集合不完整；回复后自动触发的记忆整理模型
调用没有 operation correlation，也没有进入 Agent Trace。WP-4L-02 据此再次退回 `active`。负责人明确
要求修复上述 2、3、4 项，故任务允许范围审计性扩展到 Memory owner、MCP provider、memory curator 及
相应测试；修复必须在聊天 Prompt 构建前执行有界且可取消的依赖等待，超时或失败时记录可定位的明确
降级原因，并把每次后台记忆整理作为独立 operation 写入运行日志与 Agent Trace。自动门重新通过且负责人
实机复验前不得恢复 `stabilizing`，不得标记 `accepted`，也不得合并 WP-4-04。

第五次缺陷修复自动门（2026-08-13）：范围修订提交 `c6e6c56e6245e7965f31235984fce3e9b8a7febd`
经 `harness check WP-4L-02` 审计通过；实现候选 `5fa7875588015ffb67612c121a34ad0447f52a37`
的 `harness verify WP-4L-02` 报告 `temp/harness/20260812T170000.413880Z-WP-4L-02.json` 为
17/17 自动 case 通过、0 failed、0 blocked，状态为 `manual_pending`。其中 Python unit 677 passed/6
skipped、integration 59 passed/2 skipped、Qt UI 24 passed；docs、runtime-v2-shell、python-full、
journey-observability 和 journey-agent-trace 全部通过。WP-4L-02 据此恢复 `stabilizing`，等待负责人用真实
应用发送新对话，确认 Memory/MCP 依赖行、最终工具/记忆数量和后台“记忆整理”Trace；本条不构成
`accepted`，WP-4-04 继续未合并。

负责人验收记录（2026-08-13）：项目负责人在当前开发会话中明确声明“这个我也验收过了”。暂停任务的
固定基线按负责人已批准的冲突处理方式前移后，`harness check WP-4L-02` 通过，最终
`harness verify WP-4L-02` 为 17/17 自动 case 通过、0 failed、0 blocked。WP-4L-02 据此标记为
`accepted`；原始声明、候选、基线修订与最终证据见
`docs/records/audits/WP-4L-02-AUTOMATED-VALIDATION.md`。

#### WP-4-04：Python 插件能力等价

WP-3-03C 插入暂停与恢复（2026-08-13）：项目负责人要求把已整合的 WP-4-04 暂时退回 `planned`，
其插件候选、测试和证据原样冻结，不回滚代码。WP-3-03C 经负责人验收 accepted 后，按同一实施授权把
固定 `base_ref` 单向前移到其验收收口提交 `6331b586d`，并恢复 WP-4-04 为唯一 active；本次恢复不构成
WP-4-04 accepted。

WP-3-03D 搁置与再次恢复（2026-08-14）：项目负责人明确要求停止界面优化并继续推进主线。WP-3-03D
退回 `planned` 后，WP-4-04 的固定 `base_ref` 单向前移到已整合液态 PoC 自动修复的提交
`e5b57f64591c9605fe74ec2fbb05c93db9289a5c`，并恢复为唯一 active。本次恢复不构成 WP-4-04 accepted。

```text
状态：stabilizing（浏览器插件与助手权限边界自动门通过，等待负责人复验）
前置条件：WP-4L-02 已由项目负责人明确验收并标记 accepted
范围：generation 私有插件 worker、manifest/permission/discovery、tool/prompt/context/event、插件启停与声明式设置/action、受控清理、文档与测试
建议自动验证：docs、smoke、core-host、runtime-v2-shell、journey-tools、journey-plugins
非目标：修改 plugins 或 data、renderer、Qt widget/tools tab、浏览器/移动桥接、TTS、截图、插件安装更新、通用 worker 平台
```

规范、架构选择和分阶段回退分别见
`docs/specs/runtime-v2/WP-4-04-python-plugin-capability-parity.md`、
`docs/adr/0016-runtime-v2-generation-private-plugin-worker.md` 与
`docs/plans/runtime-v2/WP-4-04-python-plugin-capability-parity.md`。插件 worker 是当前 Core generation 的
私有后代和资源，不是安全沙箱或第二生命周期根。后续修复应运行受影响的产品 profiles，并继续使用隔离
assistant root 验证插件数据兼容、真实用户数据零意外变化和 worker 资源回收；需要跨模块修复根因时不受
预设文件范围限制。

2026-08-14，恢复提交 `7b485e9f` 后的 `harness verify WP-4-04` 在 Windows x64 本机完成 21/21
自动 case，0 failed、0 blocked，返回 `manual_pending`。WP-4-04 据此进入 `stabilizing`；Windows 隔离
assistant root 实机清单和项目负责人明确验收仍未完成，因此不得标记 `accepted` 或开始 WP-4-05。

同日，项目负责人实机检查发现两项退出条件缺陷：Sakura Mobile 的声明式设置 action 因只读状态字段被
回传而报 `SETTINGS_VALUES_INVALID`；移动网页虽能打开，但 Runtime v2 尚未提供 WP-5-05 所属的移动聊天
宿主桥，页面只能显示“移动端聊天服务尚未就绪”。WP-4-04 据此退回 `active`：设置保存/action 必须只向
插件回调传递可编辑字段；延期的宿主服务必须 fail closed 并显示 `unavailable/degraded`，不得呈现为可用
空壳。修复重新通过自动门和负责人复验前，不得恢复 `stabilizing`。

纠正提交 `31204775` 的 `harness verify WP-4-04` 完成 21/21 自动 case，0 failed、0 blocked，返回
`manual_pending`，报告为 `temp/harness/20260813T183843.465618Z-WP-4-04.json`。WP-4-04 据此恢复
`stabilizing`；该结果不构成项目负责人对设置页状态或延期移动桥行为的人工复验，复验前不得 accepted。

同日，项目负责人检查真实运行日志、Agent Trace 和聊天历史后指出：Playwright 导航被错误地要求二次
确认，插件失败只留下泛化错误，工具结果续传到 Gemini 3.1 又收到 HTTP 400。负责人明确当前产品仍是
用户驱动助手，现阶段所有工具应直接执行；权限机制只在未来自主 Agent 插件阶段重新设计和启用。
WP-4-04 因此再次退回 `active`。修复须停用当前 pending-action 激活入口和确认设置，保留底层基础设施为
延期代码；插件失败须输出脱敏稳定 reason code，并保留 Provider 要求原样续传的工具调用元数据。新的
自动门和负责人实机复验前不得恢复 `stabilizing` 或标记 `accepted`。

修复提交 `2c662448` 的 `harness verify WP-4-04` 机器报告
`temp/harness/20260813T190023.288549Z-WP-4-04.json` 为 `manual_pending`：21/21 自动 case 通过、0 failed、
0 blocked，required profiles 全绿。真实私有 worker 直执行 Playwright 导航也已成功。WP-4-04 据此恢复
`stabilizing`，等待项目负责人复验浏览器无确认直接执行、失败 reason code 和 Gemini 工具结果续传；本条
不构成人工验收或 `accepted`。

负责人验收记录（2026-08-15）：项目负责人在当前开发会话中明确声明“wp4-4通过了，帮我accepted”。
负责人接受产品候选 `2c662448a8629086a2d39490220f18986f42eb1e`；声明时当前最终 HEAD 为
`e95562dc9ae238a46a47325e28541b9182261269`。WP-4-04 据此标记为 `accepted`。本记录不补写负责人未提供的
设备步骤或 CI 事实；原始声明和既有自动证据见
`docs/records/audits/WP-4-04-OWNER-ACCEPTANCE.md` 与
`docs/records/audits/WP-4-04-AUTOMATED-VALIDATION.md`。随后按负责人明确授权插入 WP-3-03E；本验收不预先
接受该 macOS 视觉工作包。

#### WP-4-05：TTS、播放与音频设备门禁

验收收口（2026-08-17）：

```text
状态：accepted
前置条件：WP-4-04 与插入的 WP-3-03E 均已由项目负责人明确验收并标记 accepted
base_ref：be006fe0ed35f0a2a482670fe31b6b4ed866535c
范围：无 Qt TTS 合成、本地服务独占子进程、Rust 默认设备播放、多段字幕同步、语音设置/整合包、每角色最近 100 条语音留存
required profiles：docs、smoke、core-host、runtime-v2-shell、journey-tts、journey-observability、python-full
数据：测试仅写隔离 assistant root；真实聊天语音写 data/voice/recordings，播放副本写 generation 私有 cache
非目标：设备选择器、通用 resource token、Backchannel TTS、历史回放/收藏 UI、Renderer、Studio、截图
```

架构、长期行为与实施顺序分别见 `docs/adr/0023-runtime-v2-tts-audio-ownership.md`、
`docs/specs/runtime-v2/WP-4-05-tts-playback-audio-device-gate.md` 和
`docs/plans/runtime-v2/WP-4-05-tts-playback-audio-device-gate.md`。bundled GPT-SoVITS 启动前只可强制
终止同一用户且命令行精确匹配当前配置的旧进程树；未知端口占用者必须 fail closed。自动门通过后只进入
`stabilizing`，Windows/macOS/Linux 真实默认音频设备证据和项目负责人明确验收前不得 accepted 或开始
WP-4-06。

项目负责人于 2026-08-17 明确确认“我实机验证通过了”。最终收口候选
`b609ab83611ea59e60522de56182787db3427c08` 的 required profiles、本机严格 Rust 编译以及 Windows x64、
macOS arm64、Linux x64 CI 均通过，编译与测试日志警告为 0。自动证据与人工声明分别记录在
[`WP-4-05-AUTOMATED-VALIDATION.md`](../../records/audits/WP-4-05-AUTOMATED-VALIDATION.md) 和
[`WP-4-05-OWNER-ACCEPTANCE.md`](../../records/audits/WP-4-05-OWNER-ACCEPTANCE.md)。该结论关闭 WP-4-05，
不预先接受 WP-4-06。

#### WP-4-06：截图、受控资源与平台权限

治理激活（2026-08-17）：

```text
状态：active（当前唯一 active/stabilizing Work Package；只允许范围冻结、Spec/ADR/Plan 与验证设计）
前置条件：WP-4-05 已 accepted
base_ref：b609ab83611ea59e60522de56182787db3427c08
目标：完成 CAP-015 手动截图纵向链、generation 私有受控图像资源、相关设置与平台权限错误恢复
必须冻结：legacy 正常/错误路径、Python/Rust/WebView/平台所有权、command/event/Operation/resource token、数据与资源生命周期
平台门：Windows/macOS/Linux；覆盖多屏与 DPI，macOS 屏幕录制权限，Linux X11 与 Wayland portal
非目标：自动观察、主动互动、提醒与调度（WP-4-07），以及无真实截图消费者的通用资源平台
```

WP-4-06 当前只激活范围冻结，不声称产品实现已经开始。进入实现前必须按能力台账补齐可执行 Spec、必要
架构取舍、实施计划、自动故障注入、真实应用验收和独立回退；对应设置只有在平台权限、受控图像资源与
多屏/DPI 门可验证后才能开放。

### Phase 5：配置、平台桌面能力与桥接等价

| WP | 对应能力 | 主要结果 | 强制退出证据 |
|---|---|---|---|
| WP-5-01 | CAP-018/019 | 审计此前逐域完成的配置仓库和 change plan，收口剩余外观/布局 feature 与跨域一致性缺口；不重建巨型保存事务 | 逐域失败不产生半更新；冲突旧控件有等价/替代决定；Qt 可读数据、三平台路径/权限通过 |
| WP-5-02 | CAP-020 | 执行设置 feature 关闭清单，编排已 accepted 切片的逐域结果与首次设置；不在此集中补造领域后端 | 所需 feature 均真实可用或有批准替代；键盘/IME/焦点、密钥输入、失败恢复和重新打开状态一致 |
| WP-5-03 | CAP-006/007/021 | 补齐运行中 Session 切换优化、历史分页与完整角色切换等价 | 旧 generation/资源失效；角色、历史、Memory/TTS scope 不串线 |
| WP-5-04 | CAP-022 | 在 WP-3U-01 右键菜单基础上补齐托盘、置顶、快捷键、显示隐藏和开机启动 | Windows/macOS/Linux 原生行为、权限和重复注册/卸载通过 |
| WP-5-05 | CAP-023/024 | 浏览器自动化与移动/本地桥接 | 浏览器树受控；端口/防火墙/鉴权安全；插件不拥有第二生命周期根 |
| WP-5-06 | CAP-025 | 扩展诊断、Runtime Repair、安全重试和更新前置检查 | 三平台路径/日志/权限诊断；任何自动下载/替换须单独批准且可回退；失败后仍可退出 |

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
| WP-7-03 | CAP-001–030 | 产品功能等价与数据兼容总审查 | 台账逐行 parity-accepted/获批替代；冻结 oracle 往返通过；批准 Legacy Qt 删除清单 |
| WP-7-04 | CAP-028 | 打包、签名/notarization、更新、完整性、干净安装 | 三平台正式工件从零安装/更新/回退，包内 Python 唯一且受控，不含 Legacy Qt 桌宠 |
| WP-7-05 | CAP-029 | soak、休眠恢复、重复启停和故障注入 | Core/MCP/TTS/browser/更新链无泄漏、死锁、数据损坏 |
| WP-7-06 | 全部 | 最终发布审查与进入 `dev` 决策 | P0/P1=0；回退演练完成；项目负责人明确批准合并/发布 |

WP-7-02 本身承载 Phase 1P deferred 的发布前真实设备硬门禁，不以 CI 或 Xvfb 替代：macOS
Apple Silicon 覆盖透明窗口、命中、拖动、焦点、中日文 IME、Retina、Spaces、多屏；Linux X11
覆盖透明窗口、命中、拖动、焦点、IME、多屏；Linux Wayland 覆盖透明、命中、拖动、焦点、
IME、窗口身份与 compositor 行为。任一对应平台未完成这些设备证据时，不得正式发布。

2026-07-27 deferred 设备证据登记：WP-3U-01 的 Windows 真实 Tauri WebView 设置窗口和产品右键菜单、
WP-3U-02 的真实角色立绘/外观预览与固定窗口包络，均须在 100% 与 150% DPI 下复验菜单位置、窗口
几何、立绘缩放、气泡/输入框位置、IME 候选窗、预览/取消和关闭行为。项目负责人明确接受本轮不执行
这些设备组合的非失败型证据风险；这不是已知产品缺陷，也不放宽任何其他退出门。后续若发现可复现且
可归因于 WP-3U-01 或 WP-3U-02 候选实现的 DPI 缺陷，必须重新打开相应责任 WP，不能仅归责于 WP-7-02。

## 15. 已记录但不阻塞基础聊天的未来设计

下列方向可以保留在 ADR、backlog 或所属能力 WP 中，但在 WP-3V-01 前不是硬门禁：

- 完整 control/interactive/background 三级业务优先级和通用 worker process 框架。
- 面向 Tools、MCP、导入、Memory 等全部任务的通用 Operation 与 progress 模型。
- 截图、音频、角色导入共用的 resource token 和完整资源权限平台注册表。
- 完整 Snapshot component model、所有未来 component 类型和通用 patch 模型。
- 完整 progress 合并、多等级背压和跨业务公平调度。
- schema 代码生成平台、Named Pipe/Unix Domain Socket 替换和未来 transport 抽象。
- 完整 Runtime Repair 页面、自动修复、在线下载/替换 Runtime 和通用日志浏览平台。
- 未来 Agent Capability Broker、任务图、多 Agent Runtime 和自治任务平台。

统一约束：设计方向已记录，不阻塞基础聊天架构验证；在出现对应真实消费者时由所属 Work Package 验证并冻结。
