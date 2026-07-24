# WP-3-01：无 Qt Assistant Adapter 与真实 Readiness

## 状态与范围

**状态：planned（本文件不是激活记录）。** 本规格只冻结 WP-3-01 的实施设计；不改变
`docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 的状态表，不授权生产
实现。实施开始前仍须由项目负责人另行完成 docs-only 激活。当前没有 Work Package 处于
`active` 或 `stabilizing`。

WP-3-01 的结果是在既有 Core Host worker/lifecycle/readiness/Snapshot 机制中接入第一个
真实、无 Qt 的 Assistant 会话。它仅只读加载角色和基础 Provider 配置，构造但不运行真实
`AgentRuntime` 与 `ChatPipeline`。它不是聊天、设置、迁移、资源管理或第二条进程生命
周期。

## 上下文与方案选择

WP-1C-04 和 WP-1P-05A 已接受；WP-3-01 是第一个消费 bundled Core lifecycle 的真实
领域切片。其三项候选方案如下。

| 方案 | 结论 | 原因 |
|---|---|---|
| 懒导入的薄 Assistant Facade + 专用严格只读 Core 配置读取器 | **采用** | 在 Core Host worker 内复用角色、模型 slot、Provider、`AgentRuntime` 和 `ChatPipeline` 的既有语义，同时维持无 Qt、无写入、无网络的边界。 |
| 复用或 headless 包装 `build_initial_app_context`/`AppContext` | 拒绝 | 导入 Qt，并初始化或写入 Memory、Tools、插件、MCP、TTS 和存储；范围及资源所有权均不可接受。 |
| Assistant sidecar/第二进程 | 拒绝 | 新增第二个生命周期根、IPC 与进程树所有权，不能比现有 Core Host 更安全或更小。 |

## 目标与非目标

### 目标

- `core.initialize` 快速接受空的生产 payload，在后台 worker 完成真实、可取消的初始化；
  `health`、`shutdown` 和握手不得等待领域工作。
- 只读地确定当前 `CharacterProfile`、chat Provider 与模型，并构造最小 `AssistantSession`。
- 复用 `CharacterRegistry`、角色 prompt 读取、`resolve_model_slot`、
  `OpenAICompatibleClient` 构造器、`AgentRuntime`、空 `ToolRegistry` 和 `ChatPipeline` 的
  业务语义。
- 将安全的角色摘要发布到既有 Python-owned Snapshot schema 1，并保持 generation 隔离和
  单调 revision。
- 在 hello 前及真实初始化后均不加载 PySide6/Qt UI，也不调用 Provider 网络、迁移或真实
  用户目录写入。
- 为三平台真实 Core Host/受控进程树验收预留一组确定的 fixture、故障和资源归零门禁。

### 非目标

- 不增加聊天 command、Router、Gateway、Operation、取消协议、UI 或前端。
- 不运行 `ChatPipeline`，不创建 history、Memory、Tools、MCP、插件、TTS、screen、
  scheduler、stores 或主动互动。
- 不做 Provider 认证、模型列表、DNS、socket、HTTP 或 TLS 验证；网络/认证是后续领域请求
  结果，绝不是 startup readiness。
- 不迁移、规范化后保存、备份或修复配置；不改用户数据、角色包或既有业务语义。
- 不重写 `AgentRuntime`，不抽象未来通用 components、资源 token 或第二个生命周期根。

## 架构、组件与所有权

```text
Rust RuntimeLocator-approved layout
          │ app root (CLI / HostConfig 注入)
          ▼
Core Host control plane ── quick initialize acceptance ──► background initializer
          │                                                   │ lazy imports only
          │ health/shutdown                                   ▼
          │                                           AssistantAdapter
          │                                           ├─ CoreConfigReader (read-only)
          │                                           ├─ CharacterRegistry
          │                                           └─ AssistantSession
          │                                              ├─ CharacterProfile
          │                                              ├─ OpenAICompatibleClient
          │                                              ├─ AgentRuntime(empty tools,
          │                                              │   disabled MemoryLike)
          │                                              └─ ChatPipeline (not run)
          ▼
existing readiness owner ── atomic publish ──► Snapshot schema 1 / Rust read-only cache
```

实施文件固定为 `app/config/core_config_reader.py` 与
`app/core_host/assistant_adapter.py`。前者是唯一的纯、显式只读 raw YAML projection；后者是唯一
Assistant Facade，定义 `AssistantSession`、disabled/no-op `MemoryLike`，并协调构造与关闭。
它们不得以 `application.py` 或 `AppContext` 作为入口。

`AssistantAdapter` 只拥有它实际构造的 session、pipeline 及未来明确可关闭的依赖；它借用
HostConfig 中的 app root、readiness owner、cancellation 与 issue sink。Provider、当前
`AgentRuntime` 和当前 `ChatPipeline` 没有外部 close 资源时，测试要锁定该事实；未来引入
可关闭对象时必须登记，由 Adapter 按创建的反序关闭。readiness owner 是唯一发布者；Adapter
不得保留 Rust Snapshot 或 generation credential。

## 精确接口与数据流

生产 `initialize` payload 为空对象；不得携带或选择 `ready`、`setup_required`、`degraded`、
`failed` 或 `hang`。现有 fake mode 仅可通过测试注入 initializer 使用，生产 envelope 不得
识别该开关。

接口名称和秘密边界如下冻结，不得以等价外观重新暴露 Provider 凭据：

```python
@dataclass(frozen=True)
class ProviderSelection:
    api_settings: app.llm.api_client.ApiSettings = field(repr=False)

@dataclass(frozen=True)
class CoreConfigReadResult:
    current_character_id: str | None
    provider_selection: ProviderSelection | None
    config_problem: StableReadinessError | None

class CoreConfigReader:
    def read(self, app_root: Path) -> CoreConfigReadResult: ...  # no writes

@dataclass
class AssistantSession:
    character: CharacterProfile
    provider: OpenAICompatibleClient
    runtime: AgentRuntime
    pipeline: ChatPipeline

class AssistantAdapter:
    def initialize(self, cancel: Event) -> ReadinessResult: ...
    def close(self) -> None: ...  # idempotent, reverse creation order
```

1. Core Host 从 Rust 通过 `RuntimeLocator` 已批准的 layout 获得 app root，写入 `HostConfig`/CLI；
   不提供默认路径，测试只能注入隔离 fixture 根。
2. `core.initialize` 创建一次后台 initializer，立即发布 `initializing`；重复调用不创建第二
   worker/session，返回既有启动结果。
3. worker 懒导入 Adapter 和其窄依赖，读取 config/角色；任何取消检查命中时不发布新 revision。
4. 读取成功后只做本地 Provider shape 校验，从 `ProviderSelection.api_settings` 构造
   `OpenAICompatibleClient(settings)`；`ProviderSelection` 与 `CoreConfigReadResult` 均不得以
   默认 repr、异常或诊断暴露 API key。禁止调用
   `test_connection`、`list_models`、`chat`、`complete_raw`、`complete_with_tools`。
5. Adapter 构造真实 `AgentRuntime`，显式传入 `ToolRegistry([])`、truthy disabled/no-op
   `MemoryLike`、角色 prompt/metadata，再构造真实 `ChatPipeline`；不得调用 `run_*`。
6. readiness owner 原子发布结果及安全摘要。关闭已经获胜时，worker 的晚到 session 必须立即
   close/丢弃，不能发布 readiness 或增加 revision。

## Readiness、错误与重试矩阵

所有结果均有稳定 `state`、machine `code`、经脱敏的稳定诊断 `message` 和公共角色摘要（存在时）。
下表是冻结映射；所有本 WP readiness result 均为 `retryable=false`，Core Supervisor 必须只按
此稳定 state/code 映射为不自动重启。为承载该映射，允许对
`desktop/src-tauri/src/core_supervisor.rs` 做必要的窄分类改动；不得增加第二条 restart 或
生命周期路径。Provider 网络和认证从不在启动阶段产生 readiness error。

| 输入或阶段 | state | stable code | session | restart |
|---|---|---|---|---|
| `system_config.yaml` 不存在 | `setup_required` | `CORE_CONFIG_SETUP_REQUIRED` | 不创建 | 不重启 |
| `system_config.yaml` 存在但为空、仅空白、null、YAML 损坏或不是 mapping | `failed` | `CONFIG_DATA_INVALID` | 不创建 | 不重启 |
| `system_config.yaml` 是 mapping，但 `config_version` 缺失、非 int、bool、旧版或未来版 | `failed` | `CONFIG_VERSION_UNSUPPORTED` | 不创建 | 不重启 |
| version 有效但 API/characters 配置缺失、空或未选择 chat profile/slot | `setup_required` | `CORE_CONFIG_SETUP_REQUIRED` | 不创建 | 不重启 |
| 无任何有效角色 | `setup_required` | `CHARACTER_SETUP_REQUIRED` | 不创建 | 不重启 |
| Provider type、profile、slot、模型归属、base URL、key 或 model 的本地形状无效 | `setup_required` | `PROVIDER_SETUP_REQUIRED` | 不创建 | 不重启 |
| Adapter 必要 pure import 失败、禁止 Qt/域阻断、不可恢复构造异常 | `failed` | `ASSISTANT_INITIALIZATION_FAILED` | 已建部分逆序关闭 | 不重启 |
| 配置当前角色失效，安全 fallback 为 `sakura` 或首个有效角色 | `degraded` | `CHARACTER_FALLBACK_APPLIED` | 构造 | 不重启 |
| 已选有效角色，但跳过损坏的可选角色包 | `degraded` | `OPTIONAL_CHARACTER_SKIPPED` | 构造 | 不重启 |
| 全部有效，本地 Provider shape 有效且 session 构造完成 | `ready` | `READY` | 构造 | 不重启 |

`degraded` 仅表示基础 session 已可用且存在上述可选/回退问题；一项输入不得同时映射多个
state。configured current 无效但存在安全 fallback 时，唯一结果是
`CHARACTER_FALLBACK_APPLIED`；选中角色有效且其他角色包损坏时，唯一结果是
`OPTIONAL_CHARACTER_SKIPPED`；无任何有效角色时，唯一结果是 `CHARACTER_SETUP_REQUIRED`。
fallback 与 skipped optional packages 同时存在时，唯一 code 优先为
`CHARACTER_FALLBACK_APPLIED`，其次才是 `OPTIONAL_CHARACTER_SKIPPED`。异常文本、文件名和日志
均须脱敏，不能包含 key、credential、完整 prompt、绝对路径或诊断内部对象 repr。

## Snapshot 公共契约

沿用 Python-owned schema 1 和现有 generation/revision 规则：每个 generation 隔离、revision
单调增加、Rust 只克隆缓存且不生成或改写 Python 业务字段。`currentCharacterSummary` 精确为：

```json
{
  "id": "string",
  "displayName": "string",
  "initialMessage": "string",
  "replyTones": ["string"],
  "portraitChoices": ["string"]
}
```

不得增加字段或泄露角色包/card/portrait 路径、voice/renderer/theme 私有配置、完整系统
prompt、Provider/profile/model/endpoint/API key、generation credential 或 diagnostics internals。
`activeInteractionSummary` 始终为 `null`。`components` 只表达本 WP 实际接入的
Adapter/Session 状态，不能为未初始化的 Memory/MCP/插件/TTS 创建通用模型。关闭或 generation
切换后，旧 worker/旧 Snapshot/旧 credential 均不得覆盖新 generation。

## Qt、导入与 legacy 等价边界

hello/import path 必须保持最小：`app.core_host` 在 initialize 前不导入 agent、UI、plugins、
voice 或 PySide6。Adapter 的所有领域 import 均在 worker 内延迟发生。初始化后可加载已批准的
纯 config/llm/agent 依赖，但仍禁止 `PySide6*`、Qt QObject/QThread、`ResourceManager`、Memory、
MCP、plugins、voice、TTS、backchannel 与 Qt stub。

两项真实 Qt blocker 必须通过真实无 Qt 重构消除，而不能用 `SAKURA_HEADLESS` 或 fake
`sys.modules`：

- 将 `VisualEffectMode` 字符串常量和纯校验移至纯模块（候选
  `app/config/visual_effect.py`）；`app.ui.window_backdrop` 从该模块导入并 re-export，legacy
  public API 和逐字段 validation 结果保持等价。
- `app.agent` 重 exports 改为 lazy；`runtime.py` 与 `memory_recall.py` 对 Memory 使用
  typing-only dependency/Protocol，需要时在 legacy default 路径局部导入。必须有 import API、
  default 行为及业务语义等价测试。

`CharacterRegistry` 固定接受 injectable issue sink：legacy 默认 sink 仍为 `log_event`，保持
现有文件日志行为；Core Host 固定注入仅输出经脱敏 stderr 的 sink，绝不触发 legacy runtime
log 文件。该分支必须有 legacy-default 与 Core-stderr 的等价/零写入测试。

## 严格只读配置与凭据规则

唯一的 `app/config/core_config_reader.py` 只读取注入 app root 内的原始文件，允许复用纯 DTO、
`load_yaml_mapping` 与 `resolve_model_slot`，但不得调用 `AppSettingsService.load_api_profiles()`
或 `load_model_selection()`，也不得运行 `MigrationRunner`。它绝不 migrate、normalize-and-save、
创建 backup、写 legacy log 或改变任何文件 bytes/mtime。

`system_config.yaml` 不存在是首次安装，固定为 `CORE_CONFIG_SETUP_REQUIRED`。文件存在但为空、
仅空白、null、损坏或非 mapping 固定为 `CONFIG_DATA_INVALID`；mapping 的 `config_version` 缺失、
非 int、bool、旧版或未来版固定为 `CONFIG_VERSION_UNSUPPORTED`。仅在 version 有效后，API 或
characters 缺失、为空或未选中才是 `CORE_CONFIG_SETUP_REQUIRED`。Provider 的有效性仅为本地形状：
选中的 chat profile/model 匹配，base URL、API key、model 非空，且 URL scheme/host 合法。API key
仅存于 `ProviderSelection.api_settings` 和 Python 进程 Provider settings，不进入 default repr、
session、Snapshot、errors、stderr、logs 或 Rust。

## 并发、取消与清理

control plane 不持有领域锁。初始化 worker 在读/构造边界检查 cancellation；`health` 永远返回
当前 readiness，`shutdown` 立即置 cancel 并不等待领域完成。Adapter `close()` 幂等，按
pipeline、runtime、provider、角色读取附属资源的逆创建顺序关闭；已关闭状态禁止二次发布。

`run_host` 的 finally 固定按 dispatcher/adapter、再 writer 的顺序逐项尝试 close；每一步均在
`try/finally` 中保证下一步执行。若已有主异常，cleanup failures 作为聚合附属错误保留；若无
主异常，首个 cleanup failure 为主错误，其余为聚合附属错误。所有输出均使用稳定、脱敏诊断，
任一 close 抛错都不能跳过 writer 或其他 close。Python cleanup 有界；不合作 worker/后代的
最终停止权仍属于 Rust `ManagedProcessTree`，不得在 Python 引入第二个强杀所有者。

Rust shutdown intent 成功写入 Core stdin 的时刻起，使用**单一共享的端到端 5000ms deadline**：
其中 protocol graceful shutdown 最多 3000ms，且该 3000ms 包含在同一 5000ms 内，而非随后再起
一段 5000ms。剩余预算用于关闭 stdin、等待根、终止整树、验证树为空、排空/关闭 pipe、join
stderr/writer thread、release fd/handle 与删除临时资源；5000ms 内必须使 root、所有后代、pipe、
fd、handle、thread 和 temp 均归零。允许且必须测试对
`desktop/src-tauri/src/core_host_runtime.rs` 的 deadline 改造，以把现有分段等待改成该共享
deadline；Windows x64、macOS arm64、Linux x64 均验证正常、超时、忽略 shutdown 与 close-block
场景。

## 实施候选白名单与禁止范围

以下是激活后才可使用的窄候选；每一项领域/UI import-only refactor 都须先以可执行 import
guard 证明不可避免，并以 legacy 等价测试证明无业务语义变化。

| 范围 | 候选路径 | 理由 |
|---|---|---|
| Adapter/readiness/CLI | `app/core_host/assistant_adapter.py`、`app/core_host/server.py`、`app/core_host/__main__.py` | 薄 Facade、注入 initializer、原子 publish/close 与注入 app root 的唯一 CLI 入口。 |
| 只读配置 | `app/config/core_config_reader.py`、`app/config/models.py`、`app/config/model_slots.py` | 纯 raw config projection 和 slot 解析。 |
| 角色/Provider/Pipeline | `app/config/character_loader.py`、`app/llm/api_client.py`、`app/core/chat_pipeline.py` | 仅复用读取、构造和无网络 Provider。 |
| 已证明的 Qt/import blocker | `app/config/visual_effect.py`、`app/ui/theme.py`、`app/ui/window_backdrop.py`、`app/agent/__init__.py`、`app/agent/runtime.py`、`app/agent/memory_recall.py` | 仅移出 Qt 依赖或改为 typing/lazy import，并保留 legacy 行为。 |
| 测试与 fixture | `tests/unit/test_core_host_*.py`、`tests/integration/test_core_host_*.py`、`tests/unit/test_core_host_cli.py`、`tests/unit/test_agent_runtime.py`、`tests/integration/test_chat_pipeline.py`、`tests/fixtures/runtime_v2/wp_3_01/**` | 隔离、脱敏 fixture、CLI 注入和既有 Core Host 命名。 |
| 三平台验收与 CI | `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`、`desktop/src-tauri/src/core_host_runtime.rs`、`desktop/src-tauri/src/core_supervisor.rs`、`.github/workflows/runtime-v2-platform-foundation.yml`、`tests/unit/test_runtime_v2_platform_workflow.py` | 仅将既有 lifecycle/snapshot acceptance、共享 deadline 与 retry 分类接到真实 Adapter；workflow 必须显式执行新增 `core_host_*` pytest。 |

明确禁止 `app/core/bootstrap.py`、`app/core/app_context.py`、`app/core/extensions.py`、resource
manager、chat/mobile workers、Memory 及其 curator、builtin/desktop tools、`app/agent/mcp/**`、
`app/plugins/**`、顶层 `plugins/**`、`app/voice/**`、产品 UI/Settings/Studio、storage/history/
runtime events/visual observation、`main.py`、`legacy_qt_main.py`、Router/Gateway/Operation/chat
Rust 或 WebView 文件、`desktop/frontend/**`、`third_party/**`、`tools/mcp/**`。也禁止写
`data/**`、`characters/**`、`runtime/**`、migration backup、用户日志/cache、Qdrant/mem0、TTS、
插件私有数据和任何真实用户目录。零新增依赖：禁止修改 `requirements*.txt`、`pyproject.toml`、
任何 Python/Node package manifest、`Cargo.toml`、`Cargo.lock`、`package-lock.json`、
`pnpm-lock.yaml`、`yarn.lock` 或其他 package lock。

## 验收矩阵、CI 与资源零残留

所有 fixture 位于隔离临时根且完全脱敏；每项 data/characters/config 前后记录相对路径、长度、
mtime、SHA-256，必须完全一致。至少覆盖如下确定性矩阵。

| 门类 | 必测情形 | 断言 |
|---|---|---|
| 配置/角色对拍 | `system_config.yaml` 不存在；存在但空/blank/null/坏 YAML/nonmapping；mapping 的 missing/bool/string/old/future version；version 有效但 API/characters 缺失/未选；valid；configured current 无效的 `sakura`/first fallback；坏可选包；无任何有效角色 | 精确 state/code：config 分支逐项对照矩阵；fallback 为 `CHARACTER_FALLBACK_APPLIED`、仅 skip 为 `OPTIONAL_CHARACTER_SKIPPED`、无有效角色为 `CHARACTER_SETUP_REQUIRED`；legacy fallback/slot/主题 validation 等价；无 bytes/mtime/.bak 变化。 |
| Provider/秘密 | 缺 profile/slot/model/base URL/key、有效本地 URL、网络不可达/认证未知 | invalid 为 `PROVIDER_SETUP_REQUIRED`；patch DNS/socket/urllib 为 fail-on-call 后有效配置仍 ready，调用数为零；秘密不出现在任何公开面。 |
| session/禁止域 | valid 角色与 Provider、Memory/MCP/plugins/TTS/voice/screen fail-if-called | 构造真实 runtime、空 tools、disabled Memory、pipeline；不运行 pipeline，不加载禁止域。 |
| import/等价 | hello 前与 initialize 后 subprocess probe；legacy agent imports；Theme/VisualEffectMode | 前者无 agent/UI/PySide6，后者仍无 Qt/ResourceManager/禁止域；public import/default/validation 语义等价。 |
| 生命周期/故障 | 慢 reader、构造中途异常、重复 initialize/shutdown、shutdown before initialize、EOF、writer failure、close throw、close block、init/close race、old worker late result | health 在 deadline 内；只构造/关闭一次；异常聚合不跳过 writer；晚结果不发布；shutdown successful-write 起共享 5000ms 内 root/后代/pipe/fd/handle/thread/temp 归零。 |
| generation/安全 | 连续两 generation、stale Snapshot/credential、公开 summary 扫描 | 单调 revision、generation 隔离、Rust 只读 clone；无路径/prompt/key/credential/endpoint/model/诊断 internals。 |
| 真实进程树 | 协作与忽略 TERM 的一个/多个 Adapter 后代、Core crash、外部 kill | Rust ManagedProcessTree 在 Windows x64/macOS arm64/Linux x64 收束 root/后代/pipe/fd/handle/thread/temp，锁立即重获。 |

Python unit、Python subprocess、Rust real-host 和 packaged/Shell 三平台纵向测试都必须覆盖
`hello → initialize → setup_required|ready|degraded → snapshot → repeated health → shutdown`，以及
failed、crash、强制回收和连续 generation。仅 fake mode、sleeping host、根 PID 消失或 Python
unit 通过不能宣称真实 Adapter 资源归零。CI 延续 `app/**`、`desktop/src-tauri/**`、
`desktop/tests/**`、`tests/fixtures/runtime_v2/**` 和 `tests/*/test_core_host_*.py` 的现有
platform filter；新增 `core_host_*` tests 必须由
`.github/workflows/runtime-v2-platform-foundation.yml` 的三平台 jobs 以明确 pytest step 实际执行，
并由 `tests/unit/test_runtime_v2_platform_workflow.py` 断言该命令和 push/PR filters。仅 path
trigger 不构成 Python pytest 已在三平台执行的证据。

## 回退与已冻结风险边界

实施应以独立 WP-3-01 commit 回退：先停止/验证所有 generation 和受控树已退出，再 `git
revert` 该 WP 的实现提交；不得删除或改写 data、角色、配置、日志、cache 或 migration
工件。若真实 Adapter 导入阻断 legacy 或资源门禁失败，回退到 WP-1C-04 已接受的 fake readiness
链，而不是引入 Qt stub、AppContext 或 sidecar。

风险仅在实施验收中验证，不留实现选择：唯一 parser 是
`app/config/core_config_reader.py`；`CharacterRegistry` 以 legacy `log_event` 默认 sink 与 Core
sanitized-stderr sink 双路径保持等价；所有 readiness code 均 `retryable=false` 且映射到既有
Core Supervisor 的不自动重启分类；`run_host` 以已规定顺序聚合 cleanup errors；Rust 从 shutdown
successful-write 起以单一 5000ms deadline 收束完整树和资源。若任何门禁失败，按上节回退，不得
放宽 code、重试、秘密、网络、写入、Qt 或进程所有权边界。
