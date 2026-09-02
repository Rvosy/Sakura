---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-3-01：无 Qt Assistant Adapter 与真实 Readiness

## 状态与范围

**历史结论：2026-07-26 accepted。** 当前状态唯一来源、接受证据和下一启动点见
`docs/plans/runtime-v2/work-packages.md`；逐任务实施记录见
`docs/archive/plans/runtime-v2/2026-07-25-wp-3-01-assistant-adapter-readiness.md`。

WP-3-01 的结果是在既有 Core Host worker/lifecycle/readiness/Snapshot 机制中接入第一个
真实、无 Qt 的 Assistant 会话。它加载角色和基础 Provider 配置，构造但不运行真实
`AgentRuntime` 与 `ChatPipeline`。它不是聊天、设置、迁移、资源管理或第二条进程生命
周期。

## 架构依据

WP-1C-04 和 WP-1P-05A 已接受；WP-3-01 是第一个消费 bundled Core lifecycle 的真实领域切片。
为何采用懒导入的薄 Assistant Facade 与专用配置投影，以及为何拒绝 `AppContext` 聚合入口和第二
Assistant 进程，见
[`ADR-0005`](../../adr/0005-runtime-v2-headless-assistant-adapter.md)。本文只定义该选择必须满足的
接口、数据、错误与验收契约。

## 目标与非目标

### 目标

- `core.initialize` 快速接受空的生产 payload，在后台 worker 完成真实、可取消的初始化；
  `health`、`shutdown` 和握手不得等待领域工作。
- 确定当前 `CharacterProfile`、chat Provider 与模型，并构造最小 `AssistantSession`。
- 复用 `CharacterRegistry`、角色 prompt 读取、`resolve_model_slot`、
  `OpenAICompatibleClient` 构造器、`AgentRuntime`、空 `ToolRegistry` 和 `ChatPipeline` 的
  业务语义。
- 将安全的角色摘要发布到既有 Python-owned Snapshot schema 1，并保持 generation 隔离和
  单调 revision。
- 在 hello 前及真实初始化后均不加载 PySide6/Qt UI，也不调用 Provider 网络或隐式迁移；
  正常日志、cache、配置和其他运行时写入遵循既有数据所有权与原子写契约。
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
          │                                           ├─ CoreConfigReader (projection)
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
`app/core_host/assistant_adapter.py`。前者是唯一的纯、显式 raw YAML projection；后者是唯一
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
    provider_selection: ProviderSelection | None = field(repr=False)
    config_problem: StableReadinessError | None

class CoreConfigReader:
    def read(self, app_root: Path) -> CoreConfigReadResult: ...  # no writes

@dataclass
class AssistantSession:
    character: CharacterProfile
    provider: OpenAICompatibleClient = field(repr=False)
    runtime: AgentRuntime
    pipeline: ChatPipeline

class AssistantAdapter:
    def initialize(self, cancel: Event) -> ReadinessResult: ...
    def close(self) -> None: ...  # idempotent, reverse creation order
```

以下同为允许实现路径中的强制秘密字段；无论是 dataclass default repr、custom repr、异常、
日志、诊断或 public projection，均不得输出其值：

```python
class ApiSettings:
    api_key: str = field(repr=False)

class ApiConfigProfile:  # 仅当 CoreConfigReader 使用该 DTO
    api_key: str = field(default="", repr=False)

class HostConfig:
    generation_credential: str = field(repr=False)
```

`AssistantSession` 内部可以持有 Provider settings/API key 以供真实 Provider 使用，但
`AssistantSession.provider` 必须是 `field(repr=False)`；key 不得进入 session repr、generic
serialization 或 public projection。`ProviderSelection.api_settings`、
`CoreConfigReadResult.provider_selection`、`ApiSettings.api_key`、可能使用的
`ApiConfigProfile.api_key` 及 `HostConfig.generation_credential` 均适用相同规则；
`ApiConfigProfile` 的 `default=""`、构造签名与 equality 必须保持 legacy 等价。generation
credential 不得进入任意 repr。

### 显式 serializer 契约

`repr=False` 只约束 dataclass repr，**不**授权或阻止其他序列化。含 API key 的 DTO、
`AssistantSession` 与 `HostConfig` 不得传入 generic `dataclasses.asdict`、`vars`、`__dict__`、
`pickle` 或 default-JSON serializer；生产代码只能使用显式 allowlist projector/serializer。API key
没有任何授权输出 serializer，只允许存在于 Python 内部的 Provider settings（及其 transient
`ProviderSelection` wrapper）与 session-held Provider。Snapshot 的 `currentCharacterSummary` 只能经显式
`project_current_character_summary(profile)` 输出已冻结的五字段：`id`、`displayName`、
`initialMessage`、`replyTones`、`portraitChoices`；不得以 generic object serialization 替代。

generation credential 的唯一授权序列化出口是既有本地受控 Core framed IPC request/response envelope
（`app/core_host/protocol.py` 与 Rust peer）。除该 envelope 外，它不得进入 Snapshot、repr、log、
error、diagnostics、WebView 或其他 public surface。测试须对显式 projector、受控 envelope、日志等
可观察输出进行 secret scan，并以静态和动态测试阻止内部 DTO 被 generic serializer 使用；这不是
含义不定的“禁止一切序列化”。

1. Core Host 从 Rust 通过 `RuntimeLocator` 已批准的 layout 获得 app root，写入 `HostConfig`/CLI；
   不提供默认路径，测试只能注入隔离 fixture 根。
2. `core.initialize` 创建一次后台 initializer，立即发布 `initializing`；重复调用不创建第二
   worker/session，返回既有启动结果。
3. worker 懒导入 Adapter 和其窄依赖，读取 config/角色；任何取消检查命中时不发布新 revision。
4. 读取成功后只做本地 Provider shape 校验，从 `ProviderSelection.api_settings` 构造
   `OpenAICompatibleClient(settings)`；`ProviderSelection`、`CoreConfigReadResult`、
   `AssistantSession`、settings/profile 与 `HostConfig` 均不得以 default/custom repr、generic
   serialization、异常、诊断或 public projection 暴露 API key 或 generation credential；credential
   仅可进入既有受控 framed IPC envelope。禁止调用
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
| version 有效，`api.yaml` 或 `characters.yaml` 不存在、zero/blank/null/empty mapping | `setup_required` | `CORE_CONFIG_SETUP_REQUIRED` | 不创建 | 不重启 |
| `api.yaml`/`characters.yaml` 存在但 YAML 语法坏、非 mapping 且非 null、字段或容器类型错误 | `failed` | `CONFIG_DATA_INVALID` | 不创建 | 不重启 |
| 有效 API mapping 缺失、为空或不匹配 profile/slot/model/base URL/key | `setup_required` | `PROVIDER_SETUP_REQUIRED` | 不创建 | 不重启 |
| characters mapping 缺失、`current_character_id` 为空或没有对应已安装角色 | `setup_required` | `CHARACTER_REQUIRED` | 不创建 | 不重启 |
| Adapter 必要 pure import 失败、禁止 Qt/域阻断、不可恢复构造异常 | `failed` | `ASSISTANT_INITIALIZATION_FAILED` | 已建部分逆序关闭 | 不重启 |
| 已选有效角色，但跳过损坏的可选角色包 | `degraded` | `OPTIONAL_CHARACTER_SKIPPED` | 构造 | 不重启 |
| 全部有效，本地 Provider shape 有效且 session 构造完成 | `ready` | `READY` | 构造 | 不重启 |

`degraded` 仅表示已选角色和基础 session 可用，但其他可选角色包被跳过；聊天、取消和主动观察仍必须可用，
桌面端不得把它当作 session 不可用。一项输入不得同时映射多个 state。
未选择角色、配置角色不存在或角色目录为空都返回 `CHARACTER_REQUIRED`，不得选择 `sakura`、首个角色或任何
隐藏 fallback。异常文本、文件名和日志均须脱敏，不能包含 key、credential、完整 prompt、绝对路径或诊断
内部对象 repr。

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
现有文件日志行为；Core Host 的 sink 必须保证诊断脱敏，可以输出 stderr，也可以接入既有 runtime
log 所有权路径。该分支验证 legacy-default 与 Core 诊断语义等价、无秘密泄漏；不要求全局零写入。

## 配置投影与凭据规则

唯一的 `app/config/core_config_reader.py` 投影注入 app root 内的原始文件，允许复用纯 DTO、
`load_yaml_mapping` 与 `resolve_model_slot`，但不得调用 `AppSettingsService.load_api_profiles()`
或 `load_model_selection()`，也不得 import 或运行 `MigrationRunner`。它在自身（或唯一纯 config
辅助模块）定义 `SUPPORTED_CORE_CONFIG_VERSION = 1`；唯一支持值是 non-bool integer `1`，不得从
迁移常量或其他写入路径取得版本语义。它绝不 migrate、normalize-and-save、
创建 backup 或写 legacy log。这个“不由投影器持久化”的约束只是组件职责，不是对 `data/` 的
全局只读政策；其他受权组件可以按既有契约正常写入。

`system_config.yaml` 不存在是首次安装，固定为 `CORE_CONFIG_SETUP_REQUIRED`。文件存在但为空、
仅空白、null、损坏或非 mapping 固定为 `CONFIG_DATA_INVALID`；mapping 的 `config_version` 缺失、
bool、string 或任何非 `1` 值固定为 `CONFIG_VERSION_UNSUPPORTED`，仅 `==1` 可继续读取。辅助配置
使用唯一矩阵：`api.yaml`/`characters.yaml` 不存在、zero/blank/null/empty mapping 都是
`CORE_CONFIG_SETUP_REQUIRED`；文件存在而 YAML 语法坏、非 mapping 且非 null、字段或容器类型错误
都是 `CONFIG_DATA_INVALID`。有效 API mapping 缺失、为空或不匹配 profile/slot/model/base URL/key
是 `PROVIDER_SETUP_REQUIRED`；有效 characters mapping 缺失或 `current_character_id` 为空是
`CORE_CONFIG_SETUP_REQUIRED`，非空但无效时按角色 fallback 规则处理。Provider 的有效性仅为本地
形状：选中的 chat profile/model 匹配，base URL、API key、model 非空，且 URL scheme/host 合法。
API key 可存在于 repr-excluded settings/profile/selection 及 session-held Provider settings；不得
进入 session repr、generic serialization、public projection、Snapshot、errors、stderr、logs 或 Rust。

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
| 配置投影 | `app/config/core_config_reader.py`、`app/config/models.py`、`app/config/model_slots.py` | 纯 raw config projection 和 slot 解析。 |
| 角色/Provider/Pipeline | `app/config/character_loader.py`、`app/llm/api_client.py`、`app/core/chat_pipeline.py` | 仅复用读取、构造和无网络 Provider。 |
| 已证明的 Qt/import blocker | `app/config/visual_effect.py`、`app/ui/theme.py`、`app/ui/window_backdrop.py`、`app/agent/__init__.py`、`app/agent/runtime.py`、`app/agent/memory_recall.py` | 仅移出 Qt 依赖或改为 typing/lazy import，并保留 legacy 行为。 |
| 测试与 fixture | `tests/unit/test_core_host_*.py`、`tests/integration/test_core_host_*.py`、`tests/unit/test_core_host_cli.py`、`tests/unit/test_agent_runtime.py`、`tests/integration/test_chat_pipeline.py`、`tests/fixtures/runtime_v2/wp_3_01/**` | 隔离、脱敏 fixture、CLI 注入和既有 Core Host 命名。 |
| 三平台验收与 CI | `desktop/src-tauri/src/core_host_runtime.rs`、`desktop/src-tauri/src/shell_lifecycle.rs`、`desktop/src-tauri/src/core_supervisor.rs`、`.github/workflows/test.yml` | 将 lifecycle/snapshot acceptance 与共享 deadline 接到真实 Adapter；Python acceptance 在 Ubuntu 执行一次，原生 matrix 验证各平台 Shell、进程和 RuntimeLocator 边界。 |

明确禁止 `app/core/bootstrap.py`、`app/core/app_context.py`、`app/core/extensions.py`、resource
manager、chat/mobile workers、Memory 及其 curator、builtin/desktop tools、`app/agent/mcp/**`、
`app/plugins/**`、顶层 `plugins/**`、`app/voice/**`、产品 UI/Settings/Studio、storage/history/
runtime events/visual observation、`main.py`、`legacy_qt_main.py`、Router/Gateway/Operation/chat
Rust 或 WebView 文件、`desktop/frontend/**`、`third_party/**`、`tools/mcp/**`。零新增依赖：禁止修改 `requirements*.txt`、`pyproject.toml`、
任何 Python/Node package manifest、`Cargo.toml`、`Cargo.lock`、`package-lock.json`、
`pnpm-lock.yaml`、`yarn.lock` 或其他 package lock。

`data/**` 是应用的正常运行时可写目录，不设整目录只读门禁。开发、启动和验收可以产生任务范围内
预期的日志、cache、配置、history 或其他由对应服务拥有的持久化结果，也不要求为这些预期写入
恢复 mtime 或伪造“零变化”证据。写入必须由任务范围或被测产品路径触发，遵循已有原子写、schema、
凭据脱敏和共享锁契约；不得顺手改写、清理、截断、恢复或删除无关用户数据。破坏性故障注入、
migration 回放以及会污染既有状态的测试仍使用隔离临时根。`characters/**` 和 `runtime/**` 只有在
任务明确涉及角色资源或 Runtime 布局时才修改。

本 WP 只消费 WP-1P-02 manifest 已冻结的 PyYAML 6.0.2 wheel import artifact；它是 `requirements.txt` 中既有依赖的不可变打包闭包，不构成新增依赖或 package manifest。三平台验收必须直接消费并复核该工件，禁止在下载 CPython 后运行 pip、写入 site-packages 或修改 `_pth` 来改变受验 Runtime。

## 验收矩阵、CI 与资源零残留

所有受版本控制的 fixture 保持脱敏且不原地污染；需要故障注入或破坏性写入时复制到隔离临时根。
验收只核对本场景声明的预期写集、禁止写集和内容不变量，不再计算或要求仓库真实 `data/` 整目录
的 path/mtime/SHA-256 前后完全一致。至少覆盖如下确定性矩阵。

| 门类 | 必测情形 | 断言 |
|---|---|---|
| system config/角色对拍 | `system_config.yaml` 不存在；存在但空/blank/null/坏 YAML/nonmapping；`config_version` missing/bool/string/non-v1/`==1`；valid；未选角色；配置角色不存在；坏可选包；无任何有效角色 | 精确 state/code：only `SUPPORTED_CORE_CONFIG_VERSION == 1` 继续；无可用当前角色为 `CHARACTER_REQUIRED`，仅 skip 为 `OPTIONAL_CHARACTER_SKIPPED`；不存在默认角色或首角色 fallback；无 bytes/mtime/.bak 变化。 |
| 辅助配置 fixture | `api.yaml`/`characters.yaml` 不存在、zero/blank/null/empty mapping；YAML syntax error；nonmapping nonnull；字段/容器类型错误；API mapping 缺/空/不匹配 profile/slot/model/base/key；characters mapping 缺/空 current id | syntax/shape/type 为 `CONFIG_DATA_INVALID`，API shape 为 `PROVIDER_SETUP_REQUIRED`，characters current id 缺/空最终投影为 `CHARACTER_REQUIRED`；读取前后 bytes/mtime/.bak 完全不变。 |
| Provider/秘密 | 缺 profile/slot/model/base URL/key、有效本地 URL、网络不可达/认证未知、`repr()`/异常/日志、allowlist projector、受控 IPC envelope 的 secret scan；`ApiConfigProfile` default 构造/签名/equality 对拍 | invalid 为 `PROVIDER_SETUP_REQUIRED`；patch DNS/socket/urllib 为 fail-on-call 后有效配置仍 ready，调用数为零；`AssistantSession.provider`、`ProviderSelection.api_settings`、`CoreConfigReadResult.provider_selection`、`ApiSettings.api_key`、使用时默认 `""` 的 `ApiConfigProfile.api_key` 与 `HostConfig.generation_credential` 均 repr-excluded；`ApiConfigProfile` 保持 legacy default/构造签名/equality；API key 无输出 serializer，credential 仅可出现在受控 envelope。 |
| session/禁止域 | valid 角色与 Provider、Memory/MCP/plugins/TTS/voice/screen fail-if-called | 构造真实 runtime、空 tools、disabled Memory、pipeline；不运行 pipeline，不加载禁止域。 |
| import/等价 | hello 前与 initialize 后 subprocess probe；legacy agent imports；Theme/VisualEffectMode | 前者无 agent/UI/PySide6，后者仍无 Qt/ResourceManager/禁止域；public import/default/validation 语义等价。 |
| 生命周期/故障 | 慢 reader、构造中途异常、重复 initialize/shutdown、shutdown before initialize、EOF、writer failure、close throw、close block、init/close race、old worker late result | health 在 deadline 内；只构造/关闭一次；异常聚合不跳过 writer；晚结果不发布；shutdown successful-write 起共享 5000ms 内 root/后代/pipe/fd/handle/thread/temp 归零。 |
| generation/安全 | 连续两 generation、stale Snapshot/credential、受控 framed IPC envelope、`repr`/generic serializer/error/log/diagnostics/WebView/public projection 的 credential 扫描、公开 summary 扫描 | 单调 revision、generation 隔离、Rust 只读 clone；generation credential 仅可在既有 envelope 中序列化，不进任意 repr、Snapshot 或其他公开面；无路径/prompt/key/credential/endpoint/model/诊断 internals。 |
| 真实进程树 | 协作与忽略 TERM 的一个/多个 Adapter 后代、Core crash、外部 kill | Rust ManagedProcessTree 在 Windows x64/macOS arm64/Linux x64 收束 root/后代/pipe/fd/handle/thread/temp，锁立即重获。 |

Python unit、Python subprocess、Rust real-host 和 packaged/Shell 三平台纵向测试都必须覆盖
`hello → initialize → setup_required|ready|degraded → snapshot → repeated health → shutdown`，以及
failed、crash、强制回收和连续 generation。仅 fake mode、sleeping host、根 PID 消失或 Python
unit 通过不能宣称真实 Adapter 资源归零。CI 延续 `app/**`、`desktop/src-tauri/**`、
`desktop/tests/**`、`tests/fixtures/runtime_v2/**` 和 `tests/*/test_core_host_*.py` 的现有
platform filter。新增 `core_host_*` 测试由 `.github/workflows/test.yml` 的 Python job 执行一次；
需要真实操作系统边界的用例放入 native matrix，并按平台运行对应的 Rust 测试。路径过滤本身不算测试证据。

秘密回归另须用静态扫描拒绝 `dataclasses.asdict`、`vars`、`__dict__`、`pickle` 与 default-JSON
serializer 对内部含密 DTO/Session/HostConfig 的调用，并在动态 probe 中让这些 generic 路径 fail
closed；同时证明唯一显式角色 projector 的字段恰为五项，API key 不存在授权输出 serializer，
generation credential 的唯一授权序列化出口是受控 framed IPC envelope。

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
放宽 code、重试、秘密、网络、数据所有权、Qt 或进程所有权边界。
