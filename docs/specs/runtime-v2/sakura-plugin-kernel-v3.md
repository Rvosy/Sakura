---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
updated: 2026-08-20
---

# Sakura Plugin Kernel v3 规范（v0.3 Freeze Candidate）

## 1. 目标、状态与适用范围

本规范定义 Plugin API v3 的第一阶段公共契约。它的完成标准不是把现有代码移动到 `plugins/`，而是：

- 官方与第三方插件使用同一生命周期和组合 API；
- 插件可创造 Sakura Core 从未预定义的 Service，并被另一插件消费；
- 删除或替换 GPT-SoVITS、Genie、Mem0 时，Core 业务逻辑不增加实现类型判断；
- 插件禁用、依赖消失或 Worker 重建后，不残留 Service、Handler、Effect 或子进程。

本文是实现前的 `draft` Freeze Candidate，不声称当前 Plugin API v2 已满足这些行为。它在 v3 cutover 后
替代 WP-4-04 中 permission、Capability Registry 和 feature-specific RPC 的规范性部分；WP-4-04 已发生的
实现、验证与验收记录继续作为历史事实保留。

本阶段只面向 Runtime v2。Legacy Qt 仅作为迁移参考，不承载 Plugin API v3。

## 2. 运行拓扑与 Context

```text
Sakura Desktop / Rust Supervisor
                |
         Runtime Python Core
                |
          Generic Bridge
                |
 generation-private Plugin Worker
                |
        Sakura Plugin Kernel
                |
             Plugins
```

- Core 不导入插件实现；一个 Core generation 最多拥有一个 Plugin Worker。
- Plugin Kernel、Service Registry、Event/Transform、Effect 和 Config 全部位于 Worker 内。
- 普通插件共享一个 Application Context，所有 Service 只在该 Context 注册。
- Session Context 只携带当前角色、会话数据、请求状态和 session-scoped Effects；它可以消费但不能提供、
  覆盖或 shadow Application Service。
- 单次调用继续使用 request ID、deadline 和取消信号，不创建 Operation Context。
- 插件自行创建的子进程必须注册正常清理 Effect；Worker/后代最终仍由 Rust 进程树兜底回收。

## 3. Manifest、发现与信任模型

Plugin API v3 manifest 的最小激活信息为：

```yaml
api: 3
id: com.example.umbrella
name: Umbrella
version: 0.1.0
entry: plugin:UmbrellaPlugin
provides: []
requires:
  - com.example.weather
optional:
  - sakura.tts
```

- `id` 是稳定插件身份和 Config/Data 命名空间；第三方应使用反向域名或其他全局唯一前缀。
- `provides` 列出启动后稳定对外提供的 Service，用于预检冲突、加载顺序和可能提供者提示。
- `requires` 缺失时插件不激活；`optional` 缺失不阻止插件激活。
- 三个依赖字段只用于激活与诊断，不是权限；`ctx.get()` 不检查 manifest 声明。
- 插件可在运行时提供未声明 Service，但第二个同名绑定出现时，后提供插件进入 `conflict`。
- required dependency cycle 使环中插件进入 `failed`，reason 明确为 dependency cycle；不增加新状态。

插件是用户主动安装的可信本地 Python 代码，与 Sakura 拥有相同账户权限。第一阶段没有 permission、签名、
OS sandbox、WASM、依赖自动下载或 pip/npm 安装。UI 必须明确提示只安装可信插件。

发现来源只有随应用发布的内置插件目录和用户插件代码目录。插件代码与私有数据必须分离；禁用、升级或
卸载代码不得隐式删除插件数据。

本地安装只支持 ZIP 和文件夹：校验 manifest、ID 冲突、文件数量/总大小、路径逃逸、绝对路径、symlink
与解压边界后复制到用户插件代码目录并重新扫描。不提供市场、在线更新、版本求解或依赖下载。卸载用户
插件默认只移除代码；删除私有数据是独立、显式确认的动作。内置插件不可从安装目录卸载。

## 4. 最小 SDK

### 4.1 Service

```python
ctx.provide("com.example.weather", weather)
weather = ctx.get("com.example.weather")
```

- `provide()` 在 Application Context 注册唯一 Service，并自动成为当前 Plugin Effect。
- 同名 Service 不允许覆盖。预检发现多个 enabled 插件声明相同 `provides` 时，相关提供插件均进入
  `conflict`，该 Service 不发布；用户必须禁用其中一个。
- 未声明的 runtime conflict 只隔离后提供者，已经 active 的 Service 保持不变。
- `get()` 是一次性本地查找；缺失时抛出包含 Service key 的 `MissingServiceError`。
- 多 Provider 注册、选择、健康和 fallback 属于具体 Service，不属于 Kernel。

Worker 内 Service 默认是普通 Python 对象。插件间调用不序列化、不经过 Core，也不要求 DTO。只有提供者
显式列出的 export 方法可跨 Bridge；未导出的方法不能被 Core 反射或调用。

每次 plugin setup 开始前必须创建独立 root EffectScope。本次 setup 的 `provide()`、Event/Transform
Handler、inject child scope 和自有 Effect 全部归入该 scope。setup 完整返回后插件才进入 `active`，
Plugin Kernel 才向 required/inject Consumer 通知其 Service 可用。

通过 Host Service 注册的 Tool、Context Contributor、Settings section 等 contribution 同样必须先暂存于
root EffectScope；setup 完整返回、callback 激活并进入 activation commit 后才可发布给 Core。setup 回滚时
不得向 Core 暴露暂存 contribution，也不得发出无意义的 unregister。

setup 中发生任何异常、Service 冲突或取消时，Kernel 必须先完整 dispose root scope，再进入与原因对应的
`failed`、`conflict`、`waiting` 或 `disabled`。active 插件后来触发 runtime Service conflict 时同样先
dispose 整个 root scope，再进入 `conflict`。插件不得暴露半激活的 Service、Handler、thread 或子进程。

### 4.2 响应式依赖

```python
def use_weather(weather, scope):
    scope.on("com.example.weather.changed", on_weather_changed)

ctx.inject("com.example.weather", use_weather)
```

- required 依赖控制整个插件激活：Service 消失时按依赖图逆序 dispose Consumer，并进入 `waiting`；
  Service 恢复后按依赖顺序重新 setup。
- `inject()` 主要用于 optional/dynamic Service。Service 出现时执行 setup，并提供 child Effect scope；Service
  消失时自动 dispose 该 scope，再次出现时以新实例重新 setup。
- 不引入 Service Proxy、自动转发或 stale-object 修复。插件不得把 inject scope 中取得的对象泄漏到 scope
  之外长期持有。

### 4.3 Event 与 Transform

```python
ctx.on("com.example.weather.changed", handler)
ctx.emit("com.example.weather.changed", {"raining": True})

ctx.on_transform("message.before_send", translate)
text = ctx.transform("message.before_send", text)
```

- `on()` 只在 Event Registry 注册事实通知 Handler；`on_transform()` 只在 Transform Registry 注册转换
  Handler。二者都自动绑定 Effect，同名 Event 与 Transform 也不会互相调用。
- `emit()` 表达已经发生的事实；Handler 返回值被忽略，一个 Handler 失败不阻止后续通知。
- `transform()` 只调用 Transform Handler，并按注册顺序把上一个有效值交给下一个 Handler。Handler 必须
  把输入视为只读并返回新值；原地修改不属于受支持语义。Host Transform DTO 应使用 immutable/frozen
  形态。Handler 失败时保留上一个有效值并继续，Kernel 不为任意对象提供通用 deep copy。
- 第一阶段没有 priority、phase、capture、bubble、cancel 或 stopPropagation。
- 普通插件可自由 emit 自己命名空间的事件，但不得 emit `sakura.host.*`。Host 保留该命名空间以保证 Host
  Event 的事实来源；用户消息、角色变化和 Session 开始/结束等 Host 确认事实必须命名为
  `sakura.host.message.received`、`sakura.host.character.changed`、`sakura.host.session.started/ended` 等。
  真正的宿主副作用必须调用 Host Service，而不是伪造事件。Transform Hook 不是已发生事实，不要求使用
  Host Event 命名空间。

### 4.4 Effect 与 Config

`provide()`、`on()`、`on_transform()`、`inject()` 和 Host 注册行为都自动成为 Effect。插件对 timer、
thread、文件句柄、socket、子进程等自有资源使用 `ctx.effect(cleanup)`；cleanup 必须幂等。

Plugin API v3 的公开资源生命周期只有 Effect，不把 `plugin.shutdown()` 作为第二套正式 API。过渡期若加载器
发现已有插件实现了该方法，只将其视为兼容 hook：停用或 setup 回滚时先调用该 hook，再逆序 dispose root
Effects；hook 抛错不得阻止 Effect 清理，hook 卡死则由 Worker deadline 与进程树回收兜底。

```python
current = ctx.config.get()
ctx.config.on_change(handle_config)
```

- Config 使用插件 ID 命名空间并原子保存；代码安装目录与 Config/Data 目录分离。
- `config.save()` 与 `config.update()` 都对用户 override 文档执行顶层 merge，适合 Settings section 的局部
  提交；只有显式 `config.replace()` 才整份替换用户 override 文档。
- 保存完成后以合并后的完整有效配置调用 `on_change`，Handler 返回 `applied`、`restart_required` 或
  `error`。
- 没有 Handler 的插件默认为 `restart_required`；Kernel 不因每次保存自动 reload 插件。
- `error` 表示文件已保存但运行时未应用。插件应尽量继续使用旧运行对象；设置页必须同时展示保存状态、
  应用状态和稳定错误，不得声称已经生效。
- 用户显式 reload 时，按 required 依赖逆序 dispose、重载目标插件、再正序恢复 Consumer。

## 5. 生命周期与用户状态

插件公开状态固定为：

| 状态 | 语义 |
|---|---|
| `disabled` | 用户 desired state 为禁用 |
| `waiting` | required Service 缺失 |
| `active` | setup 完成且 Service/Effects 已发布 |
| `failed` | manifest、依赖环、导入、setup 或运行时恢复失败 |
| `conflict` | declared 或 runtime Service 唯一性冲突 |

- 插件管理页必须显示人类可读原因、缺失 Service、冲突提供者，以及已安装但未启用的可能提供插件。
- Kernel 不使用 priority 或加载顺序自动选择冲突 Service。
- 禁用前先原子保存最新 desired state，再逆序 dispose required Consumers 和目标 Provider。
- 启用后按依赖顺序激活；required Service 实际发布前 Consumer 不得进入 `active`。
- shutdown/reload 超时使 Core 终止并重建整个 Plugin Worker。新 Worker 必须读取最新 desired state，不能
  重新加载刚被禁用的故障插件。
- Worker 重建可以暂时中断全部插件能力，但不得替换 Core generation、阻塞普通 Core control 或遗留后代。

## 6. Generic Bridge 与 Host Service

Bridge 只允许以下机制族，不包含领域名称：

```text
lifecycle / status
service call / result
host call / result
event emit
hook transform
callback handle invoke
config changed
```

协议定义、enum、router 和通用错误中不得出现 `tts`、`memory`、`weather`、`renderer`、`gpt-sovits`、
`genie` 等领域或实现标识。新增第三方 Service 不得修改 Bridge schema 或 Core allowlist。

跨桥参数与结果必须是有界 JSON-compatible 数据，继续使用 generation/token/request identity、pending 上限、
deadline、取消和脱敏错误。大文件或二进制不进入 JSON/Base64 RPC，而通过 artifacts Service 传递。

Callback 不是任意 RPC：

- 显式 export 的 Service 方法永远通过 `service.call(service_key, method, args)` 调用，不创建 callback handle；
- callback handle 只能在插件把 callable 注册给 Host Service 时创建，例如 Context Contributor、Tool
  Handler、Settings Action 或 Collection query/update/delete；
- handle 绑定 generation、Plugin、Effect 和允许的调用形态；
- Core 只保存 handle，不接收 module/function 名、Python repr、pickle 或裸 callable；
- Effect dispose、插件卸载或 generation 失效立即使 handle 不可调用；
- invoke 继续执行参数大小、deadline、取消、旧 generation 与迟到结果校验。

Bridge 调用方向固定为：Core 调用 Worker export 使用 `service.call`；Worker 调用 Core 能力使用
`host.call`；Core 回调已经注册给 Host 的 Worker callable 使用 `callback.invoke`。同一个 exported Service
方法不得同时通过 callback handle 暴露。

第一阶段 Host Service 仅包括：

- `sakura.host.context`：注册 Context Contributor，最终选择和组装仍在 Host；
- `sakura.host.tools`：向真实 Agent ToolRegistry 注册 descriptor 与 callback handle；
- `sakura.host.settings`：注册声明式页面、字段、Action、状态、进度和 Collection；
- `sakura.host.character`：读取/更新当前插件 extension，并安全解析角色包资源；
- `sakura.host.audio`：消费已提交音频 artifact 并执行现有播放、队列和取消语义；
- `sakura.host.artifacts`：分配、提交和回收 generation-bound 大型/二进制工件。

`sakura.host.session`、`sakura.host.ui` 和其他候选 Host Service 不属于第一阶段。只有出现真实消费者并证明
无法由普通 Service/Event 组合时，才能扩展 Host Service 清单。

`sakura.host.artifacts` 使用 `allocate → Worker 写入 → commit → consumer/release` 生命周期。Artifact 绑定
generation、Plugin 和 root Effect，只有 committed artifact 才能交给其他 Host Service；跨 Bridge 的已提交
descriptor 只包含 opaque ID、media type 和 byte length，不暴露文件路径。Host 对单插件数量、单 artifact
大小、普通文件与 generation cache 路径做结构校验；插件停用、Worker 重建或 generation 关闭时自动回收。

## 7. 设置表层

插件设置通过 `sakura.host.settings` 注册，官方与第三方使用相同 descriptor。第一阶段支持：

- Text、Number、Select、Toggle、Slider、Secret、Path；
- Action/Button、确认、状态和有界进度；
- 受限 Collection：分页、搜索、简单筛选、列定义、schema 表单、create/update/delete、删除确认、
  loading 和 error。

Collection 不支持自定义 HTML/JS/CSS、Cell Renderer、Canvas、Graph、拖拽、任意布局或前端生命周期 Hook。
callback 使用第 6 节的 opaque handle；WebView 不接收 Python callable、插件路径或私有数据目录。

## 8. Character Extension 与资源

Character Core 只保存 JSON-compatible、受总大小限制的 opaque extensions：

```json
{
  "extensions": {
    "sakura.tts": {"provider": "gpt-sovits"},
    "sakura.tts.gpt-sovits": {
      "gpt_model": "voice/a.ckpt",
      "sovits_model": "voice/b.pth",
      "tone_refs": "voice/tone_refs.txt"
    }
  }
}
```

- Kernel 不解释字段名，也不扫描字符串判断哪些值是资源路径。
- 插件只能通过 `sakura.host.character` 获取或更新自己 ID 对应的 extension；TTS Hub 只拥有
  `sakura.tts` 块，Provider 只拥有自己的块。
- Character Host Service 的 `get(character_id)` 只返回调用插件自己的块；`update(character_id, values)` 对该块
  做顶层 merge 并原子写回，同时保留所有 Core 字段和其他插件 extension。单块及 extensions 总体必须有界。
- `resolve_resource(character_id, relative_path)` 在实际使用时拒绝绝对路径、`..` 逃逸、包外 canonical target
  和不允许的 symlink；返回经过验证的角色包内资源。
- 全局 URL、Runtime 路径、超时和本机安装位置属于 Plugin Config，不写入 Character extension。

旧程序 Memory 数据、旧程序目录和外部旧角色包不在本阶段自动扫描或迁移。新版内置角色与测试 fixture
直接采用 extension 格式；后续由独立迁移模块在用户选择旧程序目录后执行显式导入。

## 9. Context Contribution 与 Memory

Memory 没有统一公共 Service 或 Record DTO。插件可监听 `sakura.host.*` 命名空间下由 Host 转发的会话
事实，自行使用 Mem0、向量、图、SQLite、时间线或摘要，并向 `sakura.host.context` 注册 Contributor。

普通 Contribution 最小字段为：

```json
{
  "content": "...",
  "priority": 80,
  "budgetHint": 1200,
  "label": "Long-term memory"
}
```

- 一个 callback 可返回多个 Contribution；`budgetHint` 只是上限建议，不保证分配。
- 所有插件/领域 Contribution 使用统一调度，不按 Memory、Plugin 或实现类型预切固定 token 配额。
- Host 必需运行时事实可以标记 `required`，不与普通 Contribution 竞争；插件不能自行声明 required。
- Host 保留单 Contributor 返回数量、单 Contribution 字符数、callback 总 payload、总动态上下文和 deadline
  上限，防止错误实现耗尽 Worker/Core；这些是结构可靠性边界，不是 Memory 实现协议。
- 插件不能返回完整 Prompt messages、替换 system prompt 或伪造 user/assistant/tool 角色。Host 负责排序、
  裁剪、防注入信封和最终 Prompt 组装，但不判断 Memory 如何存储、检索、总结或 rerank。

官方 Mem0 插件拥有其向量库、embedding、整理模型、进程、配置和 Collection 管理操作。另一个 Memory
插件可以使用完全不同的结构并与 Mem0 同时贡献上下文。

## 10. TTS 插件模型

第一阶段拆分：

```text
Sakura TTS Hub Plugin
  provides/exports: sakura.tts

GPT-SoVITS Provider Plugin
  requires: sakura.tts

Genie Provider Plugin
  requires: sakura.tts
```

Hub 提供 `registerProvider()`、`listProviders()`、`synthesize()`、`stop()` 和状态查询；注册返回 disposer
并自动绑定 Provider Effect。Provider shutdown 时 Effect 调用 disposer；需要提前退出时 Provider 也可自行
调用同一 disposer。第一阶段不冻结按 ID 主动注销的通用 `unregisterProvider()`。Hub 不导入具体 Provider
factory，也不理解其模型、Endpoint 或进程实现。

`synthesize()` 请求必须包含 `character_id` 和 text/options。Hub 按 `character_id` 读取
`extensions["sakura.tts"].provider`，然后只调用选中的 `provider.synthesize(request)`。Hub 不读取、复制或
传递 Provider extension；Provider 持有按自身插件身份 scoped 的 `sakura.host.character`，在
`synthesize()` 内按 `character_id` 读取自己的 extension。第一阶段
不存在影响所有角色的 mutable `selectProvider()`；设置 Provider 等价于更新对应角色的 Hub extension。
每个角色只选择一个 Provider；Provider 不可用或合成失败时返回明确错误，不得按注册顺序、安装顺序或
健康状态静默切换声线。未来 fallback 只能作为 TTS Hub 的显式、角色级有序配置增加。

Provider 自行拥有模型安装、参考音频、Endpoint、健康检查和需要的子进程。它通过
`sakura.host.artifacts` 提交音频工件，Core 再交给 `sakura.host.audio`。ADR-0023 的合成/播放/录音所有权
和 ADR-0024 的 Provider/Endpoint/Managed Runtime 分离继续适用。

## 11. 未知能力验收

必须提供仅用于示例和自动测试的 Weather/Umbrella 插件：

- Weather 声明并提供 `com.example.weather`，发送 `com.example.weather.changed`；
- Umbrella required 依赖 Weather，消费 Service 并监听事件；
- Core 与 Bridge 源码不得 import、引用、分支或 allowlist `com.example.weather`；
- 安装 Weather 后 Service 出现；安装 Umbrella 后两者 active；
- 禁用 Weather 后 Umbrella dispose 并进入 waiting；重新启用后自动恢复并使用新 Service 实例；
- 未声明 runtime conflict、declared conflict、依赖环、Handler 失败和 shutdown hang 有确定状态；
- setup 在注册部分 Event/Service/Effect 后故意冲突或抛错，root EffectScope 仍完整回收且插件从未 active；
- `emit()` 不调用同名 Transform Handler，Transform 对 immutable 输入失败后保留上一个有效值；
- export 只接受 `service.call`，Host callback 只接受 callback handle，两条路径不能互换；
- 删除插件后 Service、Event/Transform Handler、callback handle、Effect、timer、thread 和后代进程归零。

只有以上验证通过，且 TTS 替代 Provider、双 Memory Contributor 证明未引入实现特判后，才评审本文升为
`normative` 和 ADR-0027 升为 `accepted`。

## 12. 非目标与回退

第一阶段不建设 Service 版本治理、Session Service override、权限/签名/沙箱、逐插件 Worker、多语言 SDK、
WASM、Remote Runtime、在线市场、自动更新、依赖下载、自定义 Web UI、图谱 UI 或旧数据自动迁移。

v3 实现不得长期保留 v2/v3 双重业务架构。切换前可在开发分支并存以完成迁移；候选完成时所有内置插件
必须使用 v3，Core 不再依赖 v2 `register_xxx()`。回退实现时保留本文和 ADR 作为未采纳候选历史，恢复
当前 accepted v2 产品链；不得删除插件数据、Character 数据、Memory 数据或用户安装目录。
