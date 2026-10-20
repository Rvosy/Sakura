# 编写插件

Sakura 使用 Plugin API 4。插件通过公开 SDK 提供服务、工具、上下文和设置，不导入 `app.*` 或其他插件源码。插件是以当前用户权限运行的可信本地代码，可以访问文件和网络。

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

`presentation.kind` 为 `extension/provider/infrastructure`，`category` 为 `model/voice/memory/visual/tools/connectivity/other`。`icon` 使用宿主已收录的 Lucide 名称，见 [`icons.js`](../../desktop/frontend/core/icons.js)。缺失或未知展示信息使用默认值，不影响加载。不要以主程序版本号限制安装；兼容性依据实际 API 和服务。

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

module 的 source 是包内 ES Module 源码，导出 `mount(context)`，不支持相对导入。返回 `{element,dispose,update?,validate?,cancel?,contributions?}`，可异步挂载。context 提供 `read/write/readSection/action`、`signal/isCurrent`、`document/window/section`、`invoke/listen`、`notify/onError`、`enhanceSelect/refreshSelect/closeSelects/createIcon`、`importModule` 和 `onCatalogChanged`。模型目录草稿通过 `contributions().modelCatalog` 返回。`standalone: true` 可独占页面区域。具体用法参考[模型连接编辑器](../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js)。

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
| transfer | `{action,path,format,expectedCharacterId,identityPath}` → 导出结果或导入计数 |

不支持的操作省略。游标由插件定义，itemId 必须稳定；每页最多 100 项。列支持 datetime，数值列可用 `format: "percent"`。写入回调可附 applicationState；restart_required 只提示待应用，Collection 本身不自动重启插件。

角色 Collection 可传入 `transfer=callback` 提供导入导出按钮。`action` 为 export、preview 或 import，`format` 为 json 或 txt；path 来自文件选择窗口。preview 只返回合并预览，import 保留本地冲突记录，均返回 `{added,identical,conflicts,sourceCharacterId}`；export 返回 `{exported,path}`。操作固定 expectedCharacterId，插件检查当前角色后处理自己的数据。

公共模块 `sakura_data_transfer` 提供 `read_archive`、`write_archive` 和 `plan_merge`。JSON 使用 `format: "sakura-data"`、`version: 1`，包含 exportedAt、kind（history/memory）、provider、characterId 和 records。`read_archive` 返回文件对象；跨角色复制使用宿主提供的 identityPath 保存 ID 映射，记忆的来源条目和轮次引用与聊天共用映射。文件读写由数据所属插件执行，大批正文不经过 RPC 消息。

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

## 事件、角色与历史

事件是事实通知，可能丢失，重启不会补发。先订阅再读取当前状态，需要完整数据时保存 Timeline cursor 并补读。

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

接收者用 resolve(artifact_id) 取得授权路径，读完调用 release_received。跨插件任务先 bind 接收服务，再 `deliver(artifact_id, provider.identity, operation_id)`；读取时核对宿主签发的 delivery.senderId/senderScope/operationId。发送方可 release_delivered 撤销未消费交付，双方 scope 退出都会回收任务文件。普通工具图片的所有权转交与任务交付不同，不能在交付后由生产者继续删除。

## 开发参考

优先复用[Playwright 工具插件](../../plugins/optional/playwright_browser/plugin.py)、[Mem0](../../plugins/optional/sakura_mem0/plugin.py)和[手机聊天](../../plugins/optional/sakura_mobile/plugin.py)中的公开接口用法，不跨目录导入其实现。日志见[诊断接入](diagnostics.md)。验证安装、配置保存、实际业务结果及停用清理，使用隔离数据与项目 Python；运行入口见[Harness](../../harness/README.md)。
