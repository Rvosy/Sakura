# WP-0-02 用户数据与共享应用锁契约基线

> Phase / Work Package：Phase 0 / WP-0-02
>
> 基线日期：2026-07-15
>
> 工作分支：`refactor/tauri-runtime-v2`
>
> 前置：WP-0-01 `accepted`
>
> 关联 ADR：ADR-0003

## 1. 范围、证据边界与非目标

本 Work Package 冻结 Phase 1A 共享应用锁和 Phase 3 数据兼容门禁的输入，不创建或编译 Tauri 工程，不实现应用锁，不迁移、修复、清理或改写真实用户数据，不建设通用 schema migration 平台。

取证遵循以下边界：

- 真实路径、长度和文件类型来自 `D:\Project\sakura\data`、`characters` 的只读路径元数据。
- schema、版本、读写者和落盘方式来自当前分支源码与测试，不读取真实 API 配置、聊天、Memory、notes、插件私有内容或迁移备份内容。
- 真实 `data/` 前置清单记录相对路径、长度、UTC mtime 和 SHA-256；初始取证为 121 个文件、1,045,931,891 bytes，清单文件 SHA-256 为 `92fe3c38977a450c2b1ebe8c6009f2a22334d65e4ec8d2a83a9b9f8a7bc85682`。
- 所有写入、兼容写和故障注入只允许发生在 `temp/runtime-v2-wp-0-02/` 内的脱敏夹具副本。
- `tests/fixtures/runtime_v2/wp_0_02/` 只包含人为构造的 `REDACTED_FIXTURE` 占位数据；没有真实 API Key、Token、用户对话、Memory、notes、屏幕内容、角色图片、音频、模型或插件二进制。

明确非目标：

- 不修改 `main.py`、`app/`、`desktop/`、`plugins/` 生产代码和现有用户数据 schema。
- 不进入 WP-0-03、WP-0-04 或任何 Tauri Shell 实现。
- 不把测试 oracle 当成生产持久化库或迁移框架。
- 不决定 Phase 4 `audio.*` 的最终所有者。

## 2. 当前版本与兼容判定规则

当前共享数据只有一个可执行的全局版本锚点：

```text
data/config/system_config.yaml
└─ config_version: 4
```

源码真相源为 `app/config/migration_runner.py::CURRENT_CONFIG_VERSION = 4`。其他大多数共享文件没有独立 schema version，属于“隐式 legacy schema 0”；不能因能被宽松 JSON/YAML parser 打开，就假定未来格式可安全写回。

Runtime v2 Phase 1–3 的安全判定冻结为：

| 条件 | v2 行为 |
|---|---|
| `config_version == 4`，所有将要读取/写入的目标均通过结构校验 | 允许执行矩阵中明确批准的兼容写；Phase 3 当前只批准聊天历史兼容追加 |
| `config_version < 4` 或缺失 | 可以显示 diagnostics，但不得由 v2 静默执行 legacy migration 或共享写；提示先用同版本 legacy Qt 完成迁移并重新验收 |
| `config_version > 4` | `diagnostics_read_only`；不得尝试降级、覆盖或“修复”未来数据 |
| 版本字段类型无效、YAML/JSON/JSONL 损坏、必要资源缺失 | `diagnostics_read_only`；保留原数据，不写默认值覆盖 |
| v2 专属文件的 `schema_version` 高于本地支持值 | 只禁用或只读该 v2 域；不得回写共享 legacy 数据 |

这是一条保守门禁：Phase 1–3 不运行 `MigrationRunner`。未来若要放宽，必须独立更新 ADR、夹具和双向门禁。

## 3. 共享数据真实路径、格式与读写契约

以下路径均相对于仓库/便携应用根 `D:\Project\sakura`。分类表示 Runtime v2 在 Phase 1–3 的权限，不改变 legacy Qt 当前能力。

### 3.1 角色、配置与插件启停

| 数据 | 真实路径与格式 / 版本 | 当前写入者 | 当前读取者 | 当前落盘方式 | Phase 1–3 分类与结论 |
|---|---|---|---|---|---|
| 当前角色选择 | `data/config/characters.yaml`；UTF-8 YAML mapping；无文件级 version，受全局 `config_version=4` 约束 | `AppSettingsService.save_current_character_id`；v0→v1 migration | `AppSettingsService`、启动/设置链 | `save_yaml_mapping`：同目录临时文件、flush+fsync、`os.replace`，滚动 `.bak` 为 best-effort | **只读复用**。v2 可读取 current id；Phase 1–3 不写角色选择 |
| 已安装角色包 | `characters/<id>/character.json`、`card.md`、`portraits/**`、可选 `voice/**`、backchannel、renderer 资源；installed manifest 无 version | Character Studio、角色/语音 archive import、主题写回、用户手工资源管理 | `CharacterRegistry`、Prompt、portrait renderer、TTS、Studio | 混合：archive import 使用 staging directory+rename；Studio publish 使用 backup/copy/move；`card.md`、部分 theme/reference 写回仍直接覆盖 | **只读复用**。Phase 1–3 v2 只读；Rust/Tauri 不改 manifest 或任何资源二进制 |
| 角色/语音归档 | 用户选择的 `.char`/ZIP；`manifest.json.format=sakura.character.archive` 或 `sakura.character.voice`，`version=1` | `export_character_archive`、`export_character_voice_archive` | archive import/Studio | ZIP 临时输出+替换；导入到 staging 后校验并 rename | **禁止修改**。不是 Phase 1–3 共享写目标；导入/发布留到 Phase 6 |
| API / Core 配置 | `data/config/api.yaml`；UTF-8 YAML mapping；无独立 schema；包含 Provider、模型槽、TTS 和凭据 | `AppSettingsService`、旧 `.env` migration、现有 Tauri Settings 通过 Python service | bootstrap、Provider、Memory、TTS、Settings | `save_yaml_mapping` 原子替换；`.bak` 失败会记录后继续保存，因此不是强制迁移备份 | **WP-3S-01 窄兼容写**。仅在全局 schema 4 下由 Python 配置域原子写 Provider、聊天/视觉模型槽和已批准生成参数；保留未知字段、非目标域与未修改 secret bytes；密钥不得进入 manifest/Snapshot/event/response echo/普通日志 |
| system 配置 | `data/config/system_config.yaml`；UTF-8 YAML；顶层 `config_version=4`、`app_version`，另含 debug/startup/MCP runtime/Memory/legacy Qt UI 等 section | `MigrationRunner`、`record_app_version`、`AppSettingsService`、legacy Qt UI | 启动、自检、runtime log、设置、主动互动等 | `save_yaml_mapping` 原子替换+best-effort `.bak`；migration backup 是另外的强制前置步骤 | **只读复用**。现有 `ui` section 继续归 legacy Qt；v2 的 `desktop.*`、`ui.*` 不得写入本文件 |
| MCP 定义 | `data/config/mcp.yaml`；UTF-8 YAML mapping；无 version | `ensure_default_configs` 创建/补齐；用户/Settings 配置 | `load_mcp_config`、MCP Provider、Settings | 首次创建和 backfill 使用 `atomic_write_text`；用户手工编辑不受控 | **只读复用**。Phase 1–3 不改 MCP 配置；MCP 本身在 Phase 3 禁止接入 |
| 插件启停覆盖 | `data/config/plugins.yaml`；UTF-8 YAML list；无 version | `ensure_default_configs`、`save_plugin_enabled_overrides`、用户编辑 | `PluginDiscovery`、Settings | 默认创建原子；当前启停保存为 `Path.write_text` 直接覆盖，无备份/原子替换 | **只读复用**。Phase 1–3 不写；在原子保存修复和兼容测试前不得批准 v2 写入 |

### 3.2 历史、Memory、提醒、待办和 notes

| 数据 | 真实路径与格式 / 版本 | 当前写入者 | 当前读取者 | 当前落盘方式 | Phase 1–3 分类与结论 |
|---|---|---|---|---|---|
| 当前聊天历史 | `data/chat_history/<sanitize(character_id)>.jsonl`；每行 UTF-8 JSON object；必需 `created_at/role/content`，可选 `translation/tone/portrait/_debug`；隐式 schema 0 | `ChatHistoryStore.append/clear`、PetWindow；migration 合并 | HistoryWindow、Chat pipeline、history digest、Memory curator | append 为直接追加；32 MiB 后 rename archive；clear/合并用原子替换；损坏尾行会 copy2 到 `.corrupt-*.bak` 后 truncate | **Qt/Tauri 兼容写入**，是 Phase 3 唯一批准的共享业务写。必须复用 Python `ChatHistoryStore` 字段和文件名映射；新字段只能可选且 Qt 忽略；单应用锁是追加安全前提 |
| 历史兼容工件 | `data/chat_history.jsonl`、`.migrated`、`<character>.jsonl.*.archive`、`.corrupt-*.bak`、尾点 ID 变体 | legacy migration、ChatHistoryStore rotation/repair | migration、ChatHistoryStore archive reader | rename/copy/truncate/原子合并 | **禁止修改**。v2 不重跑 legacy migration、不删除、不合并、不重命名这些工件 |
| Memory 常驻档案 | `data/memory/core_profiles.json`；UTF-8 JSON mapping；隐式 schema 0 | `MemoryStore.upsert/delete_core_profile` | `MemoryStore`、Memory context/curator | `atomic_write_text`，无 backup | **只读复用**。Phase 1–3 不接入 Memory，不写；后续只能由 Python MemoryStore 兼容写 |
| Memory 外部存储 | `data/memory/qdrant/**`、`data/memory/mem0_history.db`、`data/memory/qdrant/.lock`；Qdrant/SQLite 外部库格式，无 Sakura schema version；vendored mem0 标识 `2.0.4-vendored` | mem0/Qdrant client、Memory curator/tools | `MemoryStore`/mem0 | 外部库事务、SQLite transaction、Qdrant 自有文件锁；不是 Sakura atomic text writer | **禁止修改**（Rust/Tauri）。Phase 1–3 不打开；后续仅当前持锁桌面根授权的 Python MemoryStore 可访问。不得复制解析、schema migration、手工删 `.lock` |
| legacy `memory.json` | `data/memory.json`；当前路径仍保留，但当前 MemoryStore 不作为主存储读取；格式/所有权不完整 | 未确认；历史版本 | 当前仅兼容构造路径识别 | 未确认 | **未知或需要后续验证**。完整保留且不写；Phase 3 门禁只比较 hash/mtime |
| Memory 整理状态 | `data/memory_curation_state.json`；JSON object：`processed_history_count/pending_turns/backfill_completed`；无 version | `MemoryCurationState`、PetWindow | 同上 | `atomic_write_text`，无 backup | **只读复用**。Phase 1–3 不写；不得在 v2 聊天后推进游标 |
| screen awareness 状态 | `data/screen_awareness_state.json`；JSON mapping；无 version | legacy PetWindow | legacy PetWindow | `atomic_write_text`，无 backup | **只读复用**。Phase 1–3 禁止主动观察，不写 |
| reminders | `data/reminders.json`；JSON object `{reminders:[...]}`；无 version | `ReminderStore`、PetWindow due/completion | built-in tools、PetWindow | whole-file `atomic_write_text`，无 backup | **只读复用**。Phase 1–3 不写；后续兼容写必须通过本 WP 的强制备份/失败矩阵或保持现有原子格式 |
| tasks | `data/tasks.json`；JSON object `{tasks:[...]}`；无 version | `TodoStore` | built-in tools | whole-file `atomic_write_text`，无 backup | **只读复用**。Phase 1–3 不写 |
| notes | `data/notes/<name>.txt`；UTF-8 text；无 version | `NotesStore.write_note`、用户 | `NotesStore.read_note`、用户 | 直接 `Path.write_text` 覆盖，无 backup/atomic replace | **只读复用**。v2 不写；在落盘语义改造和单独批准前禁止兼容写 |

### 3.3 运行事件、视觉观察、插件数据和用户资源

| 数据 | 真实路径与格式 / 版本 | 当前写入者 | 当前读取者 | 当前落盘方式 | Phase 1–3 分类与结论 |
|---|---|---|---|---|---|
| runtime events | `data/runtime_events/<character>.jsonl`，轮转 `.1`–`.8`；JSONL `event_type/timestamp/source/metadata/priority`；无 version | `RuntimeEventLog`、PetWindow | startup carryover、Agent runtime | 直接 append；8 MiB 后 rename 轮转；失败静默 | **只读复用**。Phase 1–3 v2 不写 legacy runtime event log；Shell 日志/状态走 v2 专属目录 |
| visual observations | `data/visual_observations/<character>.jsonl`；短期文本摘要，不应含原图；无 version | `VisualObservationStore`、Chat/Event worker | Chat pipeline、PetWindow | 读取全量、脱敏/裁剪后 whole-file 原子替换 | **只读复用**。Phase 4 前不写、不采集屏幕；任何 `data_url/image_url` 都不进入夹具或持久化 |
| 插件私有数据 | `data/plugins/<sanitize(plugin_id)>/**`；常见 `config.json`，其余任意插件自定义；无统一 version | `PluginContext.save_config` 和插件自身 | 对应插件 | 当前公共 `save_config` 为直接覆盖；其他文件由插件任意决定 | **未知或需要后续验证**。Phase 1–3 插件未接入，整个目录 hash 不变；未来逐插件准入，Rust/Tauri 不直接解释 |
| 插件安装资源 | `plugins/<id>/plugin.yaml`、代码和默认 `config.json` | 发布/开发者 | PluginDiscovery/PluginManager | 应用资源，不是运行时用户写入目标 | **禁止修改**。WP-0-02 只读取证；不修改第三方/插件实现 |
| Character Studio workspace | `data/character_studio/drafts/<id>/draft.json` 与包副本、`backups/**`；draft `version=1` | `CharacterStudioService`、现有 Tauri Studio 通过 Python RPC | Studio | state 原子写；资源有 copy/staging/backup/publish 混合事务 | **禁止修改**。Phase 6 前不读写，不用作运行中角色真相源 |
| TTS bundles / cache | `data/tts_bundles/{installed,downloads,onnx,...}`、`data/cache/tts/**`；大量外部二进制，migration state 可见 `.sakura_migration.json` | TTS installer/migrator/synthesis | TTS factory/playback/Settings | 目录 staging/rename、下载 partial、外部安装脚本，格式按 bundle 实现 | **禁止修改**。Phase 1–3 不启动/迁移；Phase 4 单独确认 `audio.*` 和资源所有权 |
| logs / diagnostics | `data/logs/**`、`data/diagnostics/**`；日志、JSON report、WAV 等，无统一 schema | runtime logger、crash diagnostics、TTS/MCP/插件子进程 | diagnostics UI/人工排障 | append/rotate/外部进程写入，非统一事务 | **未知或需要后续验证**。Phase 1A 冲突失败不得写这里；Phase 1C 前明确 v2 日志位置和脱敏，不能把普通日志当共享业务数据 |

### 3.4 migration backup、滚动备份和锁文件

| 数据 | 真实路径与格式 / 版本 | 当前写入者 / 读取者 | 当前落盘方式 | Phase 1–3 分类与结论 |
|---|---|---|---|---|
| migration backup | `data/migration_backup/<YYYYmmdd-HHMMSS>_<step>/<源相对路径>`；可包含 YAML、JSONL、历史 `.env` 等 | `MigrationContext.backup_file` 写；人工恢复/诊断读 | `shutil.copy2`，先备份后 apply；备份失败会使该 migration step 失败 | **禁止修改**。v2 不创建、不清理、不恢复；真实内容可能含凭据，永不进入 Git/普通日志 |
| 配置滚动 `.bak` | `data/config/*.bak` | `atomic_write_text(backup=True)` 写；人工/诊断读 | 直接复制旧 bytes 到固定 `.bak`；失败只记日志并继续保存 | **禁止修改**。它是 best-effort 上一版本，不满足 ADR 的强制 migration backup；不能作为唯一恢复证据 |
| current Qt lock artifact | `data/sakura.lock`；QLockFile PID/host/app metadata | `SingleInstanceGuard` | QLockFile；stale 判定由 Qt 实现 | **禁止修改**。WP-1A-04 切换到共用 Windows named mutex 后，本文件不再是权威锁；不得自动删除历史残留 |
| Qdrant lock | `data/memory/qdrant/.lock` | Qdrant | 外部文件锁 | **禁止修改**。不是桌面应用锁，不得作为双入口互斥依据 |

## 4. Runtime v2 专属配置命名空间

冻结独立位置：

```text
data/runtime_v2/
├─ config/
│  ├─ desktop.json
│  └─ ui.json
├─ state/
│  └─ shell.json
├─ logs/          # Phase 1C 前再确认具体日志契约
└─ diagnostics/   # Phase 1D 使用；不得包含密钥/Prompt/私密配置
```

三个首批文件使用 UTF-8 JSON object：

```json
{
  "schema_version": 1,
  "domain": "desktop | ui | shell",
  "settings_or_state": {}
}
```

契约：

- `desktop.json` 只存窗口位置/锚点、置顶、启动行为、托盘、快捷键等 Tauri/Rust 所有数据。
- `ui.json` 只存 Runtime v2 主题、字体、气泡尺寸、打字机速度和布局。
- `shell.json` 只存可安全丢失的 Shell 表现状态；不得存未完成业务 Operation 或 Python 领域真相。
- 现有 `system_config.yaml.ui` 是 legacy Qt 数据。v2 可以在明确的一次性只读映射中参考旧值，但不得新增、覆盖或删除其中字段。
- Qt 不扫描 `data/runtime_v2/`，升级包也不得覆盖 `data/`；因此 legacy Qt 会忽略 v2 专属配置。
- 每个 v2 文件独立 validate 和同目录 temp+flush+fsync+atomic replace；保存失败保留旧文件，不跨域回滚。
- `schema_version > 1` 时只将该 v2 域置为 defaults/diagnostics，不触碰共享 legacy 数据。
- `audio.*` 不进入上述文件，等 Phase 4 audio ADR 决定。

夹具已提供：

- `tests/fixtures/runtime_v2/wp_0_02/dataset/data/runtime_v2/config/desktop.json`
- `tests/fixtures/runtime_v2/wp_0_02/dataset/data/runtime_v2/config/ui.json`
- `tests/fixtures/runtime_v2/wp_0_02/dataset/data/runtime_v2/state/shell.json`

这些只是契约样本，不是 Tauri 工程或生产实现。

## 5. Qt / Tauri 共用应用锁契约

### 5.1 稳定 identity 与系统位置

Windows 首轮冻结为命名 mutex：

```text
semantic identity: sakura.desktop.shared-user-data.v1
Windows object name: Local\SakuraDesktop.SharedUserData.v1
scope: 当前 Windows 登录会话（Local namespace）
```

identity 不包含：可执行文件名、安装路径、版本号、分支名、Qt/Tauri 入口名、PID 或角色 ID。Qt 与 Tauri 必须使用完全相同的 UTF-16 object name。

选择 named mutex 而不是普通 lock file 的原因：

- Windows 在最后一个句柄关闭或进程崩溃后自动释放内核对象。
- 不需要根据 PID 猜 stale，不会把残留标志文件误判为活动实例。
- Qt/Python 和 Rust 都能调用同一 Win32 primitive。
- `Local\` 精确匹配“同一用户登录会话只有一个桌面根”；不扩大到其他 Windows session。

默认 DACL 必须保持当前用户可访问，不授予无关用户写权限。`ERROR_ACCESS_DENIED` 等创建/打开错误属于 lock failure，不得伪装成“已有实例”。

### 5.2 获取、持有和释放时机

```text
进程入口
-> 获取 named mutex
-> 成功后才允许创建/探测/写入 data/、日志、配置和启动 Core
-> 持有到所有窗口、Core、MCP/插件/TTS/浏览器后代和写入任务结束
-> 最后 flush/close
-> 释放 mutex
-> 进程退出
```

强制规则：

1. 锁获取必须早于当前 Qt 的 crash log 准备、自检写入探针、默认配置、版本记录、migration、登录启动一致性写入和 Core/服务启动。当前 `main.py` 在 QLockFile 前已有若干 `data/` 动作，这是 WP-1A-04 必须关闭的实现差距。
2. Tauri 必须在创建 Python Core 之前持锁。Core 子进程不另行竞争桌面锁；它只能在当前持锁桌面根授权下写共享数据。
3. 锁持有时间覆盖整个桌面生命周期，不能在主窗口隐藏、Core restart 或打开 Settings/Studio 时释放。
4. 正常退出：先禁止新写入，停止/等待所有写入者和后代进程，关闭文件/数据库句柄，最后关闭 mutex handle。
5. 崩溃/强杀：操作系统回收 handle；下次启动直接重新获取，不依赖删除任何文件。
6. `data/sakura.lock` 和 `data/memory/qdrant/.lock` 都不是新契约的权威判断源；不得因它们存在而拒绝启动或自动删除。

### 5.3 冲突结果与提示

活动实例已持锁时，失败入口必须：

- 不启动 Python Core，不运行 migration，不打开 Qdrant，不创建默认配置，不写共享日志/diagnostics/data。
- 显示标题 `Sakura 已在运行`。
- 显示正文 `另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。`
- 只提供确认/退出动作，不提供“强制接管”“删除锁文件”或并发只读启动。
- 作为预期冲突退出，进程退出码为 0；自动测试另外断言结构化结果 `already_running`。

mutex API 本身失败且无法判断是否已有实例时，进入 fatal diagnostics，返回非零退出码；不得继续启动为第二写入者。

### 5.4 不同时写用户数据的保证

该保证由四层共同成立：

1. 两个桌面入口竞争同一 named mutex。
2. 未持锁入口在任何 `data/` mutation 之前退出。
3. Python Core 只由持锁 Tauri 根进程启动，退出时在释放锁前完成回收和写入 drain。
4. Phase 3 自动测试双向覆盖：Qt 持锁→Tauri 失败；Tauri 持锁→Qt 失败；崩溃释放后另一入口成功。

仅保留 `SingleInstanceGuard`、仅检查 `data/sakura.lock`、或只在开始聊天前检查锁，都不满足本契约。

## 6. 脱敏代表夹具

机器可读清单位于 `tests/fixtures/runtime_v2/wp_0_02/FIXTURE-MANIFEST.json`。每类数据至少一个样本；不能安全提交的二进制或私密格式有明确缺失原因。

| 分类 | 代表夹具 | 缺失说明 |
|---|---|---|
| 角色配置/资源 | `characters/fixture/character.json`、`card.md`、文本 portrait placeholder、`characters.yaml` | 不提交真实 PNG、音频、模型；相对路径和必需文件契约仍可执行 |
| API/Core | `data/config/api.yaml` | endpoint 为 `fixture.invalid`，凭据值为 `REDACTED_FIXTURE_VALUE` |
| system/MCP/plugins | `system_config.yaml`、`mcp.yaml`、`plugins.yaml` | 均为最小无外部调用配置 |
| 聊天历史 | `data/chat_history/fixture.jsonl` | 内容全部为 `REDACTED_FIXTURE` 占位符 |
| Memory/整理状态 | `memory.json`、`core_profiles.json`、`memory_curation_state.json`、`screen_awareness_state.json` | Qdrant/SQLite 不提交，原因写入 `EXTERNAL-STORES.txt` |
| reminders/tasks/notes | 三个对应文件 | 无真实提醒、任务或笔记 |
| runtime events/visual | 两个 JSONL | 不含真实屏幕文字或图像数据 |
| 插件数据/用户资源 | 插件 `config.json`、Studio draft、TTS 缺失说明 | 任意插件/TTS 二进制不提交 |
| migration/兼容工件 | v3 system backup、legacy migrated history、secret 缺失说明 | `.env`、凭据和真实备份禁止提交 |
| v2 专属 | `desktop.json`、`ui.json`、`shell.json` | 提议 schema 1，仅用于契约测试 |

验收脚本会扫描常见 API Key、Bearer token、私钥和非占位 `api_key`，并拒绝二进制文件进入该夹具。

## 7. Qt → Tauri v2 → Qt 双向兼容门禁

### 7.1 Phase 3 真实验收顺序

Phase 3 必须在独立源码树和脱敏/专用测试数据根执行：

```text
1. 对真实仓库 data/ 建立 before 清单；后续所有应用写入指向隔离根。
2. legacy Qt 获取 Local\SakuraDesktop.SharedUserData.v1。
3. Qt 加载 fixture 角色/config/history/Memory 元数据，创建一条脱敏聊天历史记录并退出。
4. 确认 Qt 的 Core/线程/插件/MCP/写入任务结束，mutex 已释放。
5. Tauri v2 获取同一 mutex；读取同一角色和 Core 配置。
6. 使用 deterministic fake/local Provider 完成基础聊天；只向同一 character JSONL 追加当前 Qt 可读字段，并写 v2 私有 desktop/ui/shell 配置。
7. Tauri 停止新请求，等待 history 写入完成，关闭 Core/后代进程，最后释放 mutex。
8. legacy Qt 重新获取 mutex，真实启动并读取相同角色/config/history；新记录在 HistoryWindow 可见。
9. 对 Memory/Qdrant、reminders/tasks/notes、runtime events、visual observations、插件数据、Studio、TTS、migration backups 比较 before/after；未批准的数据必须零变化。
10. 对真实仓库 data/ 建立 after 清单并与第 1 步逐项一致。
```

真实门禁不能由静态源码检查或当前 Python oracle代替；本 WP 只冻结 fixture、预期和失败判定。

### 7.2 失败矩阵

| 场景 | 注入点 | 必须观察到的结果 | 禁止结果 |
|---|---|---|---|
| 正常读取/兼容写入 | Qt 当前 schema 4 fixture；Tauri 追加一行带 Qt 可忽略 optional field 的 JSONL | Tauri 可读；退出后 Qt 可读原记录和新增记录；v2 私有配置不改变 Qt | 重写角色/config、修改 Memory、丢历史、出现两个 writer |
| 强制备份失败 | whole-file 兼容写在创建/校验 migration-grade backup 前失败 | 原文件 path/length/hash/mtime 不变；无 replace；进入 diagnostics/read-only 或返回明确保存失败 | 使用 `atomic_write_text` 的 best-effort `.bak` 语义继续危险写入 |
| 临时文件写入失败 | 同目录 temp 创建/写入/fsync 失败 | 原文件完整；已验证 backup 可保留；temp 清理或标记；写入失败 | 截断 target、写半个 JSON/YAML |
| 原子替换失败 | temp 已完整解析，`os.replace`/平台 replace 失败 | 原文件仍为旧完整版本；backup 完整；temp 清理；不更新运行时状态 | 把未提交配置应用到运行中 Core/UI |
| 异常中断 | temp fsync 后、replace 前强杀；另测 replace 后强杀 | 重启时读取旧完整 target 或新完整 target；孤儿 temp 不作为权威文件；mutex 自动释放 | 按 temp 文件猜测完成、自动删除用户 backup、双 writer |
| 损坏文件 | JSON/YAML/JSONL 中间损坏或必要资源缺失 | diagnostics/read-only；明确指出数据类和路径；不写默认值覆盖 | 把损坏视为空数据并保存、静默迁移 |
| 不支持的未来 version | `config_version > 4` 或 v2 private `schema_version > 1` | 共享数据整体禁止写；未来 legacy 数据不降级；v2 private 域使用 defaults/diagnostics | 将未来字段丢弃后覆盖原文件 |
| 只读/diagnostics 安全状态 | 上述任一危险条件 | Shell 可显示并退出；不启动会写共享数据的 Core 功能；允许用户打开日志/说明 | 空白窗口、无限重启、自动 repair/migration |
| 双入口冲突 | Qt 持锁启动 Tauri；Tauri 持锁启动 Qt | 只有持锁入口继续；失败入口提示并以 `already_running` 结束；共享 data 零变化 | 第二 Core、第二日志/配置写入者、强制接管 |
| stale / crash release | 强杀持锁进程 | Windows 自动释放 mutex；另一入口随后成功；历史 `data/sakura.lock` 不影响结果 | 要求用户删锁文件、误删 Qdrant lock |

### 7.3 当前可执行 oracle

`docs/runtime-v2/baselines/wp_0_02_contract.py` 固定执行以下脱敏场景：

- normal Qt-parser → Tauri-compatible append → Qt-parser。
- mandatory backup failure。
- temporary write failure。
- atomic replace failure。
- interruption after temp fsync/before replace。
- corrupt file → `diagnostics_read_only` and write blocked。
- future schema → `diagnostics_read_only` and write blocked。
- fixture source tree before/after SHA-256 manifest identical。

reference whole-file writer 只接受 JSON fixture，用来表达“强制 backup + temp validate + atomic replace”的验收结果，不允许被生产代码 import。

## 8. 重复执行与数据零变化证明

唯一推荐命令：

```powershell
cd D:\Project\sakura
.\docs\runtime-v2\baselines\run_wp_0_02_baseline.ps1
```

脚本会：

1. 对真实 `data/` 记录相对路径、长度、UTC mtime、SHA-256。
2. 在唯一 `temp/runtime-v2-wp-0-02/<run-id>/` 运行兼容 oracle。
3. 用 `runtime/python.exe` 运行 `tests/unit/test_wp_0_02_data_contract.py` 和唯一 pytest basetemp。
4. 重新计算真实 `data/` 清单并逐项比较。
5. 生成 `data-before.json`、`data-after.json`、`contract/report.json`、`summary.json`；任何场景、测试或 data 差异都返回非零。

最终 accepted 记录必须填入实际结果目录、pytest 数量、场景数、清单摘要和 `DATA_UNCHANGED=True`。证据目录在 `temp/`，不提交 Git。

### 8.1 稳定化实际结果

完整脚本连续执行三次，均通过：

```text
run 1: temp/runtime-v2-wp-0-02/wp-0-02-20260715-234628-0ebe3ec5
run 2: temp/runtime-v2-wp-0-02/wp-0-02-20260715-234950-ada02892
run 3: temp/runtime-v2-wp-0-02/wp-0-02-20260715-235106-8a3b418c
```

三次结果一致：

| 项目 | 结果 |
|---|---|
| 脱敏 fixture 文件 | 30 |
| fixture tree SHA-256 | `6c7b34e2f6af7dfce4d0a69a756499e552fea87943902782d383ef6df78ea8ff` |
| contract 场景 | 7/7 passed |
| 场景 | 正常双向 parser、backup failure、temp failure、replace failure、异常中断、损坏文件、未来 schema |
| 定向 pytest | `4 passed`；最终轮 1.27s |
| 真实 `data/` 文件 | 121 |
| 真实 `data/` canonical manifest SHA-256 before/after | `63d79065372c9943e9de12065dcf6df14eef14447fe2bc56fd43587e533ee6cf` / 相同 |
| 真实 `data/` path/length/UTC mtime/SHA-256 | 完全一致，`DATA_UNCHANGED=True` |
| P0/P1 / 数据污染 / 范围扩张 | 未确认 |

前置取证的 `92fe...` 是首次 JSONL 清单文件自身的 hash；稳定化脚本使用 canonical compact JSON manifest，最终门禁以 `63d790...` 为准。两者序列化方式不同，不表示真实数据发生变化。

## 9. 已知限制与后续门禁输入

1. 当前 Qt 的权威锁仍是 `QLockFile(data/sakura.lock)`，且若干 `data/` 动作发生在锁前。WP-1A-04 必须同时修改 Qt/Tauri 的入口顺序并真实验证 named mutex；本 WP 不实现。
2. installed `character.json`、API/MCP/plugins/history/Memory state 等多数格式无独立 schema version，只能以 `config_version=4` 作为全局兼容 epoch。
3. 现有 `.bak` 是 best-effort；不能满足 ADR migration-grade backup failure 门禁。
4. chat/runtime events 使用直接 append；原子性依赖单 writer、单行写入和 reader 容错，不等价于 whole-file atomic replace。
5. notes、插件 config、plugins.yaml 和部分角色写回仍直接覆盖。Phase 1–3 不批准 v2 写入，因此不是本 WP P0/P1；若未来要共享写，必须先拆独立修复/验证范围。
6. Qdrant、mem0 SQLite、TTS、插件私有数据和 logs/diagnostics 没有统一 Sakura schema；Rust/Tauri 不能直接迁移或修复。
7. WP-0-02 的 Python oracle不是实际 Tauri、真实 WebView 或双进程锁测试；ADR-0003 只能在 Phase 1A/Phase 3 对应真实门禁后升级状态。

若后续实现要求修改上述禁止目录、放宽 `config_version` 判定、在 Phase 3 写入聊天历史以外的共享数据，或需要自动 repair/migration，必须停止当前 Work Package/后续实现并更新允许列表、ADR 和夹具，不得绕过。

## 10. 独立回退

本 Work Package 只新增文档、脱敏夹具和测试 oracle，并更新 ADR/状态记录。回退：

```powershell
git revert <WP-0-02-commit>
```

回退不得删除 `temp/` 之外的用户文件，不得恢复或改写真实 `data/`、`characters/`、插件数据、Memory/Qdrant、migration backup 或既有 lock artifact。
