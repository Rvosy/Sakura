# Runtime v2 设置功能增量迁移规范

```text
规范状态：Normative / planned
决策日期：2026-07-28
执行状态唯一真相源：docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md 第 2 节
当前硬门：WP-3U-02 accepted 后才能激活首个新增设置 WP
下一设置 WP：WP-3S-01 供应商与模型设置纵向链
```

## 1. 决策

从 WP-3U-02 之后，设置不再等到 Phase 5 一次性“补齐全部页面”，而是作为一条持续交付轨道，
随对应产品能力逐项迁移。每个设置切片必须同时完成真实读取、字段校验、保存、生效计划、失败回滚、
能力门控和真实应用验收；只恢复旧页面视觉、只接表单、只写配置或只返回成功均不算迁移完成。

这个调整不改变单一 Work Package 规则。WP-3U-02 当前仍是唯一 `stabilizing` 项；在其同一最终候选
三平台硬门关闭前，只允许修改规范和进行只读取证，不开始 WP-3S-01 的生产实现。

## 2. 为什么现在开始

- WP-3U-01 已提供同一 Tauri App 内唯一设置窗口、关闭协调和 capability shell。
- WP-3U-02 已证明第一条设置纵向链可以跨 WebView、Rust、Python/兼容配置完成预览、原子保存、
  失败恢复和 legacy Qt 回读。
- 旧设置页面已经恢复为唯一视觉与交互基准，后续不需要再建第二套设置 UI。
- 供应商与模型配置是 `setup_required -> ready` 和真实聊天的直接前置，把它放到 WP-3-04 之后会迫使
  用户继续依赖 legacy Qt 或手工编辑配置。

适合开始的是“逐域纵向迁移”，不是把 `app/ui/tauri_settings.py`、旧 HostRpc 或巨型保存事务整体搬入
Runtime v2。Memory、Tools、MCP、插件、TTS、截图和主动互动仍必须等各自领域所有者进入对应 WP，
不能因为页面已存在就提前伪开放。

## 3. 迁移单位与完成定义

迁移单位是一个稳定的 feature key，例如 `providers.credentials`、`model.chat_slot`、
`voice.playback`，不是整页，也不是旧设置返回对象中的全部字段。

每个 feature 必须一次性交付：

1. 明确数据与运行态所有者，以及 legacy Qt/v2 共读时的 schema 和锁边界。
2. `get` 只返回该 feature 所需的公开 DTO；密钥、裸路径和私有插件数据不得进入通用 Snapshot。
3. `validate` 在任何落盘或运行态修改前完成，错误指向稳定的 feature/field。
4. `save` 逐域原子提交，保留未知字段；失败时旧文件和旧运行态仍有效。
5. 返回明确 change plan：立即生效、受控 Core restart、下次启动生效或不支持；不允许假装热更新。
6. WebView 只拥有草稿和未提交预览；Rust 只做权限、identity、窗口和调用协调；领域真相留在 Python
   Core 或已经批准的原生平台服务。
7. capability manifest 只开放已经完成上述闭环的 feature；未迁移控件继续禁用并显示稳定原因。
8. 自动测试、隔离目录故障注入、真实 Tauri 操作、重新打开一致性和独立回退全部通过。

一个页面可以包含多个状态不同的 feature。页面可进入不代表其中所有控件可用；禁止为方便前端而把
未迁移控件接成 no-op，也禁止一次“保存全部”跨越多个尚无共同事务所有者的配置域。

## 4. Capability manifest 演进

WP-3U-01 的 section 级 manifest 足以门控空壳，不能准确表达旧页面内逐控件迁移。WP-3S-01 应把契约
演进为 feature 级能力，同时保留对 v1 的失败安全兼容：

```json
{
  "schemaVersion": 2,
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
- v2 前端必须按 feature 控制单个输入和操作按钮。legacy 独立设置宿主若尚未提供 v2 manifest，继续
  使用其既有 HostRpc 能力，不得用“全部可用”的伪造 manifest 污染 Runtime v2 判定。
- schema 升级必须同时更新 canonical frontend freshness、secret scan 和窗口 generation 测试。

## 5. 交付顺序

| 设置切片 | 目标 WP | 开放条件与边界 |
|---|---|---|
| 当前角色只读、立绘缩放、字体、角色主题 | WP-3U-02 | 已有候选；其余角色/布局控件保持禁用 |
| 供应商管理、凭据、模型列表/连通性、模型槽选择 | **WP-3S-01** | WP-3U-02 accepted；先于 WP-3-04 |
| 真实聊天直接消费的气泡/输入/打字机交互设置 | WP-3-04 | 只迁移真实聊天 UI 已消费字段，不改变固定窗口包络 |
| 角色切换与会话/历史联动 | WP-5-03 | Session、历史、Memory/TTS scope 可安全重建后开放 |
| Memory 设置和记忆管理操作 | WP-4-01 | Memory 领域、外部存储和降级路径迁移时一并开放 |
| 内置 Tools 设置与副作用确认选项 | WP-4-02 | ToolRegistry、Action ID 和取消/超时真实可用后开放 |
| MCP 配置与运行状态 | WP-4-03 | MCP 进程归属当前 generation、凭据和退出门通过后开放 |
| 插件启停、插件设置与设置 action | WP-4-04 | 插件发现、私有数据、错误隔离和卸载清理迁移时开放 |
| TTS Provider、参考音频、播放选项与资源任务 | WP-4-05 | 合成/播放 backend、设备错误和受控子进程可用后开放 |
| 手动截图相关选项 | WP-4-06 | 平台权限、受控图像资源和多屏/DPI 门通过后开放 |
| 主动屏幕感知、主动互动、提醒与调度选项 | WP-4-07 | 截图依赖、时区/休眠和持久化语义完成后开放 |
| 剩余外观/布局与跨域配置一致性 | WP-5-01 | 对已迁移仓库做缺口收口；冲突旧控件需明确替代决定 |
| 首次设置编排、逐域结果和页面迁移关闭清单 | WP-5-02 | 只编排已 accepted 的切片，不在此重新造巨型后端 |
| 置顶、快捷键、开机启动等系统设置 | WP-5-04 | 对应原生平台服务拥有真实读写和撤销语义后开放 |
| 诊断、日志与 Repair 设置 | WP-5-06 | 诊断/修复所有者、权限和失败安全门完成后开放 |
| 角色导入导出、Studio 修改与发布 | WP-6-01 至 06-04 | Workspace/Draft、资源校验、原子发布和回滚完成后开放 |

没有列出的旧控件默认保持 `unavailable`。若固定桌宠产品语义已经使某个 legacy 控件不再适用，例如会
破坏固定窗口包络的自由布局参数，责任 WP 必须记录保留等价、约束后迁移或 `approved-replacement`，
不能静默删除，也不能为了表面等价破坏已验收的窗口语义。

## 6. WP-3S-01：供应商与模型设置纵向链

### 6.1 目标

在 canonical 旧设置页面中开放“供应商”和“模型”的真实配置能力，使缺少 Provider 的 Runtime v2
可以从 `setup_required` 进入可重试的配置路径，保存后按明确 change plan 受控重建 Core，并为
WP-3-04 提供可由用户维护的真实聊天配置。

### 6.2 允许能力

- 读取 Provider 公开信息、配置完成状态、可选模型目录和当前模型槽；读取响应不返回已保存密钥。
- 新增/编辑/删除旧页面已经支持的 Provider 配置，逐字段校验 base URL、Provider 类型和非敏感选项。
- 密钥输入采用“空白保持原值 + 显式清除动作”；`configured=true` 可以显示，密钥本体不能回显。
- 使用新输入或 Core 内已保存凭据执行有界 `list_models`/`test_connection`；错误必须脱敏，control/
  shutdown 不得被网络探测阻塞。
- 保存聊天/视觉等实际存在的模型槽；引用不存在 Provider/模型时拒绝整个相关域。
- 保存成功返回 `applied`、`core_restart_required` 或 `next_launch_required`，由现有 Supervisor 执行受控
  restart；设置前端不得自行杀进程或声称已经热更新。
- 当 readiness 为 Provider 缺失导致的 `setup_required` 时，可以聚焦设置窗口对应页面；这不是完整
  首次设置向导，也不开放 Studio。

### 6.3 数据与安全边界

- `data/config/api.yaml` 仍由 Python 配置领域拥有。WebView 不直接访问文件；Rust Gateway 注入
  window/Core generation、request identity 和 deadline，不成为配置真相源。
- 激活前必须更新 ADR-0003/WP-0-02 的 Phase 3 允许写集合：只批准当前 schema 的 Provider/模型字段，
  冻结 unknown-field preservation、未来 schema 只读和 Qt -> v2 -> Qt 夹具。未完成该文档与夹具门时
  不得写真实配置。
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

### 6.5 退出门

- Provider 新增/编辑/删除、密钥保持/替换/显式清除、模型槽引用和重新打开一致性通过。
- `list_models`、连通性成功/认证失败/超时/取消/关窗/Core crash 均为有界唯一终态且错误脱敏。
- 原子替换、权限、损坏 YAML、未来 schema、旧 generation、重复保存和 restart 失败不产生半更新。
- legacy Qt 创建配置 -> v2 读取/修改 -> legacy Qt 回读通过；未知字段和未修改 secret bytes 保持。
- Core 受控 restart 后使用新配置达到预期 readiness；旧 generation response/event 不改变设置 UI。
- Windows 真实 Tauri 完成中文 IME 密钥/URL/模型输入、模型列表、测试、应用/保存、失败恢复和重新打开；
  公共 Python/Rust/frontend 代码取得同一候选 SHA 的三平台门禁。
- P0/P1、credential 泄漏、进程/请求/临时文件残留和共享锁问题为零。

### 6.6 回退

禁用 `providers.*` 和 `model.*` feature，设置窗口恢复只读/未迁移提示，停止新的 Provider 网络探测；
回退 Gateway、Core Adapter 和前端接线，但不删除、恢复或重写用户现有 `api.yaml`。已经以兼容 schema
保存的数据继续由 legacy Qt 读取；若生产缺陷涉及写入安全，Runtime v2 对该域退回只读。

## 7. 后续 WP 的强制设置责任

从本规范生效后，任何拥有用户可配置能力的 WP 在激活记录中必须增加“设置切片”字段，明确：

- 本 WP 开放哪些 feature key；哪些旧控件仍禁用以及原因。
- get/validate/save/change plan 的所有者和失败原子性。
- secret、路径、插件私有数据和 generation identity 如何隔离。
- 对应真实页面、键盘/IME、应用/保存/取消、重新打开和运行态生效验收。
- 独立禁用与回退方式。

若一个能力 WP 不需要用户设置，也必须明确写“无设置切片”，避免把遗漏误认为有意延期。Phase 5 的
WP-5-01/02 只负责缺口收口、跨域一致性审计和首次设置编排，不得重新聚合已经分域完成的保存逻辑。
