# WP-3U-02：角色包可见能力与外观设置联动

```text
当前状态：以 docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md 第 2 节为唯一真相源
前置依赖：WP-3U-01 accepted
主要结果：优先迁移当前角色包中用户可直接看到的表现能力，并让同 App 设置窗口的角色外观页真实可用
数据边界：只允许批准的角色外观/ui 配置兼容写入；characters/** 只读
回退边界：关闭角色外观保存命令，设置窗口退回能力门控壳，保留当前角色只读展示
```

## 目标

在真实聊天进入 UI 前，先完成角色包的可见表现闭环：角色名、初始消息、主题、默认立绘、表情资源映射、
立绘切换、布局预览和外观配置。真实聊天随后只需把回复段中的 portrait/tone 投影到已经接受的表现层。

本 WP 不实现运行中角色切换、历史分页或完整 Session 重建。设置页中的角色选择控件必须隐藏或明确
禁用，并说明将在 WP-5-03 接入；不得保存 `current_character_id`。

## 能力范围与优先级

第一优先级：

- `display_name` 和 `initial_message`。
- 当前角色 `theme`。
- `portrait.default`。
- `portrait.expressions` 的逻辑键、真实图片和 fallback。
- Fake Core 多段回复中的 portrait key 驱动真实立绘切换。

第二优先级：

- 当前角色立绘缩放和受约束的布局预览。
- 气泡、输入框、字体和主题的当前 Runtime v2 支持子集。
- 应用、保存、取消、失败回滚和重新打开一致性。

不因“角色包相关”提前迁移：

- 角色卡人格/Prompt 对话语义；它由已存在的 Assistant Adapter 和真实聊天 WP 消费。
- TTS 模型、参考音频和 tone refs。
- 角色导入/导出、Studio、草稿、发布和回滚。
- 插件 renderer、Live2D、Canvas 或高级动画系统。

## 所有权

- Python Core：当前角色身份、角色 manifest 校验、公开表现 DTO、兼容角色配置读取。
- Rust/Tauri：`ui.*` 配置仓库、设置窗口命令入口、原子保存协调、受控资源 URL 和当前 generation 缓存。
- WebView：表单草稿、即时预览、立绘交叉淡入、打字机和未提交 dirty state。
- legacy Qt：保留兼容读取者；本 WP 不删除其设置工具或改变无法回读的 schema。

`characters/**` 始终是角色资源真相源且只读。WebView 不得直接写角色包、拼接本地路径或持久化角色业务
对象。预览状态不进入 Python 领域真相源，取消或窗口失败时必须恢复打开设置前的 UI。

## 窄设置契约

旧 `app/ui/tauri_settings.py` 同时包含纯 DTO/校验和 PySide6 进程/线程代码。本 WP 只允许把当前消费者
需要的纯逻辑抽到无 Qt 模块，legacy Qt wrapper 与 Runtime v2 Core Adapter 共同调用；不得整体搬迁该文件。

建议最小命令：

```text
settings.characterAppearance.get
settings.characterAppearance.preview
settings.characterAppearance.save
settings.characterAppearance.cancelPreview
```

约束：

- command allowlist 固定；Rust 注入 request/generation identity，WebView 不能伪造。
- `get` 返回当前角色公开表现、允许的 ui 字段、范围和 capability，不返回 Credential 或裸路径。
- `preview` 只更新 WebView/Rust 短期表现，不写磁盘。
- `save` 逐字段校验并使用现有兼容 schema 的原子写入；失败时旧文件和当前 UI 保持有效。
- `cancelPreview` 恢复设置窗口打开时的基线；重复调用幂等。
- 设置窗口关闭、WebView 崩溃、Core generation 变化或主应用退出都会自动取消未提交预览。
- 不建立跨 `core.*`/`ui.*` 的分布式事务；本 WP 只保存已明确属于角色外观/ui 的窄字段。

## 多角色真实验收

至少使用仓库中的 Sakura 与 N.A.V.I 角色包作为只读验收输入：

- 覆盖横向较宽和纵向较高的立绘宽高比。
- 验证默认立绘、全部 manifest 表情键、缺失/非法资源 fallback。
- 验证角色主题、初始消息和显示名称。
- 验证 portrait 快速切换、旧 decode callback、旧 generation resource 和 reduced motion。
- 验证不同角色资产不会导致窗口包络、气泡或输入框移动。

这只是同一实现对多个真实角色包的验收矩阵，不是产品角色选择功能。

## 设置窗口行为

- WP-3U-01 的设置窗口开放“角色与外观”页面的当前角色信息和已迁移字段。
- 未迁移控件必须删除、隐藏或禁用并附稳定说明；不能发送空操作。
- 预览期间桌宠保持可交互，并使用同一固定窗口包络。
- “应用”保存且保持窗口；“保存”保存并关闭；“取消”恢复未提交预览。
- 保存成功后重新打开设置，值与桌宠实际表现一致。
- 保存失败显示字段/域级错误，不关闭窗口、不留下部分文件或半应用 UI。

## 实施白名单

允许修改：

- `app/core_host/**` 中当前角色公开表现与窄外观设置 Adapter。
- 为去 Qt 复用而新增的纯设置 DTO/校验模块，以及 legacy Qt wrapper 的最小适配。
- `desktop/src-tauri/src/**` 中角色外观 Gateway、ui repository 和受控资源的窄实现。
- `desktop/frontend/settings/**`、`desktop/frontend/pet/**`、共享 DTO/theme/portrait 模块。
- `tools/settings-tauri/**` 中继续消费 canonical frontend/纯契约所需的兼容改造。
- 相关测试、fixture 和规范文档；`characters/**` 只读取证。

明确禁止：

- 修改角色包源资源或角色卡业务语义。
- 保存当前角色选择、运行中 Session 切换或历史分页。
- TTS、Memory、Tools、MCP、插件、截图、主动互动、完整首次设置、Studio、导入/导出。
- WebView 直接写 `data/**`，或 Rust 复制/修改 Python Assistant 业务对象。
- 通用 resource token、完整配置平台或跨域事务抽象。

## 验收门禁

自动测试：

- 两个真实角色 manifest 与立绘矩阵、资源安全和 generation 失效。
- portrait key 正常/缺失/非法/快速切换、decode 失败和 fallback。
- theme 与外观字段 validate、preview、save、cancel、窗口崩溃回滚和重复打开。
- 原子写失败、权限失败、未来 schema、Qt 可读兼容和凭据脱敏。
- PySide6 import guard：bundled Core 执行本能力时不得加载 Qt。
- 固定窗口、命中区域、IME、Fake Core/typewriter/close 回归。

真实 Windows 候选验证 Sakura/N.A.V.I、100%/150% DPI、立绘切换、外观预览、应用/保存/取消、
失败恢复、设置窗口关闭和主程序退出。公共代码须通过三平台构建；macOS/Linux 真实 WebView 留至 WP-7-02。

## 状态与回退

只有 WP-3U-01 accepted 后才能激活。本 WP 完成后进入 `stabilizing`；真实角色、设置联动、兼容写入、
回滚、Qt-free 和资源安全门全部通过且无 P0/P1 后才能 accepted。

回退时禁用/移除角色外观设置命令和保存入口，取消全部预览并恢复持久化基线；设置窗口退回 WP-3U-01
能力门控壳，桌宠保留 WP-3-03 当前角色只读表现。不得删除、恢复或改写角色包和无关用户数据。

## 2026-07-27 激活记录

WP-3U-01 已在唯一状态源中 accepted，WP-3U-02 因此前置依赖满足并于 2026-07-27 激活。本节保存
当次激活快照，不作为当前状态的第二真相源。

实际允许目录：

- `app/core_host/**`、为兼容读取新增的 `app/config/**` 无 Qt 纯 DTO/校验，以及 legacy Qt 消费点的最小适配。
- `desktop/src-tauri/src/**`、`desktop/frontend/settings/**`、`desktop/frontend/pet/**` 和必要的共享前端模块。
- `tools/settings-tauri/**` 中消费 canonical settings frontend 所需的兼容改造。
- `tests/**`、`desktop/frontend/tests/**`、`desktop/src-tauri/tests/**`、相关 fixture、harness 注册和本 WP 文档。

明确禁止目录与范围：

- `characters/**` 始终只读；不得修改角色资源、manifest、角色卡或对话语义。
- 不修改 `third_party/**` 或 `tools/mcp/**`；不提前迁移 TTS、Memory、Tools、MCP、插件、截图、主动互动、
  Studio、导入导出、完整设置平台或 WP-3-04 真实聊天 UI 接线。
- 不保存 `current_character_id`，不实现角色选择、Session 切换、历史分页、通用 resource token、完整配置
  框架或跨域事务抽象。

验收环境：

- 本地 Windows 候选：仓库自带 `runtime/python.exe`、Node test runner、locked Cargo 依赖、真实 Tauri/WebView2，
  以 Sakura 与 N.A.V.I 角色包作为只读输入。
- 自动门禁：相关 pytest、bundled Core PySide6 import guard、frontend `node --test tests/*.test.js`、Tauri
  `cargo fmt --check` 与 `cargo test --locked`、legacy settings host `cargo check --locked`、Harness smoke；影响面
  扩大后运行 Harness unit。
- 公共 Rust/前端代码需要同一候选 SHA 的 Windows x64、macOS arm64、Linux x64 构建证据；未经项目负责人
  授权不 push，无法取得的新 CI 证据不得伪造为通过。

故障矩阵：

| 故障 | 必须行为 | 证据 |
|---|---|---|
| portrait key 缺失、非法或资源 decode 失败 | 确定回退到 default/安全占位；旧 callback 不覆盖新状态 | frontend/Rust 测试与真实切换 |
| 快速切换、reduced motion、旧 generation | 只提交最新 generation/revision；动画关闭时结果等价 | frontend/Rust 测试 |
| 外观字段非法或未来 schema | 拒绝整个请求，不写盘、不半应用 UI、不泄漏凭据/裸路径 | Rust/Python 测试 |
| 原子替换或目录权限失败 | 保留旧文件与持久化 UI 基线，不留下临时/部分文件 | 隔离临时目录故障注入 |
| 取消、设置关窗、WebView 崩溃 | 幂等取消全部未提交预览，恢复设置打开时基线 | Rust/frontend 生命周期测试 |
| Core generation 变化或 App 退出 | 废弃旧预览并恢复最新持久化基线 | Rust 生命周期测试 |
| 重复打开设置 | 聚焦同一窗口；同一预览会话与 dirty state 不被重置 | Rust/frontend 测试与 Windows 验收 |
| 保存成功后重新打开 | 持久化值、桌宠表现和 legacy Qt 兼容读取一致 | Rust/Python 集成测试与 Windows 验收 |

数据写入边界：

- WebView 只能调用固定 allowlist；request、generation 与当前角色 identity 由 Rust 注入，WebView 不直接写
  `data/**`、不接触裸本地路径、credential 或角色业务对象。
- preview 仅驻留内存，不落盘。save 只允许逐字段校验后的角色外观/ui 兼容字段，采用同目录临时文件、
  flush/sync 与原子替换；破坏性写入测试仅使用隔离临时目录，不清理、截断、恢复或删除真实用户数据。
- 保存格式必须能由 legacy Qt 的无 Qt 配置读取路径消费；不写 `current_character_id`，不修改 `characters/**`。

独立回退命令：

```powershell
git revert <WP-3U-02-最终验收提交> <WP-3U-02-稳定化提交> <WP-3U-02-功能提交> <WP-3U-02-激活提交>
```

按从新到旧顺序仅回退本 WP 提交；回退前先取消内存预览并关闭角色外观保存入口。不得删除或恢复用户
配置文件，不回退 WP-3U-01/3-03，也不改写角色包。

计划提交保持单一目的并可独立回滚：激活治理记录；无 Qt 外观 DTO/兼容读取；Rust Gateway、原子仓库与
生命周期；canonical settings 与 pet 预览；稳定化修复和门禁证据；最终接受记录。实际实现若需进一步拆分，
每个生产提交仍须在正文记录 Phase/WP、背景、主要变更、明确非目标、验证、风险和回退方式。

DPI 决定：项目负责人按 G-008 明确接受本轮不执行 WP-3U-02 的 100%/150% DPI 人工验收，记录为尚未
补齐的非失败型设备证据而非已知产品缺陷；真实 DPI/WebView 复验点已登记至 WP-7-02，其他退出门不放宽。
后续若发现可复现且可归因于本候选实现的 DPI 缺陷，必须重新打开 WP-3U-02，不能简单归责于 WP-7-02。

## 2026-07-27 稳定化开始记录

生产候选 `078c18df` 完成后，WP-3U-02 在唯一状态源中进入 `stabilizing`。候选实现包括：

- 当前角色 display name、initial message、角色主题、default/全部 expression portrait 的受控展示。
- 固定 allowlist 的 get/preview/save/cancelPreview，Rust 注入 settings window generation、Core generation 和
  character identity；WebView 不持有数据路径或 credential。
- `data/runtime_v2/config/ui.json` 中批准外观字段的逐字段校验、同目录临时文件与 Windows/POSIX 原子替换，
  保存失败恢复预览基线；legacy Qt 通过无 Qt reader 兼容读取实际存在的批准字段。
- canonical settings frontend 的当前角色只读页及主题、字体、立绘缩放子集；未迁移控件和角色选择被隐藏，
  “应用”保存不关窗、“保存”保存并关闭、“取消”恢复预览。
- 桌宠在固定 816×680 原生包络中应用主题/字体和受约束立绘缩放，同时更新 PNG alpha 原生命中区域；
  气泡、输入框和原生窗口位置不随角色或预览改变。
- 设置关闭、WebView 销毁/重载、Core generation 变化、主应用退出的幂等预览回滚；旧 generation 和非法
  identity/字段由 Rust 与 WebView 双重拒绝。

进入稳定化时已取得的自动证据：frontend 75 passed；Rust 最近完整回归 202 passed、23 ignored，随后新增
的真实角色和保存失败测试单独通过；Python 外观/角色表现/PySide6 import guard 23 passed；Sakura 与
N.A.V.I. 的真实 manifest、全部 portrait keys、受控 URL、资源元数据和不同宽高比已由 Rust 只读测试覆盖。

尚未宣告通过的退出门：最终候选的完整 Rust/frontend/Python/Harness 回归、legacy settings host locked check、
真实 Windows WebView 候选验收，以及同一最终候选 SHA 的 Windows/macOS/Linux 公共构建证据。100%/150%
DPI 人工证据仍按激活记录中的 G-008 决定延期，不得计作已通过，也不放宽上述其他门禁。

## 2026-07-27 候选验收证据更新（保持 stabilizing）

本节保存候选验收快照，不声明第二个当前状态。生产实现候选为 `078c18df`；为在不触碰真实用户数据的
前提下完成保存失败实机复验，又增加了 debug-only、严格限制到系统临时目录的验收注入 `729c2f7c`。

本地自动门禁：

- `runtime\python.exe -m harness run smoke`：25 passed。
- `runtime\python.exe -m harness run unit`：1158 passed，6 skipped；包括外观兼容读取、未来 schema、
  legacy Qt 回读和 bundled Core PySide6 import guard。
- `desktop/frontend` 下 `node --test tests/*.test.js`：75 passed；覆盖 portrait 正常/缺失/非法、快速切换、
  decode 失败、fallback、reduced motion、旧 callback/generation、theme/字体/缩放预览和生命周期回滚。
- `desktop/src-tauri` 下最终 `cargo test --locked`：206 passed，23 ignored；`cargo fmt --check`、
  `cargo build --locked` 均通过。23 个 ignored 均为由原生 platform workflow 或父测试启动的既有夹具/分层门禁，
  未为本 WP 扩大忽略列表。
- legacy settings host `cargo check --locked` 通过；`git diff --check` 通过。

Windows 真实 Tauri/WebView2 候选：

- Sakura 正确展示夜乃桜名称、日文 initial message、粉色主题和 15 个真实 portrait 键（含
  `__default__`）；Fake Core `/multi` 多段回复驱动了可见的真实立绘切换。
- N.A.V.I. 正确展示名称、日文 initial message、绿色主题、不同宽高比立绘和 8 个真实 portrait 键（含
  `__default__`）；Fake Core `/multi` 完成，读取其独立主题和持久化立绘缩放。
- 两个角色及立绘切换、缩放预览期间，原生窗口始终为 816×680，气泡与输入框位置不变。Sakura 的
  150% -> 137% 预览在取消确认后恢复 150%；145%“应用”保存且不关窗；150%“保存”保存并关闭，
  重新打开仍为 150%。
- 设置窗口最小化后重复打开复用同一窗口；设置内取消/关窗不退出桌宠或 Core；Sakura 与 N.A.V.I.
  候选均通过原生退出菜单正常退出并释放共享锁。
- 保存失败候选从 100% 预览至 121% 后执行“应用”，设置窗口保持打开并保留 dirty state，稳定显示
  `APPEARANCE_PERMISSION_DENIED`，桌宠立即回滚至 100%。故障只发生在系统临时目录直属、固定前缀的
  隔离根 `blocked/ui.json`；根中没有 `.tmp` 或部分文件，验收后已在确认进程为零和路径精确匹配后删除。

数据与兼容性证据：

- 真实 `data/runtime_v2/config/ui.json` 在正常“应用/保存”路径中只原子写入批准的 ui/角色外观字段；最终
  `schema_version=1`、`domain=ui`、立绘缩放 150%，保留 Sakura/N.A.V.I. 独立主题，不含
  `current_character_id`、credential-shaped 字段或临时兄弟文件。
- 保存失败前后真实文件 SHA-256 均为
  `6A1FF67606B9BA2C0D6484D65C94AD0C63A963C97780B0C8CAD427FE4059C3FD`；故障注入没有恢复、删除或
  截断真实用户数据。legacy Qt 无 Qt reader 与未来 schema/权限失败测试均通过。
- `characters/**` 全程只读；Sakura 与 N.A.V.I. 仅作为真实 manifest/资源验收输入，没有实现角色选择或保存
  当前角色身份。

DPI 设备证据仍按 G-008 延期：本轮没有执行 100%/150% DPI 人工复验，不能登记为通过；它是项目负责人
明确接受的非失败型设备证据风险，不是已知产品缺陷，复验点已登记 WP-7-02。后续若发现可复现且可归因于
本候选的 DPI 缺陷，必须重新打开 WP-3U-02，不能仅归责于 WP-7-02。

当前唯一未关闭的硬退出门是同一最终候选 SHA 的 Windows 2025 x64、macOS 15 arm64、Ubuntu 24.04 x64
原生公共构建/测试证据。本机只有 Windows 真实候选；安装交叉 target 不能替代原生 macOS/Linux Tauri runner。
本轮未经授权不得 push，因而不能触发所需 platform workflow。此缺口不按 DPI 决定延期，也不能伪造为通过；
WP-3U-02 保持 `stabilizing`，WP-3-04 依赖未满足并保持 `planned`。

当前代码候选的独立回退顺序为：先回退 `729c2f7c` 的 debug-only 验收注入，再按新到旧回退
`078c18df`、`1a99d5d6` 和 `c4a94510`；治理记录 `32c36ba9` 与本证据记录可分别回退。回退前取消内存预览并
关闭角色外观保存入口；不得删除或恢复真实配置、修改 `characters/**`，也不得回退 WP-3U-01/3-03。
