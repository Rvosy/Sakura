# ADR-0003：Runtime v2 用户数据兼容与 legacy Qt 回退

> 状态：Technically Validated（Windows shared lock）；POSIX backends 与跨平台兼容门禁待 Phase 1P/Phase 3 验证
> 日期：2026-07-15
> 适用范围：Runtime v2 与 legacy Qt 入口共享的角色、配置、历史、Memory、工具数据、插件数据和用户资源
> Phase 0 基线：`docs/runtime-v2/baselines/WP-0-02-data-lock-baseline.md`

## 背景

Runtime v2 在开发分支中提前切换默认 Tauri 入口，同时保留 legacy Qt 作为显式回退。保留源码本身不能保证回退可用：如果 v2 修改共享数据格式、迁移失败或两个入口同时写入，Qt 可能已经无法读取用户数据。

本 ADR 定义首轮迁移期间的数据安全底线。目标不是建设通用迁移平台，而是保证逐步替换桌面层时用户数据不被新入口破坏。

Phase 0 取证确认：当前数据集的全局版本锚点是 `data/config/system_config.yaml.config_version`，当前值为 `4`；installed character、API、MCP、plugins、history、Memory state、reminders/tasks/notes、runtime events 和 visual observations 多数没有独立 schema version。详细路径、读写者、原子写入方式、脱敏夹具和缺失样本原因以 WP-0-02 基线为准。

WP-1A-04 已将 legacy Qt 的权威显式回退命令冻结为 `.\runtime\python.exe .\legacy_qt_main.py`，并提供 `start-legacy-qt.bat` 作为便利入口。回退前必须先退出 Tauri；用户不负责删除锁文件、判断 stale PID 或合并共享数据。能否启动和写入由两个入口共同实现的 named mutex、schema 安全状态和当前桌面生命周期根负责。

## 不可妥协的约束

- Phase 1–3 不执行破坏性用户数据迁移，也不由 v2 静默运行 legacy `MigrationRunner`。
- 现有角色资源、Core 配置、历史、Memory 和用户目录优先原样复用。
- legacy Qt 仍受支持期间，共享数据写入必须保持 Qt 可读且不会破坏旧入口启动。
- v2 专属桌面/UI/Shell 配置使用独立文件和独立命名空间。
- Tauri 和 legacy Qt 使用同一应用锁，同一用户会话中两个桌面入口不能同时运行，也不能出现两个共享数据写入者。
- schema migration 必须有版本、强制迁移前备份、同目录临时目标、解析/校验、原子切换、失败恢复和明确回退策略。
- 现有配置滚动 `.bak` 是 best-effort；备份失败仍继续普通保存，不能替代 migration-grade mandatory backup。
- 迁移或兼容验证失败时，保持原数据并阻止危险写入，不能带病继续。
- Rust/Tauri 不直接解析、修复或写入 Qdrant、mem0 SQLite、插件任意私有格式、TTS bundle 或角色资源二进制。

## 数据分类与 Phase 1–3 权限

```text
只读复用
├─ characters/<id> installed package 与 data/config/characters.yaml
├─ api.yaml / system_config.yaml / mcp.yaml / plugins.yaml
├─ memory/core_profiles.json、memory_curation_state.json、screen_awareness_state.json
├─ reminders.json、tasks.json、notes/
├─ runtime_events/、visual_observations/
└─ Phase 1–3 未批准写入的其他 legacy 用户数据

Qt/Tauri 兼容写入
└─ Phase 3 基础聊天的 data/chat_history/<character>.jsonl 追加

Runtime v2 专属
└─ data/runtime_v2/{config,state,logs,diagnostics}/

禁止修改
├─ data/migration_backup/
├─ config/*.bak、history archive/corrupt/migrated 工件
├─ data/memory/qdrant/**、mem0_history.db 和 Qdrant .lock（Rust/Tauri）
├─ data/character_studio/**、data/tts_bundles/**、角色/插件安装资源
└─ current data/sakura.lock 历史工件（新锁切换后不再是权威）

未知或需要后续验证
├─ legacy data/memory.json
├─ data/plugins/<id>/** 任意插件私有格式
└─ legacy logs/diagnostics/cache 的 v2 所有权和兼容范围
```

“只读复用”是 Runtime v2 Phase 1–3 权限，不禁止 legacy Qt 在持锁时执行其既有业务。“禁止修改”表示 v2 Shell/Rust/Core Host 不得直接改变；后续领域 Work Package 若需要写入，必须独立更新 ADR/门禁。

## 版本与安全状态

Runtime v2 以 `system_config.yaml.config_version` 作为整个 legacy 数据集的兼容 epoch：

| 条件 | 结果 |
|---|---|
| `config_version == 4` 且目标结构校验通过 | 允许执行明确列入兼容矩阵的写入；Phase 3 仅聊天历史追加 |
| `< 4` 或缺失 | diagnostics/read-only；提示先用同版本 legacy Qt 完成迁移；v2 不推进版本 |
| `> 4` | diagnostics/read-only；不降级、不覆盖未来格式 |
| 类型无效、文件损坏、必要资源缺失 | diagnostics/read-only；不以默认空数据覆盖 |

当前隐式无版本文件不能单独证明未来兼容。停止支持 legacy Qt 或首次引入不兼容共享 schema 时，必须用新 ADR supersede 本文，并同时更新版本锚点、迁移和双向 fixtures。

## Runtime v2 专属命名空间

冻结位置：

```text
data/runtime_v2/
├─ config/desktop.json
├─ config/ui.json
├─ state/shell.json
├─ logs/
└─ diagnostics/
```

首批 JSON 顶层必须包含：

```json
{
  "schema_version": 1,
  "domain": "desktop | ui | shell"
}
```

- `desktop.json` 由 Tauri/Rust 拥有窗口位置/锚点、置顶、启动行为、托盘和快捷键。
- `ui.json` 由 Tauri/Rust UI config repository 拥有 v2 主题、字体、气泡、打字机和布局。
- `shell.json` 只保存可丢失的表现状态，不保存 Python 领域真相或未完成 Operation。
- 现有 `system_config.yaml.ui` 继续归 legacy Qt。v2 可以做明确的只读初始映射，但不得新增、覆盖或删除其中字段。
- 每个 v2 域独立 validate 和同目录 temp+fsync+atomic replace；失败保留旧文件，不建设跨 Rust/Python 分布式事务。
- v2 私有未来 schema 只影响对应 v2 域，不能触发共享 legacy 数据覆盖。
- `audio.*` 等 Phase 4 audio ADR 决定。

## 共享应用锁

### 稳定 identity 与位置

所有平台共享同一个语义 identity：

```text
semantic identity: sakura.desktop.shared-user-data.v1
scope: 当前登录用户/桌面会话
```

平台 object：

| 平台 | 权威锁 |
|---|---|
| Windows | `Local\SakuraDesktop.SharedUserData.v1` named mutex |
| macOS | `$TMPDIR/sakura/sakura.desktop.shared-user-data.v1.lock`；`TMPDIR` 不可用时为 `$HOME/Library/Caches/sakura/sakura.desktop.shared-user-data.v1.lock` |
| Linux | `$XDG_RUNTIME_DIR/sakura/sakura.desktop.shared-user-data.v1.lock`；依次 fallback 到 `$XDG_STATE_HOME/sakura/...`、`$HOME/.local/state/sakura/...` |

identity 不按 executable、安装路径、版本、Qt/Tauri、PID、角色或 Core generation 区分。Windows 双方必须使用完全相同的 object name；macOS/Linux 双方必须使用完全相同的路径解析、打开模式和 advisory lock 语义。POSIX 普通文件存在或 PID 文本不能代表锁仍被持有，锁文件不得位于共享 `data/` 内。

WP-1P-03 冻结的 POSIX 细则为：候选环境根必须是绝对路径；canonical `sakura` 目录必须由当前 effective UID 所有并收紧为 `0700`；锁以 read/write、create、`O_CLOEXEC | O_NOFOLLOW`、`0600` 打开；已打开 fd 必须是当前用户所有、单硬链接 regular file，再执行 `flock(LOCK_EX | LOCK_NB)`。只有 `flock` 的 `EACCES/EAGAIN/EWOULDBLOCK` 表示 `already_running`；路径、打开、owner/type/link 或权限失败全部 fatal。完整可执行契约见 `docs/runtime-v2/WP-1P-03-shared-instance-lock.md`。

### 生命周期

- 桌面入口必须在任何 `data/` 创建、探针、日志、配置、migration、Core spawn 或外部服务启动之前获取锁。
- 锁由桌面生命周期根持有，直到窗口、Python Core、MCP/插件/TTS/浏览器后代和全部写入任务退出并 flush/close。
- Core 子进程不竞争桌面锁；其写权限来自当前持锁桌面根。
- 正常退出最后释放对应平台的 OS lock/handle。
- 崩溃/强杀后由 OS 自动释放 held lock；不依赖普通标志文件和 PID stale 猜测。
- `data/sakura.lock` 是历史 QLockFile 工件；WP-1A-04 切换后不再是权威，不自动删除。
- `data/memory/qdrant/.lock` 是 Qdrant 内部锁，不得用作桌面互斥或手工删除。

WP-1A-04 已把共用 mutex 前移到 crash log、selfcheck probe、默认配置、版本记录、migration 与服务构建之前，并以源码顺序测试和真实双入口门禁关闭该实现差距。

### 冲突与错误结果

另一个入口已持锁时，失败入口：

- 不启动 Core，不运行 migration，不打开 Qdrant，不创建/更新配置，不写共享 data/logs/diagnostics。
- 提示标题 `Sakura 已在运行`。
- 提示正文 `另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。`
- 不提供强制接管、删锁或并发只读启动。
- 返回结构化结果 `already_running`，交互式进程退出码为 0。

平台锁创建/打开本身失败且无法确认 holder 时，进入 fatal diagnostics 并返回非零；不能继续为第二写入者。

## 共享写入协议

Phase 1–3 原则上只允许：

- 只读复用。
- Phase 3 通过 Python `ChatHistoryStore` 追加 Qt 可读的 JSONL 记录。
- Qt 可忽略的 optional 字段；不得改变必需字段、文件名映射或把 optional 变为必需。
- v2 独立命名空间中的新增数据。

whole-file 共享写若在后续获得批准，必须：

1. 读取并校验当前目标和全局 version。
2. 在同一文件系统创建 mandatory backup，完成 flush/fsync 和 SHA-256/解析校验；失败立即停止，原文件不变。
3. 在目标同目录写临时文件，flush/fsync 后重新解析并校验关键字段/记录计数。
4. 使用平台原子替换提交。
5. replace 失败时保留原文件和已验证 backup，不应用依赖新配置的运行时状态。
6. 中断恢复只接受原 target 或已提交新 target；孤儿 temp 不是权威，不自动删除用户 backup。

不得在应用启动过程中静默执行不可逆 migration。不得将 `atomic_write_text(backup=True)` 的 best-effort `.bak` 当成 mandatory migration backup。

## Phase 1A 应用锁技术门输入

WP-1A-04 必须自动化或真实验证：

1. Qt 持有 `Local\SakuraDesktop.SharedUserData.v1` 时，Tauri 返回 `already_running`，不启动 Core，隔离 data 清单不变。
2. Tauri 持锁时，Qt 同样失败，隔离 data 清单不变。
3. 正常退出只有在所有写入任务/Core 后代结束后才允许另一入口获取。
4. 强杀 Qt/Tauri 后，Windows 自动释放 mutex，另一入口无需删文件即可获取。
5. stale `data/sakura.lock` 不影响新 mutex；测试不得删除真实 lock/Qdrant lock。
6. mutex API 权限/创建失败进入 fatal diagnostics，不继续启动。
7. 冲突失败发生在任何 `data/` 写入前，包括日志和 selfcheck probe。

以上全部通过后，本 ADR 才具备从 `Proposed` 更新为 `Technically Validated` 的 Phase 1A 锁证据；仅编译、mock 或单边实现不够。

### Phase 1A 技术验证结果（2026-07-20）

- Python legacy Qt 与 Rust/Tauri 使用 exact `Local\SakuraDesktop.SharedUserData.v1`；同名非 mutex 内核对象按 fatal 处理，不误判为普通冲突。
- `acceptance-drain-fail-closed-green-20260720-235133` 在真实 Windows Qt/Tauri 上通过 13/13 场景：debug/release 成功与重复执行、双向冲突、API fatal、正常/强杀释放、stale 文件锁、默认入口、显式回退脚本、QThread drain 期间持锁及 drain 超时 fail-closed。
- 正常退出在 external tools 清理和 lingering QThread drain 后释放；drain 超时以进程强制终止让 Windows 原子回收 mutex，不在仍有线程时通过 Python unwind 提前释放。
- 最终真实 `data/` 清单为 121 文件、1,045,977,101 bytes；before/after path、length、UTC mtime、SHA-256 canonical digest 均为 `1cd1602645b63308e74e2cd831d25870614ae26ff3bb993996a681071f0bd84c`。
- 负责人完成默认 Tauri、显式 Qt 回退、双向冲突、正常/强杀释放与退出清理后立即重获的实机验收；最终独立复审无 Critical/Important，P0/P1 与退出条件相关缺陷为零。

据此本 ADR 更新为 `Technically Validated`。Phase 3 的 Qt → Tauri v2 → Qt 共享数据兼容门禁仍未开始，因此不得标记为 `Accepted`。

以上仅是 Windows backend 的技术验证。macOS/Linux 不能复用本段作为通过证据。

## Phase 1P POSIX 应用锁技术门

WP-1P-03/06 必须在 macOS 与 Linux 分别验证：

1. legacy Python/Qt 持锁时 Tauri 返回 `already_running`，反向冲突同样成立。
2. 冲突和锁 API/权限失败发生在日志、配置、migration、Core spawn 和共享 `data/` 写入之前。
3. 正常退出必须等待 Core、插件、MCP、TTS、浏览器和写入任务完成后才释放。
4. 强杀任一入口后由 OS 释放 advisory lock，另一入口无需删除文件或判断 stale PID 即可获取。
5. 锁文件仍存在但无人持锁时可以正常获取；不得以文件存在误判冲突。
6. Rust/Python 对路径、符号链接、权限、用户 scope 和错误分类使用共享 golden fixture。
7. 真实数据清单前后保持不变；每个平台失败清场只处理本轮精确登记的进程和临时资源。

上述门禁通过前，本 ADR 的 `Technically Validated` 只能表述为 Windows shared lock 状态。

### WP-1P-03 backend 技术验证结果（2026-07-23）

- production Rust/Tauri 与 legacy Python backend 已冻结 macOS/Linux 路径优先级、canonical parent、owner/type/link、`0700/0600`、`O_CLOEXEC | O_NOFOLLOW` 和非阻塞 exclusive `flock` 语义。
- GitHub Actions run `30025831299` 在 `macos-15` arm64、`ubuntu-24.04` x64 和 `windows-2025` x64 同一提交 `71c3039c` 上全绿；push run `30025828101` 独立重复全绿。
- macOS/Linux 均真实通过 Rust 持锁/Python 冲突、Python 持锁/Rust 冲突、正常释放、普通文件残留、双方 holder 强杀后 OS 自动释放以及路径/权限安全测试；Windows named mutex 回归无变化。
- Test run `30025831268` 的 Unit/UI 全绿，迁移后仍导入 Tauri `main.py` 的 legacy 测试已改回 `legacy_qt_main.py`，没有恢复旧生命周期代码。

据此 WP-1P-03 的共享锁 backend 技术门已接受。真实三平台 Tauri Shell + Core、legacy Qt 回退入口、全部后代/写入任务排水完成后才释放，以及隔离数据清单前后零变化，仍由 WP-1P-06 验收；本段不能替代该产品级生命周期总门或 Phase 3 数据兼容门。

## Phase 3 Qt → Tauri v2 → Qt 兼容门禁输入

真实流程：

```text
legacy Qt 获取共用 mutex
-> 读取/创建脱敏角色、配置、历史和 Memory 元数据
-> 兼容写一条聊天历史并退出、drain 写入、释放 mutex
-> Tauri v2 获取同一 mutex
-> 读取相同角色/Core 配置
-> deterministic fake/local Provider 完成基础聊天
-> 只追加允许的 Qt-compatible history，写 data/runtime_v2 私有配置
-> 关闭 Core/后代/写入任务，最后释放 mutex
-> legacy Qt 重新获取 mutex并真实启动
-> Qt 读取原角色/配置/历史并显示新增记录
```

还必须覆盖：

- 两个入口同时启动时只有一个成功持锁。
- v2 专属 `desktop/ui/shell` 配置不改变 Qt 行为或 `system_config.yaml.ui`。
- mandatory backup 失败时原文件不变。
- temp write/fsync 失败时原文件完整。
- atomic replace 失败时原文件和 backup 完整，运行时不应用未提交值。
- temp fsync 后/replace 前异常中断时原 target 可读，孤儿 temp 被忽略；replace 后中断时新 target 完整。
- 损坏文件进入 diagnostics/read-only，不写默认值覆盖。
- `config_version > 4` 进入 diagnostics/read-only，不尝试降级。
- Memory/Qdrant、reminders/tasks/notes、runtime events、visual observations、插件/Studio/TTS/migration backups 等未批准数据 path/length/mtime/SHA-256 零变化。
- 真实仓库 `data/` 在整个隔离门禁前后清单完全一致。

脱敏 fixture、当前可执行 oracle 和重复执行命令：

- `tests/fixtures/runtime_v2/wp_0_02/`
- `docs/runtime-v2/baselines/wp_0_02_contract.py`
- `docs/runtime-v2/baselines/run_wp_0_02_baseline.ps1`

Phase 0 oracle 只能冻结预期，不能代替实际 Tauri/Qt 双进程、WebView 和应用锁门禁。Phase 3 全部通过后，本 ADR 才可更新为 `Accepted`。

## 结果与代价

收益：

- legacy Qt 是可验证的真实回退，而不是仅保留源码。
- Runtime v2 dogfooding 不会以用户数据为代价。
- lock identity、持有时间、冲突结果、stale 行为和数据安全状态不再留给实现临时决定。
- v2 专属配置不会被 legacy Qt 误读或覆盖。
- 后续停止 Qt 回退时有明确决策点，而不是被格式变化意外切断。

代价：

- Phase 1–3 只有当前 schema 4 数据允许共享兼容写，旧/未来/损坏数据先进入 diagnostics/read-only。
- Qt 回退期内，共享 schema 演进受到向后兼容约束。
- 双入口不能同时运行，开发调试需要显式退出前一个入口。
- Qdrant、插件、TTS 和无版本格式需要后续领域 Work Package 单独验证，不能由 Rust 统一接管。

## 允许调整的范围

可以调整 Win32/POSIX/Rust/Python 的具体 lock wrapper、private config 内部字段、备份命名和测试驱动，只要以下结果不变：

- exact shared lock identity 的语义稳定，Qt/Tauri 在同一平台竞争同一个 OS 锁。
- 同一用户会话只有一个桌面生命周期根和共享数据写入者。
- 锁在任何共享 data mutation 前获取，在全部写入者/后代退出后释放。
- Phase 1–3 无破坏性 migration，只有批准的 Qt-compatible write。
- migration-grade backup 失败不修改原数据。
- 未来/损坏 schema 进入 diagnostics/read-only。
- Qt → Tauri → Qt 真实兼容门禁和真实 `data/` 零变化证据通过。

## ADR 状态门禁

本 ADR 当前为 `Technically Validated`，精确含义是 Windows Phase 1A 的共用 named mutex、双入口、崩溃释放和真实数据零变化门禁已经通过，且 WP-1P-03 的 macOS/Linux Rust/Python shared-lock backend 已在原生平台通过双向冲突、正常/强杀释放和安全属性测试。更新为 `Accepted` 前仍必须完成 WP-1P-06 的真实三平台应用生命周期/数据零变化总门、全部正式平台的 Qt → Tauri → Qt 真实兼容门禁和产品功能等价台账中的数据项。停止支持 legacy Qt 或引入不兼容共享 schema 时，必须以新的 ADR Supersede 本文。
