# 编写插件

Sakura 使用 Plugin API 4。插件通过公开 SDK 提供服务、工具、上下文、处理钩子和设置，不导入 `app.*` 或其他插件源码。插件是以当前用户权限运行的可信本地代码，可以访问文件和网络。

本文说明通用接口；模型、Assistant、MCP、语音等见[能力接入](plugin-recipes.md)，渲染与编辑器见[角色与表现](characters.md)。公开模块位于 [`app/plugin_sdk/`](../../app/plugin_sdk/)。

安装、进程、服务与资源回收的业务规则见[插件运行规格](../specs/plugin-runtime.md)；配置归属、草稿和保存行为见[设置规格](../specs/settings.md)。

## 最小插件

在同一目录放置 `plugin.yaml`、`plugin.py` 和可选 `config.json`。从设置的插件安装入口安装目录或 ZIP，再启用并应用。

```yaml
api: 4
id: example.greeter
name: 问候工具
version: 1.0.0
entry: plugin:Plugin
enabled: false
provides: []
requires:
  - sakura.host.tools
  - sakura.host.settings
presentation:
  kind: extension
  category: tools
  icon: messages-square
```

`config.json`：

```json
{"greeting": "你好"}
```

`plugin.py`：

```python
class Plugin:
    def setup(self, context):
        context.config.on_change(lambda values: "applied")
        context.get("sakura.host.tools").register(
            {
                "name": "greet_someone",
                "description": "向指定的人打招呼。",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "group": "plugin",
                "risk": "low",
            },
            lambda args: {
                "text": f"{context.config.get()['greeting']}，{args['name']}！"
            },
        )
        context.get("sakura.host.settings").register(
            {
                "sectionId": "general",
                "title": "问候语",
                "fields": [{
                    "key": "greeting", "label": "开场白", "type": "string",
                    "default": "你好", "required": True,
                }],
            },
            load=context.config.get,
            save=lambda values: {
                "applicationState": context.config.update(values)
            },
        )
```

工具每次读取当前配置，因此配置回调可以直接返回 `applied`。有缓存、连接或后台工作的插件应真正更新它们后再返回成功。

## 安装包与依赖

根目录的 `plugin.yaml` 确定插件身份。入口为 `模块:类名`，类能无参构造并提供 `setup(context)`。包内示例可以另有 Manifest，不把它们当作安装入口。

| 字段 | 用途 |
|---|---|
| `api`、`id`、`entry` | 必需；当前 API 为整数 4，ID 稳定且唯一 |
| `name`、`author`、`description`、`version` | 显示名称、作者、用途和版本 |
| `enabled` | 首次安装状态；第三方默认停用 |
| `provides` | setup 必须提供的 Service key |
| `requires` | 启动和失效传播使用的硬依赖 |
| `priority` | 排序值，不用于解决服务冲突 |
| `presentation` | 展示分类和图标，不影响运行与权限 |
| `visuals`、`ttsResources`、`frontendModules` | 对应能力的静态声明 |

`presentation.kind` 为 `extension/provider/infrastructure`，`category` 为 `model/voice/memory/visual/tools/connectivity/other`。`icon` 使用宿主已收录的 Lucide 名称，见 [`icons.js`](../../desktop/frontend/core/icons.js)。缺失或未知展示信息使用默认值，不影响加载。

可选字段 `min_app_version` 声明最低 Sakura 版本，采用 SemVer（如 `1.3.6`）。未声明时不限制主程序版本。下载、安装和加载前都会检查；版本不足时仍显示更新，但禁用下载，不发送插件更新提醒。主程序更新提醒不受影响。API 和服务要求仍单独检查。

依赖按 `requirements.lock`、`requirements.txt`、`pyproject.toml` 的顺序选择；后者可配 `uv.lock`。安装器用 uv 在插件独立暂存环境准备依赖，成功后发布。普通启动不联网安装，不把插件依赖装进主 Python。

默认下载源和显式 uv/PIP 配置由安装器统一处理。插件保留自己的版本约束和专用来源，不在源码中再造安装器。代码和依赖更新保留用户配置、数据与启停选择；可选插件通过插件市场更新，不随主程序替换。安装失败保留原版本与具体原因。

## 生命周期与服务

`setup` 登记能力并及时返回，下载和长期任务放到后台。资源清理交给 `context.effect(cleanup)`，不另设 `shutdown()`。Effect 按反向登记顺序执行，显式 disposer 与退出共用一次清理。关闭应停止并等待自己启动的线程、连接和子进程。

```python
context.provide("example.service", service, exports=("run", "status"))
context.effect(service.close)
result = context.get("other.service").run({"value": 1})
```

`exports` 是唯一方法表，需与 Manifest 的 `provides` 相符。服务参数与结果使用 JSON；大文件用 Artifact，不传 Python 对象、callable、异常对象、句柄或任意本地路径。一个服务只能有一个启用提供者，冲突明确报错，不按优先级暗选实现。

| Context 成员 | 用途 |
|---|---|
| `plugin_id` | 当前插件 ID |
| `get(key)` | 动态调用当前可用服务 |
| `bind(key)` | 固定当前插件服务实例 |
| `provide(key, service, exports=...)` | 发布服务，返回 disposer |
| `on(name, handler)` | 订阅宿主事件，返回 disposer |
| `effect(cleanup)` | 登记资源清理，返回 disposer |
| `config`、`data_path(relative)` | 配置及私有数据 |
| `exception_diagnostics(error, secrets=...)` | 提取清洗后的原始诊断 |
| `caller_id`、`caller_scope` | 当前跨进程调用的真实来源，不自动传播到新线程 |

创建后台任务后，查询、取消和回收必须使用同一绑定：

```python
provider = context.bind("other.jobs")
job_id = provider.begin({"text": "内容"})
state = provider.poll(job_id)
provider.invoke("cancel", job_id, timeout_seconds=1.0)
```

插件停用、崩溃或重载后，旧绑定返回 `SERVICE_BINDING_EXPIRED`，不能转发到同 ID 新进程。Host 服务使用 `get`。普通调用不设统一期限；显式超时不证明任务已经停止，也不能据此自动重放。

插件状态为 `disabled/starting/active/failed`。setup 成功且声明的服务已发布才是 active。失败只停止实际受影响的硬依赖消费者，无关插件继续运行。自己启动的子进程可用公开 `sakura_process.terminate_process_tree(process, timeout=...)` 清理，Shell 保留整树最终停止权。

## 配置与私有数据

```python
values = context.config.get()
state = context.config.update({"enabled": True})
state = context.config.replace({"enabled": True})
context.config.on_change(apply_config)
path = context.data_path("notes.sqlite3")
```

包内 `config.json` 是默认值，用户覆盖保存在插件数据目录。`update` 合并本次字段，`replace` 替换全部覆盖；局部保存通常使用 update。默认值升级不覆盖用户显式配置。`data_path` 使用相对路径，不反推宿主目录。

配置处理器收到完整有效配置，返回 `applied/restart_required/error`。没有处理器时变化返回 restart_required；宿主在本次保存中重启目标及必要消费者。error 表示已写入但应用失败，必须如实呈现。相同且已生效的配置不重复应用，上次失败时仍可明确重试。

## 设置区块、动作与页面

声明 `sakura.host.settings`，用 `register(descriptor, load=..., save=..., actions=...)` 注册。`load` 只读取，`save` 只保存收到的可编辑值。返回应用状态字符串，或 `{"applicationState": "applied"}`。

```python
settings.register({
    "sectionId": "connection", "title": "连接", "order": 100,
    "fields": [
        {"key": "endpoint", "label": "地址", "type": "string", "default": ""},
        {"key": "token", "label": "令牌", "type": "password", "default": ""},
    ],
    "actions": [{"actionId": "test", "label": "测试连接"}],
}, load=load, save=save, actions={"test": test_connection})
```

Action 使用当前草稿，返回 `{}` 或 `{values, message, applicationState}`。`values` 只更新返回的字段，不替代持久化；确需保存的动作自行调用配置接口，并说明后果。所有 Action 都要提供同名回调，当前 `danger` 只支持 false。耗时动作启动后台任务后返回，通过只读状态提供进度、取消和失败。

| 字段类型 | 值及常用选项 |
|---|---|
| `string/password` | 字符串；`required/maxLength/copyable` |
| `boolean` | 布尔值 |
| `integer/number` | 数值；`minimum/maximum/step` |
| `select` | `options: [{label,value}]` |
| `readonly` | 只读文字，可复制 |
| `status` | `{state,label,message}`；state 为 neutral/ready/working/warning/error |
| `resource` | 下载和安装状态，格式见下文 |
| `image` | null 或 `{dataUrl,alt}`，支持内嵌 PNG/JPEG/WebP |
| `data` | 组件使用的 JSON 对象或数组，不生成普通输入框 |

通用属性有 `description`、`readonly`、`placement`（row/advanced/section_header）、`restartRequired`、`unit`、`placeholder`、`tooltip`、`displayDefault`。`enabledWhen: {field, equals, hide?}` 以同区块字符串值控制启用或隐藏，隐藏不清除值。数值字段的 `optionalToggle` 关闭时写入 null。只读字段不进入普通保存参数，密码控件也不会替插件隐藏后端已返回的值。

Resource 值使用：

```json
{
  "applicability": "required", "subtitle": "本地模型", "ready": false,
  "taskState": "running", "message": "正在下载", "detail": "",
  "progress": 35, "availableActionIds": ["cancel"]
}
```

`applicability` 为 required/not_required/unsupported；`taskState` 为 idle/queued/running/succeeded/failed/cancelled，progress 为 null 或 0–100。字段的 `actionIds` 声明可能动作，availableActionIds 只列当前可用项。ready 只表示资源可用，不证明引擎已运行。“关于”的组件总览自动聚合已启用插件的 Resource，不需重复注册。

原生文件选择用 Action 的 `filePicker: {field: "bundlePath", extensions: ["zip","7z"]}`；field 对应同区块可编辑字符串。选择只用于这次动作，取消不执行或保存其他草稿。

未放置区块进入插件设置窗口。要放进功能页：

```python
settings.register_page({
    "pageId": "notes", "title": "笔记", "icon": "sticky-note",
    "group": "behavior", "order": 100, "regions": ["extensions"],
})
settings.place("general", page_id=context.plugin_id + ":notes")
```

页面 ID 为 `pluginId:pageId`，分组为 character/ai/behavior/system。其他插件只能使用明确开放的 region。宿主页使用 `host:character/appearance/providers/model/voice/memory/interaction/tools/plugins/system/about`，开放 content。一个区块只有一个主要编辑位置；移动不改变配置和回调。旧 `surface-v0` 继续兼容，新贡献优先使用页面放置。

区块的 `presentation.component` 可为 form、connection-status、record-table 或 module。form 支持 collapsible/group/alignedUnits。connection-status 绑定 statusField/imageField/actionsField；后者是已声明 Action ID 的数组。record-table 绑定只读 itemsField、可编辑 valueField 和 columns：列表为 `[{id,label,description?,values}]`，草稿为 `{记录ID: {字段:值}}`。可选 visibleField 绑定只读布尔字段。

record-table 的独立读取使用 inspectAction/requestField/resultField，提交 `{id,requestId}`，返回 `{id,requestId,state,title,rows:[{group?,label,value,secondary?}],message?}`。耗时读取返回 running 并声明 statusAction；关闭或对象更换后不回填旧结果。

module 的 source 是包内 ES Module 源码，导出 `mount(context)`，不支持相对导入。返回 `{element,dispose,update?,validate?,cancel?,contributions?,updateContributions?}`，可异步挂载。context 提供 `read/write/readSection/action`、`signal/isCurrent`、`document/window/section`、`invoke/listen`、`notify/onError`、`enhanceSelect/refreshSelect/closeSelects/createIcon`、`importModule`、`modelServices/modelServicePresets` 和 `onCatalogChanged`。模型目录通过 `contributions().modelCatalog` 返回。`standalone: true` 可独占页面区域。具体用法参考[模型连接编辑器](../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js)。

账号类模型服务通过 `contributions().modelServicePresets` 提供添加入口 `{id,label,iconName?,add}`；`add({createdAt})` 每次保存一条新连接及其创建时间，返回新连接的服务项 ID。已添加的连接通过 `contributions().modelServices` 提供 `{id,label,iconName?,createdAt,description,configured,models,element,select?,cancel}`。`iconName` 使用宿主图标名，颜色跟随主题；`models` 为模型 ID 列表；`element` 为右侧管理面板，`select` 刷新选中连接，`cancel` 结束离开连接时的等待。工作台将这些连接与 API 连接按 `createdAt` 混合排列。

只登记服务的模块使用隐藏的根 element，设置区块放在 `host:providers` 并声明 standalone。宿主汇总贡献，OpenAI 兼容模型插件的工作台通过 `context.modelServices()` 和 `context.modelServicePresets()` 读取；状态改变时调用 `onCatalogChanged()`，宿主通知其他模块的 `updateContributions()`。账号、模型同步和持久化仍由提供方负责。基础插件依赖见[模型服务规格](../specs/model-services.md#配置归属)，示例见 [OpenAI Official 账号面板](../../plugins/optional/sakura_codex/frontend/account.js)。

前端模块沿用已安装插件的信任边界，关闭、重载或实例更换时中止 signal 并调用 dispose；迟到挂载也需回收。异步操作检查当前身份，不建立第二套草稿与保存机制。通用插件设置尚未开放任意原生窗口；输入栏工具贡献的公共 callable 注册封装仍未开放。

## Collection

记录列表通过 `sakura.host.settings.collection-v0` 登记到已存在的设置区块：

```python
collections.register("notes", {
    "collectionId": "items", "title": "笔记", "scope": "character",
    "columns": [{"key": "content", "label": "内容", "type": "string"}],
    "fields": [{"key": "content", "label": "内容", "type": "string", "default": "", "required": True}],
    "filters": [], "searchable": True, "pageSize": 25,
    "deleteConfirmation": "确定删除这条笔记吗？",
}, query=query, create=create, update=update, delete=delete)
```

| 回调 | 参数与返回 |
|---|---|
| query | `{cursor,limit,search,filters}` → `{items:[{itemId,values}],nextCursor,total?}` |
| create | values → `{itemId,values}` |
| update | item_id, values → `{itemId,values}` |
| delete | item_id → `{deleted:true}` |

不支持的操作省略。游标由插件定义，itemId 必须稳定；每页最多 100 项。列支持 datetime，数值列可用 `format: "percent"`。写入回调可附 applicationState；restart_required 只提示待应用，Collection 本身不自动重启插件。

scope 为 global/character，省略兼容为 character；它控制草稿行为，数据隔离仍由插件实现。打开编辑器不等于修改，恢复原值后解除未保存状态。角色草稿阻止换角色，全局草稿可保留；“应用”不会替 Collection 保存记录，“保存并关闭”要求先处理未保存记录。角色切换、同角色刷新或实例更换时隔离旧查询、写入回执和删除确认，不自动重放。

## 工具与动态上下文

工具 descriptor 使用 `name/description/parameters/group/risk`，可带 capability。name 全局唯一，risk 为 low/medium/high，parameters 是 JSON Schema。工具回调接收参数对象并返回 JSON；失败可返回带 `isError: true` 的结果，保留具体原因。工具不设统一时限，取消不能撤销已经发生的外部操作。

需要真实消息依据的工具可声明 `invocationContext: true`，回调接收 `(arguments, invocation)`。默认 Assistant 执行时通过第四个参数提供 `humanEntryId/turnId`，宿主从当前角色 Timeline 核对后返回 `characterId/turnId/humanEntry`。没有有效用户消息时 invocation 为空；模型自行填写的来源字段不能充当依据。

Context 使用 `sakura.host.context`：

```python
contexts.register(
    {"providerId": "example.notes", "scope": "turn", "failurePolicy": "skip"},
    lambda request: [{"id": "note", "content": "相关内容", "priority": 60, "budgetHint": 160}],
)
```

`describe()` 返回 schemaVersion 2、scopes step/turn、failurePolicies skip/abort。request 使用 snake_case，常见字段为 current_input、character_id、current_turn_id、recent_messages、available_tools、source、mode、current_time。插件自行组织文本，不声明 kind、trust 或与角色卡的关系；宿主绑定真实来源。

默认 Assistant 对 step 每次组装采集，turn 一轮只采集一次；skip 失败后跳过，abort 终止本轮。片段 required 为 true 时必须完整装入，预算不足明确失败；可选片段按自身 budgetHint 和模型总预算选择。priority 为 0–100，budgetHint 为 1–4096，sensitivity 为 public/private/sensitive。版本不兼容和取消不能按可选失败吞掉。

消费工具和上下文时，使用 `tools.catalog()/execute(registrationId,name,args)`、`contexts.catalog()/collect(registrationId,request)`。保留登记身份，失效后不按同名查找并重放。Context 不自动写历史。

## 处理钩子（Hooks）

插件可以在处理步骤中修改数据或中止当前操作。在 Manifest 的 `requires` 中声明 `sakura.host.hooks`，再登记普通函数：

```python
class Plugin:
    def setup(self, context):
        hooks = context.get("sakura.host.hooks")
        hooks.register(
            {"hook": "model.before_request", "order": 100, "failurePolicy": "skip"},
            self.adjust_request,
        )

    def adjust_request(self, event):
        request = event["data"]["request"]
        request.setdefault("parameters", {})["temperature"] = 0.3
        return {"patch": {"request": request}}
```

`register` 返回撤销函数，也会随插件退出自动撤销。`order` 越小越先执行，默认 100；相同顺序按登记先后执行。一次调用固定当时的处理器列表，后一个处理器读取前一个处理器修改后的数据。

回调收到 `{hook, data, context}`。`data` 是当前步骤的数据副本；返回 `None` 表示保持原值，返回 `{"patch": {...}}` 更新或补充顶层字段，嵌套对象和列表整体替换。字段类型由使用数据的业务接口决定，钩子不按旧值类型限制修改；回调结果中未使用的字段会被忽略。直接改回调参数而不返回 patch 不会生效。`context` 用于读取操作来源和身份，修改它不会改变实际会话、模型绑定或工具目标。

返回 `{"abort": true, "reason": "中止原因"}` 可停止当前步骤及后续处理器。聊天会展示原因，保留已经保存的用户输入；工具执行前中止会返回失败结果，实际工具不会执行。执行后的钩子只能处理结果，已经发生的模型请求、外部动作和费用不会撤销。

`failurePolicy` 默认为 `skip`：回调抛错时记录原始原因，保留此前成功的修改并继续。需要拦截作为执行前提时设为 `abort`，回调故障会向调用方报告。显式返回 abort 始终生效，不受 failurePolicy 影响；运行时不自动重试钩子。

| Hook | 业务读取的 data 字段 | 触发位置与 context |
|---|---|---|
| `chat.before_assistant` | `message` | 输入已写入历史、交给 Assistant 前；context 含 operationId、turnId、characterId、sourcePluginId |
| `chat.before_commit` | `segments` | 回复保存及表现处理前；context 同上，段落字段使用 text、translation、tone、portrait、suppress_tts、control |
| `model.before_request` | `request` | 公共 ModelClient 发请求前；context 含本次模型调用的 operationId、model 引用、callerId |
| `model.after_response` | `response` | ModelClient 解码完整响应后；context 同上 |
| `tool.before_execute` | `arguments` | 宿主执行工具前；context 含 toolName、callerId |
| `tool.after_execute` | `content` | 工具执行结束后；context 另含实际 success、error，修改内容不会改写执行成败 |

输入钩子只影响本轮交给 Assistant 的消息，历史保留用户原文。回复钩子的结果用于历史、字幕和语音；角色和表现目标仍由 Core 校验。直接绕过 ModelClient 调用模型提供方时，不触发模型钩子。模型请求和响应的字段见[能力接入](plugin-recipes.md#使用模型)。

`describe()` 返回内置扩展点和失败策略，`catalog(hook=None)` 返回当前登记的处理器及插件来源。插件也能定义自己的扩展点：`hooks.run("example.plugin.before_save", {"text": text}, {"recordId": record_id})` 返回处理后的 data；名称使用本插件命名空间，调用方负责接续业务操作。

钩子同步执行，应尽快返回；下载和长期任务使用服务任务接口。回调可以调用其他服务，但不要再次触发自身所在的钩子；确需使用模型时，根据 context.callerId 跳过自身请求。大段数据自动通过 Artifact 传递，插件不必自行拆分。停用、重载或撤销后，旧回调结果不会并入当前处理链；聊天取消后的修改不会提交。

## 事件、角色与历史

事件是事实通知，可能丢失，重启不会补发。先订阅再读取当前状态，需要完整数据时保存 Timeline cursor 并补读。需要在步骤完成前修改数据时，使用[处理钩子](#处理钩子hooks)。

| 事件 | 内容或查询入口 |
|---|---|
| sakura.host.character.changed | characterId；character.current() |
| sakura.host.visual.changed | 当前绑定；visual.current() |
| sakura.host.chat.state.changed | sessionId/characterId/characterName/idle；chat.current() |
| sakura.host.chat.completed | characterId/turnId/cursor；Timeline 补读 |
| sakura.host.chat.completed.v2 | 增加 operationId，供发起操作的插件关联 |
| sakura.host.chat.request.started | 用户对话开始，供提前准备资源 |
| sakura.host.tts.started/ended | 实际播放身份及可信的历史段落关联 |
| sakura.host.scope.closed | pluginId/scopeId；跨插件资源服务撤销该调用者 |

`sakura.host.character` 提供 current/list/presentation；get(character_id) 与 update(character_id, values) 只读写本插件的角色扩展。资源通过 resolve_resource 读取，声明和分享见[角色与表现](characters.md)。

`sakura.host.timeline` 提供 latest_cursor、read_recent({limit})、read_since({cursor,limit})、get_entry({entryId})。最近页返回 entries/cursor，增量页返回 entries/nextCursor/hasMore；条目含 entryId/turnId/characterId/kind/origin/createdAt/payload。limit 为 1–500，游标绑定角色，失效时报告 TIMELINE_CURSOR_INVALID，不解析或改写游标。Assistant 的固定快照分页见[能力接入](plugin-recipes.md)。

主动历史整理使用 `read_range({startDate,endDate,cursor?,snapshotCursor?,limit?})`。日期格式为 `YYYY-MM-DD`，按本机时区计算，包含首尾当天；返回 entries/nextCursor/hasMore/snapshotCursor/total。下一页沿用 snapshotCursor，任务开始后新增的记录不会混入本次范围。Context 片段可携带 JSON metadata，用于在 Trace 中保留记忆来源与检索依据。

## Artifact

文件通过 `sakura.host.artifacts` 交换：

```python
artifacts = context.get("sakura.host.artifacts")
allocation = artifacts.allocate({"mediaType": "image/png", "suffix": ".png"})
try:
    render_png(allocation["path"])
    descriptor = artifacts.commit(allocation["artifactId"])
except Exception:
    artifacts.release(allocation["artifactId"])
    raise
```

path 是宿主明确签发的临时写入位置。commit 返回 artifactId/mediaType/byteLength；每插件最多持有 16 项，单项最多 64 MiB。工具图片以 `{content,artifact:descriptor}` 交付，成功后由接收者持有并释放。

接收者用 resolve(artifact_id) 取得授权路径，读完调用 release_received；模型任务的结果文件由提供方在 release 时统一回收。跨插件任务先 bind 接收服务，再 `deliver(artifact_id, provider.identity, operation_id)`；读取时核对宿主签发的 delivery.senderId/senderScope/operationId。发送方可 release_delivered 撤销未消费交付，双方 scope 退出都会回收任务文件。普通工具图片的所有权转交与任务交付不同，不能在交付后由生产者继续删除。

## 开发参考

优先复用[Playwright 工具插件](../../plugins/optional/playwright_browser/plugin.py)、[Mem0](../../plugins/optional/sakura_mem0/plugin.py)、[手机聊天](../../plugins/optional/sakura_mobile/plugin.py)和[对话规则](../../plugins/optional/context_rules/README.md)中的公开接口用法，不跨目录导入其实现。日志见[诊断接入](diagnostics.md)。验证安装、配置保存、实际业务结果及停用清理，使用隔离数据与项目 Python；运行入口见[Harness](../../harness/README.md)。
