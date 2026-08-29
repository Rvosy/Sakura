---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-29
---

# Runtime v2 设置功能增量迁移规范

```text
规范状态：Normative
决策日期：2026-07-28
执行状态唯一真相源：docs/plans/runtime-v2/work-packages.md 第 2 节
已关闭硬门：WP-3U-02 已于 2026-07-29 accepted
当前设置执行项：只以 docs/plans/runtime-v2/work-packages.md 为准，本文不复制当前状态
```

## 1. 架构依据

设置不再等到 Phase 5 一次性补齐，而是按 feature 随对应领域能力纵向迁移。该架构选择、候选方案和
长期后果见 [`ADR-0007`](../../adr/0007-incremental-settings-feature-migration.md)。本文定义每个设置
切片必须满足的行为、数据、安全和验收契约。

这个调整不改变单一 Work Package 规则。已经完成的设置切片及当前激活项只以 Work Package 状态源为
准；每个后续领域仍只开放其冻结 feature，不重新开放被固定桌宠 UI 否决或尚无真实消费者的控件。

## 2. 适用前提

- WP-3U-01 提供同一 Tauri App 内唯一设置窗口、关闭协调和 capability shell。
- WP-3U-02 提供跨 WebView、Rust、Python 当前配置的首个设置纵向链。
- 旧设置页面继续作为 canonical 视觉与交互基准，不建立第二套设置 UI。
- Memory、Tools、MCP、插件、TTS、截图和主动互动只能由各自领域 Work Package 开放，不能因为
  页面或控件已经存在而提前标记可用。

## 3. 迁移单位与完成定义

迁移单位是一个稳定的 feature key，例如 `providers.credentials`、`model.chat_slot`、
`voice.playback`，不是整页，也不是旧设置返回对象中的全部字段。

每个 feature 必须一次性交付：

1. 明确数据与运行态所有者，以及唯一当前 v1 schema 和锁边界。
2. `get` 只返回该 feature 所需的公开 DTO；密钥、裸路径和私有插件数据不得进入通用 Snapshot。
3. `validate` 在任何落盘或运行态修改前完成，错误指向稳定的 feature/field。
4. `save` 逐域原子提交，保留未知字段；失败时旧文件和旧运行态仍有效。
5. 普通设置保存返回 `applied` 并遵守 ADR-0032；只有非在线能力才返回下次启动生效或不支持。
6. WebView 只拥有草稿和未提交预览；Rust 只做权限、identity、窗口和调用协调；领域真相留在 Python
   Core 或已经批准的原生平台服务。
7. capability manifest 只开放已经完成上述闭环的 feature；未迁移控件继续禁用并显示稳定原因。
8. 自动测试、隔离目录故障注入、真实 Tauri 操作、重新打开一致性和独立回退全部通过。

一个页面可以包含多个状态不同的 feature。页面可进入不代表其中所有控件可用；禁止为方便前端而把
未迁移控件接成 no-op，也禁止一次“保存全部”跨越多个尚无共同事务所有者的配置域。

## 4. Capability manifest 演进

Capability manifest 只保留 feature 级结构，schema 固定为 v1：

```json
{
  "schemaVersion": 1,
  "windowGeneration": 7,
  "sections": {
    "providers": {
      "status": "available",
      "features": {
        "providers.manage": "available",
        "providers.credentials": "available",
        "providers.list_models": "available",
        "providers.test_connection": "available"
      }
    }
  },
  "unavailableReasons": {
    "voice.playback": "语音运行时尚未迁移"
  }
}
```

约束：

- 状态只允许 `available`、`read_only`、`unavailable`；未知 section、feature 或状态一律按
  `unavailable` 处理。
- manifest 只能包含能力 ID、状态、窗口 generation 和非敏感原因；不得包含配置值、路径、Provider
  地址、模型名、插件内容或 credential-shaped 字段。
- 前端必须按 feature 控制单个输入和操作按钮，只接受示例中的字段结构；旧 section 级结构直接拒绝。
- 正式发布前新增字段仍保持 schema v1，并同步更新 frontend freshness、secret scan 和窗口 generation 测试。

## 5. 交付顺序

设置的实际交付顺序由消费者依赖、数据风险和可回退性共同决定，不按表单字段多少排序。下表只表达
迁移顺序和领域归属，不复制 Work Package 当前状态。

| 顺序 | 设置切片 | 目标 WP | 相对难度 | 开放条件与边界 |
|---:|---|---|---|---|
| 1 | 当前角色只读、立绘缩放、字体、角色主题 | WP-3U-02 | 中 | 已 accepted；其余角色/布局控件保持禁用并由所属 WP 迁移 |
| 1A | 输入栏纯色/Windows 实时高斯效果 | WP-3-03C | 中 | 全局偏好与平台有效模式分离；Windows 开放，macOS/Linux 保留偏好但禁用控件 |
| 2 | 供应商管理、凭据、模型列表/连通性、聊天与视觉模型槽 | **WP-3S-01** | 高 | WP-3U-02 accepted；解决 `setup_required -> ready`，先于 WP-3-04 |
| 3 | 真实聊天直接消费的气泡、输入和打字机交互设置 | WP-3-04 | 低 | 只迁移真实聊天 UI 已消费字段，不改变固定窗口包络 |
| 4 | Memory 设置和记忆管理操作 | WP-4-01 | 高 | CRUD 位于插件提供的常驻记忆页；整理槽位于模型页；整理间隔、embedding 下载与未来导出位于插件设置；Memory 领域、外部存储和降级路径迁移时一并开放 |
| 5 | 内置 Tools 运行限额 | WP-4-02 | 中 | ToolRegistry 与取消/超时真实可用后开放；Runtime v2 不提供确认策略 |
| 6 | MCP 配置与运行状态 | WP-4-03 | 高 | MCP 进程归属当前 generation、凭据和退出门通过后开放 |
| 7 | 插件启停、插件设置与设置 action | WP-4-04 | 高 | 插件发现、私有数据、错误隔离和卸载清理迁移时开放 |
| 8 | TTS Provider、参考音频、播放选项与资源任务 | WP-4-05 | 很高 | 合成/播放 backend、设备错误和受控子进程可用后开放 |
| 9 | 手动截图相关选项 | WP-4-06 | 高 | 平台权限、受控图像资源和多屏/DPI 门通过后开放 |
| 10 | 主动屏幕感知与定时截图选项 | WP-4-07 | 高 | 已 accepted；只接普通 timer、忙时跳过和现有聊天链，不建设通用 Scheduler |
| 10A | 提醒与待办选项 | 未排期 | 未评估 | CAP-017 出现真实需求后单独立项；当前不开放，也不预留协议 |
| 11 | 剩余外观/布局与跨域配置一致性 | WP-5-01 | 中 | 对已迁移仓库做缺口收口；冲突旧控件需明确替代决定 |
| 12 | 首次设置编排、逐域结果和页面迁移关闭清单 | WP-5-02 | 中高 | 只编排已 accepted 的切片，不在此重新造巨型后端 |
| 13 | 角色切换与会话/历史联动 | WP-5-03 | 很高 | 仅设置页开放；原子保存后完整重启 Core generation，角色草稿阻断且 Memory/历史/TTS 不跨角色 |
| 14 | 快捷键、开机启动等系统设置 | WP-5-04 | 高 | 桌宠置顶已由 WP-3U-01 的 2026-08-29 后续决定交付；其余能力需在对应原生平台服务拥有真实读写和撤销语义后开放 |
| 15 | 诊断、日志与 Repair 设置 | WP-5-06 | 高 | 诊断/修复所有者、权限和失败安全门完成后开放 |
| 16 | 角色导入导出、Studio 修改与发布 | WP-6-01 至 06-04 | 很高 | Workspace/Draft、资源校验、原子发布和回滚完成后开放 |

### 5.1 输入栏视觉效果增量契约

WP-3-03C 在 UI schema v1 的 `settings` 中增加可选全局字段 `visual_effect_mode`。缺失按
`gaussian_blur` 读取；WP-3-03D 将合法值扩展为 `solid | gaussian_blur | liquid_glass`，且不得写入角色主题 override。Appearance
publication 保持 v1 并强制发布 `values.visualEffectMode`。Windows capability
`appearance.input_visual_effect` 可用；macOS/Linux 仅把有效模式固定为纯色，保存其他字段时必须保留
原始偏好。初始化失败同样只降级有效模式，不能通过保存路径把偏好改写成纯色。

### 5.1 难度顺序只用于拆分，不用于提前开放

纯技术实现从易到难大致为：

1. 已有真实前端消费者的本地标量设置，例如气泡、输入和打字机字段。
2. 剩余外观/布局字段，以及只编排已完成领域结果的首次设置流程。
3. Provider/模型的公开读取与本地校验、Tools 运行限额。
4. Provider 密钥与原子保存、网络探测、同 generation 热应用，以及 Memory/MCP/插件领域设置。
5. TTS、截图、主动互动、角色切换和 Studio；这些切片包含设备、权限、子进程、外部存储或跨 Session
   资源所有权，不能按普通表单迁移。

难度较低但消费者或原生所有者尚未迁移的控件仍不得提前开放。尤其禁止只迁移 `tts_enabled`、
`launch_at_login`、角色选择或插件启停等表面简单的开关；它们必须与对应运行态读取、生效、撤销、
失败恢复和退出门一起交付。

没有列出的旧控件默认保持 `unavailable`。若固定桌宠产品语义已经使某个 legacy 控件不再适用，例如会
破坏固定窗口包络的自由布局参数，责任 WP 必须记录保留等价、约束后迁移或 `approved-replacement`，
不能静默删除，也不能为了表面等价破坏已验收的窗口语义。

### 5.2 WP-5-03 角色切换设置契约

角色选择是唯一需要完整 Core generation 重建的已开放设置切片。Python 保存成功只返回
`unchanged | core_restart_required`；Rust 对后者只派发一次 restart，前端等待新的 Supervisor、Snapshot、
readiness 和 Character Presentation identity 全部一致后再水合。保存失败不重启，restart 派发失败不回滚或
重写配置。

角色外观、语音和 Memory 草稿属于当前角色，存在未保存内容时阻止选择另一角色。Provider、Tools、Plugin、
Screen Awareness 等全局草稿保留并绑定新 generation。switching 开始即清空旧 Memory/历史内容和游标，禁止
旧 transport、旧分页或迟到事件重新显示。完整行为见
[`WP-5-03-safe-character-switch.md`](WP-5-03-safe-character-switch.md)。

角色下拉只形成设置窗口内草稿，允许在提交前反复改选；选择本身不得写配置、清空角色页面或启动 restart。
它只可调用只读视觉预览命令，将桌宠主题、默认立绘和气泡问候语暂时投影为目标角色；Core、Memory、Timeline、
TTS、名字与回复状态仍绑定正式角色，放弃时恢复正式角色当前应显示的视觉和气泡内容。
“应用”和“保存并关闭”先保存当前 generation 的其他域，最后只提交一次最终角色。放弃设置或选回已提交角色
会清除该草稿；暂存期间锁定仍属于当前正式角色的外观、语音和 Memory 页面。只有收到已提交的 restart
receipt 后才进入 switching，并关闭 Memory editor portal、失效在途查询、清空角色级页面再重新水合。

## 6. WP-3S-01：供应商与模型设置纵向链

### 6.1 目标

在 canonical 旧设置页面中开放“供应商”和“模型”的真实配置能力，使缺少 Provider 的 Runtime v2
可以从 `setup_required` 进入可重试的配置路径，保存后在同 generation 创建/替换 Session，并为
WP-3-04 提供可由用户维护的真实聊天配置。

### 6.2 允许能力

- 读取 Provider 公开信息、配置完成状态、可选模型目录和当前模型槽；读取响应不返回已保存密钥。
- 新增/编辑/删除旧页面已经支持的 Provider 配置，逐字段校验 base URL、Provider 类型和非敏感选项。
- 密钥输入采用“空白保持原值 + 显式清除动作”；`configured=true` 可以显示，密钥本体不能回显。
- 使用新输入或 Core 内已保存凭据执行有界 `list_models`/`test_connection`；错误必须脱敏，control/
  shutdown 不得被网络探测阻塞。
- 保存 Core 与当前 active 插件注册的动态 Chat Completion 模型槽；引用不存在 Provider/模型或遗漏必选
  槽位时，在任何 owner 写入前拒绝。
- Provider、Core-owned 槽与当前 PluginApplication 的插件槽在一次请求中保存，并按稳定 identity 顺序调用插件槽位
  callback。Provider 模型 Snapshot 必须先在 PluginApplication 的有界初始化 deadline 内等待当前 generation
  完成槽位注册，不能把初始化中的空注册表发布成稳定槽位集合。不同 owner 不承诺跨文件事务；后序失败
  返回 `partial`、已保存槽位与失败 owner，
  并刷新真实快照，设置前端不得伪装成整体成功或整体失败。插件保存 callback 报错后不得自动重试写入；
  只允许回读同一 generation 的槽位，且仅在槽位为 `READY`、Provider 与模型均和目标完全一致时调和为
  已保存，同时记录稳定 `MODEL_SLOT_SAVE_RECONCILED` 诊断；回读不一致仍返回原槽位的稳定失败代码。
- 当 readiness 为 Provider 缺失导致的 `setup_required` 时，可以聚焦设置窗口对应页面；这不是完整
  首次设置向导，也不开放 Studio。

### 6.3 数据与安全边界

- `user_root/config/api.yaml` 仍由 Python 配置领域拥有。WebView 不直接访问文件；Rust Gateway 注入
  window/Core generation、request identity 和 deadline，不成为配置真相源。
- `system_config.yaml` 只接受 `config_version: 1`；其他版本、缺失版本和损坏文件均拒绝读写，不迁移。
- 写入使用同目录临时文件、flush/sync 和原子替换；验证或替换失败不改变旧文件，不留下部分文件。
- 新密钥允许作为专用 command 的瞬时 payload 经过 WebView/Rust/Core，但不得进入 capability manifest、
  Snapshot、event、response echo、普通日志、错误详情、测试快照或证据工件。
- `list_models`/`test_connection` 是 Provider 设置的窄消费者，不提前冻结通用 Operation 平台；必须有
  deadline、取消/关窗处理和唯一终态。

### 6.4 明确非目标

- 不迁移 Memory、Tools、MCP、插件、TTS、截图、主动互动或其设置。
- 不实现角色切换、Studio、导入导出或完整首次设置。
- 不整体复用 `app/ui/tauri_settings.py` 的 Qt/线程/进程宿主，不恢复旧 stdio HostRpc。
- 不建立跨 `api.yaml`、`system_config.yaml`、`characters.yaml` 和 v2 `ui.json` 的“保存全部”事务。
- 不因配置保存而改变聊天、Provider fallback 或模型选择的既有业务语义；需要语义变更时另行批准。

### 6.5 实施顺序

WP-3S-01 是单一 Work Package，以下顺序是其内部提交与验证顺序，不表示前一项完成后即可单独宣告
feature 已迁移：

1. **数据门与夹具**：冻结当前 v1 `system_config.yaml`/`api.yaml` schema、损坏 YAML、
   unknown-field preservation 和未修改 secret bytes。
2. **feature capability v1**：只接受 feature 级 manifest；`providers.*`、`model.*` 初始保持
   `read_only` 或 `unavailable`，未知 feature 失败安全禁用。
3. **公开只读 DTO**：接通 Provider 公开信息、`configured` 状态、模型目录和模型槽读取；不得复用会在
   load 时修改配置的 API，也不得返回密钥。
4. **纯 DTO 与整域校验**：在无 Qt 的 Python 配置领域完成 Provider、credential action 和模型槽的组合
   校验；任何字段无效时不修改文件或运行态。
5. **单次原子保存**：一次读取并合并 Provider 与相关模型槽，保留未知字段后一次原子替换 `api.yaml`；
   不得串行调用多个独立 save 造成半更新。完成密钥保持、替换和显式清除语义。
6. **热应用 change plan**：普通保存成功后返回 `applied`，由当前 generation 在聊天边界更新 client 或
   创建/退休 Session；不得调用 Supervisor restart。
7. **有界网络探测**：最后接入 `list_models`/`test_connection`，覆盖 deadline、取消、关窗、Core crash、
   旧 generation 丢弃、唯一终态和全链路错误脱敏。
8. **旧页面逐 feature 开放**：依次开放 Provider 管理、凭据、模型列表/连通性和通用 `model.slots`；模型页
   根据 Core 与 active 插件的真实注册动态增减，不再为每个插件增加 `model.<用途>_slot` 特判。

第 1 至 7 项及对应故障矩阵完成前，旧页面相关输入和操作按钮不得标记为 `available`。禁止从网络探测、
保存按钮或整页启用开始倒序实现。

### 6.6 退出门

- Provider 新增/编辑/删除、密钥保持/替换/显式清除、模型槽引用和重新打开一致性通过。
- `list_models`、连通性成功/认证失败/超时/取消/关窗/Core crash 均为有界唯一终态且错误脱敏。
- 原子替换、权限、损坏 YAML、非 v1 schema、旧 generation 和重复保存不产生半更新。
- 当前 v1 配置读取/修改/回读通过；未知字段和未修改 secret bytes 保持。
- Core 在同 generation 使用新配置达到预期 readiness，Snapshot revision 单调递增；旧 identity 的
  response/event 不改变设置 UI。
- Windows 真实 Tauri 完成中文 IME 密钥/URL/模型输入、模型列表、测试、应用/保存、失败恢复和重新打开；
  公共 Python/Rust/frontend 代码取得同一候选 SHA 的三平台门禁。
- P0/P1、credential 泄漏、进程/请求/临时文件残留和共享锁问题为零。

### 6.7 回退

禁用 `providers.*` 和 `model.*` feature，设置窗口恢复只读/未迁移提示，停止新的 Provider 网络探测；
回退 Gateway、Core Adapter 和前端接线，但不删除、恢复或重写用户现有 `api.yaml`。若生产缺陷涉及写入安全，
Runtime v2 对该域退回只读，不切换用户入口。

## 7. WP-3-04：真实聊天表现设置切片

WP-3-04 开放两个彼此独立提交的 feature key。`chat.presentation_timing` 公开 DTO 只包含：

- `subtitle_typing_interval_ms`：完整回复进入 WebView 后的逐字显示间隔；
- `reply_segment_pause_ms`：相邻完整回复段之间的展示停顿。

两项字段必须使用有界整数、一次原子保存到 Runtime v2 `ui.json`，保存成功后立即作用于后续回复；失败
时旧持久值和当前运行值都保持不变。设置窗口重新打开必须回读已提交值，未提交预览和旧 window
generation 的结果不得覆盖新值。WebView 只持有草稿和当前展示 timer，不成为持久化真相源。

`chat.subtitle_language` 只持久化 `subtitle_language: "zh" | "ja"`。缺失或非法值读取为默认 `zh`，
下一次成功保存时规范化；`zh` 优先展示 segment `translation`，空值回退 `text`，`ja` 展示 `text`。
主窗口通过 `sakura.chat.subtitle.toggle` 右键菜单动作切换，菜单 manifest 必须返回 checked 状态；保存失败
保持旧文件、旧运行值与旧勾选态。切换成功后，输入中的当前段立即清空并按新语言从头重播；settled 或
当前会话回看段立即完整替换，不等待下一条回复、不回放已完成段，也不切换立绘。
该 feature 不读写 `system_config.yaml`，只使用当前 v1 `ui.json`。

`appearance.character` 已迁移的角色名、气泡/输入字体和主题 token 继续复用，不在本 WP 重复建模。
`bubble_auto_hide_enabled`、`bubble_auto_hide_delay_seconds`、气泡高度、输入栏偏移和自由布局字段继续
`unavailable`：它们会破坏 WP-3-03 冻结的常驻气泡、常驻输入和固定窗口包络。Enter 发送、Shift+Enter
换行与 IME composition 门禁是固定产品交互；Runtime v2 不提供“立即显示”控件，也不新增对应配置开关。

回退时先把 `chat.presentation_timing` 和 `chat.subtitle_language` capability 恢复为 `unavailable`，停止新的
预览 timer，回退 Gateway/前端接线；不得删除、恢复或重写用户已有 `ui.json`。

## 8. 后续 WP 的强制设置责任

从本规范生效后，任何拥有用户可配置能力的 WP 在激活记录中必须增加“设置切片”字段，明确：

- 本 WP 开放哪些 feature key；哪些旧控件仍禁用以及原因。
- get/validate/save/change plan 的所有者和失败原子性。
- secret、路径、插件私有数据和 generation identity 如何隔离。
- 对应真实页面、键盘/IME、应用/保存/取消、重新打开和运行态生效验收。
- 独立禁用与回退方式。

若一个能力 WP 不需要用户设置，也必须明确写“无设置切片”，避免把遗漏误认为有意延期。Phase 5 的
WP-5-01/02 只负责缺口收口、跨域一致性审计和首次设置编排，不得重新聚合已经分域完成的保存逻辑。

WP-4-01 的 `memory.manage`、Mem0 普通设置 section、动态 `model.slots` contribution、失败降级和独立回退由
[`WP-4-01 Memory spec`](WP-4-01-memory-capability.md) 约束。

设置页面的视觉归属不改变领域所有权：远程整理模型通过 Mem0 owner 的动态槽位呈现在“模型”页；记忆
Collection 只呈现在“记忆”页；整理间隔、embedding 下载/状态和未来导出留在 Mem0 插件设置。任何保存
引发 Core generation 更换时，当前设置窗口必须原位重新绑定并保留草稿、筛选、选中项和 IME composition；
不得用“关闭并重新打开设置”代替重绑定，也不得让旧 generation 的迟到结果覆盖当前页面。

WP-4-02 只保留 `tools.runtime_limits`。Runtime v2 的确认策略、Action ID、原生确认和桌面 MCP 设置均已
删除；WP-4-03 只保留通用 MCP 生命周期与只读状态边界。
