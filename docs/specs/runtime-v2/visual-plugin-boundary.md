---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-12
---

# 表现插件：资源、编辑、控制与渲染

## 范围与所有权

完整立绘链路使用 Plugin API v4 的普通 Service 和安装目录内的前端模块。内置 `sakura.portrait` 负责立绘
资源解释、PNG 校验、标签与选图、控制说明、专属解析、图片渲染、编辑和组件投影。它可以停用或被兼容提供者替代。
宿主负责角色身份、人设、语言与 TTS 语气、公共回复解析、历史、控制目标、实际播放时机、窗口和文件事务。
公共层不要求图片、表情标签或统一动作枚举；没有可用表现时保留角色公共信息和文字聊天。

本文定义已接入的接口。无图片数值插件验证了编辑至播放的链路，不代表 Live2D、VRM 或任意模型引擎已经可用。
实现范围与验证记录见[实施计划](../../plans/character-composition.md)，架构取舍见
[ADR-0046](../../adr/0046-composable-character-resources-and-renderer-host.md)。

## 静态声明与角色资源

```yaml
api: 4
id: example.model
entry: plugin:Plugin
provides: [example.model.control]
requires: [sakura.host.character]
visuals:
  - type: example.model@1
    service: example.model.control
    contract: 1
    renderer: frontend/renderer.js
    editor: frontend/editor.js
```

`visuals` 可省略，最多 32 项，同一安装不能重复声明类型。`type` 是格式及其版本；`service` 必须列于 `provides`；
`contract` 是宿主合同版本，当前支持 1。其他正整数合同保留在 Inventory 中，绑定时报不兼容。
`presentation` 仍仅用于插件列表展示。资源 ID、类型和提供者 ID 各自独立。

`renderer` 必填，`editor` 可省略。模块必须是安装目录内已有的 `.js` 或 `.mjs`，路径各段以 ASCII 字母、数字、
下划线或短横线开头，其余字符限同组字符及点。路径使用 `/`，不含空段、点段、驱动器、网络地址或穿越。
Discovery、Inventory、安装器共用校验，不在发现阶段执行模块。资源数据路径不受模块字符集限制。

新角色清单的表现区如下；最多 32 份资源，同时只选一份：

```json
{
  "visuals": {
    "resources": [{"id":"model-1","name":"日常形态","type":"example.model@1","root":"models/main","entry":"resource.json"}],
    "default":"model-1",
    "providers":{"model-1":"example.model"}
  }
}
```

资源的 `name` 可省略，最长 80 个字符；旧四字段资源继续可读。界面优先显示资源名称，缺省使用插件名称，
均不可用时显示“未命名形态”，不将类型 ID 当作名称。`providers` 可省略。
`root` 相对角色包根，可以为 `.`；`entry` 相对资源根。引用采用可移植相对路径，实际读取时
再次校验解析后的包含关系，禁止包外链接。公共 Profile 持有引用，不持有默认图片、表情映射或选图方法。
资源缺失、未知类型和提供者不可用不阻止加载角色公共信息，实际绑定会报告失败。

没有 `visuals` 的旧 `portrait` 包在兼容入口合成 `portrait-default / sakura.visual.portrait@1 / . / character.json`。
其形态名称统一为“立绘1”；已保存为通用引用的同一旧兼容资源未命名时也使用“立绘1”。用户明确填写的名称保留。
旧路径在立绘适配器中归一化，包括 `./`、反斜杠和未逃逸包根的 `..`；新引用仍严格检查。读取不改写包，
旧实验性 `renderer` 字段仅保留数据，不执行。`.voice` 与现有语音配置行为保持兼容。

## 提供者选择与资源说明

设置的“角色”页在当前角色下显示“显示方式”。列表只包含包内形态，直接显示当前实际选择；已有配置未指定个人
选择时显示包默认形态，不另设“跟随角色默认”选项。选中项用勾号标记，字重与其他选项一致。旁边的“设置”打开
实际提供者的插件设置。插件缺失、停用或不兼容时显示状态并提供“查看插件”，不隐式安装或更换提供者。
进入角色页、窗口重新获得焦点或工坊保存后重新读取状态，保留尚未应用的选择。
同一角色的并发读取共用在途请求，避免页面切换、焦点与目录刷新重复占用请求槽位。

`characters.visuals.get({characterId})` 返回 `{schemaVersion:1, characterId, defaultResourceId,
preferenceResourceId, resources}`；每项资源为 `{id,name,providerId,installId,reasonCode}`。
不返回资源绝对路径或插件私有配置；Rust 和前端检查角色身份、唯一资源 ID 及默认/个人选择的成员关系。

`characters.settings.select` 除 `characterId` 外可带 `visualSelections:{角色ID:资源ID或null}`，
未提供时保留原行为。所有更改先验证后一次写入 `config/characters.yaml` 的 `visual_selections`；
null 清除个人覆盖，恢复跟随角色包的 `visuals.default`。更换角色使用 Core 重启；同角色切换显示方式返回
`visual_rebind`，在当前 Core 内更新资源、控制说明和控制目标，保留 Assistant 和其他插件。候选就绪后才撤销
旧绑定，在途旧回复不能控制新形态。仅改变其他角色的偏好不重启。验证候选使用独立绑定，不撤销当前播放。

选择是设置草稿，“应用”后持久化，“放弃”恢复原值；不改角色包默认项、人设或语音。包更新后，已删除资源的
旧个人覆盖在读取时忽略并回退包默认，不重写旧配置。资源仍存在但插件不可用时保留选择并报告原因。

没有候选返回 `VISUAL_PROVIDER_MISSING`；多个候选返回 `VISUAL_PROVIDER_SELECTION_REQUIRED`，需要明确选择。
同 ID 冲突安装服从 Inventory 的有效 winner。显式指定的插件停用、不兼容或不可用时，不自动改选、安装或联网。
其他常见状态为 `PLUGIN_DISABLED`、`API_VERSION_UNSUPPORTED`、`VISUAL_CONTRACT_UNSUPPORTED`、
`VISUAL_SERVICE_UNAVAILABLE`、`VISUAL_PROVIDER_MISMATCH`、`VISUAL_RESOURCE_INVALID`。

插件通过 `context.provide()` 导出以下方法。参数和结果均为 JSON；插件不得导入 Core 私有代码。

```text
describe(request) -> {prompt, outputSchema, rendererData, parserData, assets?}
parseControl(request, parserData, payload, legacy) -> {state?, actions?}
editorData(resource, raw) -> private JSON
exportResource(resource, raw) -> {entry, data}
previewImage(resource, raw) -> resource-relative image path | null  // optional
request = {characterId, resource:{id,type,root,entry}, segment?}
```

`describe` 通过 `sakura.host.character.resolve_resource(characterId, relativePath)` 读取文件。`assets` 是
`key -> 角色包内相对路径`，最多 256 项；它列出供渲染或组件导出的文件。宿主不解释资源格式。
资产 key 与 rendererData 内部键属于插件语义，`secret`、`token` 等名称可以作为资源标识；不按宿主敏感字段名拒绝。
该例外只适用于表现 DTO 的私有字典，资产值仍须通过包内路径校验，其他 Snapshot 字段继续检查。
说明总 JSON 最多 64 KiB，`prompt` 最多 16,384 个字符，`outputSchema` 必须是对象。
宿主把私有载荷说明和 Schema 组合进公共提示词，不执行 Schema 中的外部引用。

绑定保存描述的独立副本，提示词与解析使用同一份 `parserData` 快照；重新绑定才更新。解析时 `request.segment`
提供本段公共信息，例如 TTS `tone`。`describe` 与 `parseControl` 不执行动作；进程级资源仍使用 v4 effect 回收。
Service 调用前后检查 provider 与进程 scope，清理或重启后的在途结果返回 `VISUAL_BINDING_EXPIRED`。

## 回复、历史与执行时机

模型只输出资源目标和私有载荷：

```json
{"version":1,"resourceId":"model-1","payload":{"angle":12.5,"wave":true}}
```

宿主校验 version、resourceId 和顶层字段，插件解析 payload。插件返回持续状态 `state`、一次动作列表 `actions`，
或两者；至少有一个字段，动作最多 32 项。结果必须是有限 JSON，最多 64 KiB，不允许插件指定 bindingId。
宿主补上绑定身份后写入 segment 的可选 `control`：

```json
{"version":1,"resourceId":"model-1","bindingId":"32位随机十六进制ID","state":{"angle":12.5},"actions":[{"wave":true}]}
```

普通聊天、工具回复与主动事件使用相同处理路径。公共文字、翻译、TTS tone 和 suppressTts 独立于表现控制。
非法或过期控制只剥离控制，不丢文字、语音或整条历史。单条 Timeline 的紧凑 JSON 上限仍为 256 KiB；
当可选控制累计超预算时剥离该回复的全部 control，保留原有文字片段。

旧 `portrait` 继续作为兼容数据读取。没有新封装时交由所选插件解释；存在非法新封装时也不退回旧值。
内置立绘插件按“显式 key/旧 portrait → 本段 tone 对应图片 → 默认图”选择。其他插件可以拒绝旧字段。
旧历史不批量重写。历史页只投影可读文字；浏览、分页、UI 重绘和 `chat.completed` 不执行控制。

宿主在 TTS 确认开始播放、或静音段开始展示时派发该段控制；不是合成开始时派发。同一 operation 的相同
segmentIndex 最多执行一次。取消先中止 operation signal，再调用插件 cancel；新 generation、资源绑定、
插件停用或重载撤销旧目标。首次 lifecycle 公告没有旧 generation，不得销毁刚完成初始挂载的实例。

## 前端挂载与原生服务

```javascript
export function mount({ container, resource, host, signal }) {
  return { ready, applyState(state, context), perform(action, context), cancel(context), destroy() };
}
```

`resource` 是当前 VisualPresentation，含 bindingId、resourceId、type、providerId、私有 data 和受控 assets URL。
`ready` 可以是 Promise；`applyState`、`destroy` 必须存在，其他回调按需要实现。控制 context 包含
`operationId`、`segmentIndex` 与 operation `signal`。挂载 signal 表示整份绑定的生命期。
RendererHost 限制模块加载、mount 和 ready 等待各为 10 秒，销毁迟到实例，并隔离旧回调及宿主服务调用。
切换 generation 或形态时先撤销旧控制，保留旧实例的静态画面；新实例完成资源加载和 ready 后再替换并销毁旧实例。
插件负责停止自己的动画、计时器、监听和资源；长异步工作在 await 后复核 signal。

宿主提供以下技术服务，不承担图片选择：

```text
prepareSurface({assetKey}) -> boolean
setSurface({assetKey?, width, height}) -> boolean
finishSurface() -> boolean
cancelSurface()
reportError(reasonCode)
unavailable(reasonCode)
```

尺寸为 1–8192 的整数。不提供 assetKey 时使用矩形表面；提供 key 时由原生 PNG alpha-mask 原语生成命中数据。
宿主将挂载容器按声明的表面比例放入角色区域，底部居中，并在容器上统一应用个人缩放；插件不重复应用该缩放。
宿主保留 DPI、拖拽、窗口裁剪与透明穿透，视觉容器与命中区域采用相同尺寸和缩放。取消时恢复最后已提交的表面；
晚到的准备或提交不能更换画面。内置立绘插件自行创建图片 DOM、解码、缓存和交叉淡入，并在取消或提交失败时清理过渡层。

`reportError` 记录局部失败，保留已提交的画面与命中区域。RendererHost 同样将控制执行异常作为局部失败处理，
后续片段仍可执行。`unavailable` 用于无法继续显示的情况，显示宿主占位；初始挂载失败也进入该路径。
单次切图解码或提交失败不得遮住已有画面，重新选择当前图片仍保持可见。

Core 的 `CharacterPresentation` 使用 schemaVersion 2：角色公共信息、主题、可空 visual 和 visualReasonCode。
启动时的 `VISUAL_NOT_BOUND` 表示插件尚未完成绑定，前端沿用现有就绪等待，不把它显示为失败；收到资源缺失、
插件停用或渲染失败等确定结果时才显示不可用状态，诊断使用现有运行日志事件并携带稳定错误码。
Rust 将资源 URL 编码为 `/v1/{hexGeneration}/{bindingId}-{hexAssetKey}`，模块为
`/module/{hexGeneration}/{bindingId}/{安装内相对模块路径}`，通过 `sakura-character` 协议提供。
WebView 不接收安装绝对路径；模块只能来自已安装插件，角色包里的 JavaScript 不会作为模块加载。
普通资产限 64 MiB，模块限 4 MiB；PNG 命中服务另有限定尺寸、解码预算和小容量缓存。
URL 随 generation 与绑定失效；同 generation 的插件重绑同样撤销旧目标。CSP 不允许 eval 或运行时 CDN。
前端模块是可信插件代码，共享 WebView 权限，不是恶意 JavaScript 沙箱。

## 工坊与文件交换

```javascript
export function mountEditor({ container, data, host, signal }) {
  return { collect(), validate(), destroy() };
}
```

私有 data 不经过 snake/camel 转换。编辑器通过 `host.changed(data)` 更新该资源草稿，`host.error(error)` 显示错误，
`host.importFiles({multiple?,folder?})` 使用宿主文件选择和复制，`host.assetUrl(relativePath)` 获取资源文件 URL，可供图片或模型文件读取。
工坊打开资源列表时，通过 `studio.visual.previews` 一次获取各插件可选的 `previewImage(resource, raw)` 结果，
不需要先打开各形态的编辑器，也不调用 describe 或挂载渲染器。参数与 editorData 相同；结果是资源根内的
PNG、JPEG、WebP、GIF 相对路径或 null，单图上限 20 MiB。未导出该方法、图片缺失或插件不可用时显示占位图标，
不影响其他形态。Core 校验路径，Rust 复用工坊媒体预览协议注册图片，绝对路径不传入 WebView。
立绘插件返回默认图；Live2D、3D 等插件可以返回包内模型截图，不要求宿主实时生成模型快照。
编辑期间可调用 `host.previewImage(relativePath或null)` 更新当前卡片；只接受本编辑器最后一次请求的结果，
跨编辑器或工作区的旧回包不能更新封面。同资源的封面路径未变时保留已加载的图片节点，不因编辑器地址换绑而重载；
封面变化时先解码新图片，再替换旧图。异步初始列表不能覆盖编辑期间的新封面。
打开编辑器时，Core 将草稿资源根交给 Rust；Rust 校验其位于草稿目录内，再向前端返回绑定范围内的 `assetBaseUrl`。
`host.assetUrl` 在本地生成稳定 URL 并复用同路径结果，图片和模型文件直接经原生资源协议读取，不逐张请求 Core，
也不占用草稿写锁。协议复核 generation、编辑器授权和路径包含关系，单文件上限 64 MiB。
路径编码为 `/editor-assets/{hexGeneration}/{bindingId}/{hexUtf8RelativePath}`；JSON 返回 application/json，
未知格式返回 application/octet-stream，并启用 nosniff，包内脚本不能作为模块执行。
导入结果含资源相对 resourcePath、原 name；小型文本文件可提供 text，由插件解释。宿主不解析立绘标签文件。
目录导入保留文件名、大小写和子目录关系，每次使用独立导入目录避免覆盖已有文件；拒绝符号链接和目录联接，
最多 512 个文件、512 个目录、32 层目录、总计 256 MiB。取消或失败清理本次导入，保留原草稿资源。
`collect` 返回私有数据，修改时必须调用 changed；`validate` 返回布尔值或抛出可读错误。模块或挂载超时为 10 秒。
桌面 CSP 不允许动态内联 `<style>`。插件可使用 `CSSStyleSheet.replaceSync` 与 `document.adoptedStyleSheets`
安装有作用域的样式，在 destroy 时移除。signal 中止时停止异步工作，保留静态画面和样式，供宿主等待新实例就绪。
编辑器可返回 `ready` Promise；宿主等待它完成后替换旧内容，模块、挂载和 ready 超时均为 10 秒。

工坊使用 `studio.visual.catalog/previews/open/create/import/export`。公共外壳管理资源列表、默认项、提供者选择、
移除和保存；图片标签、导入目录中的 `立绘说明.txt`/`description.txt` 等由立绘编辑器处理。
正式“角色形态”页使用形态卡片、名称与默认标记；选中正在编辑的形态不改变包默认。“添加形态”弹窗按已安装
插件列出可创建类型并填写名称。名称写入当前草稿中的资源对象，自动保存返回新对象后仍可继续修改。
立绘编辑器显示图片缩略图、默认单选、标签、文件名、替换和移除，缩略图可打开大图预览。
旧包的默认图片未出现在表情映射中时，编辑器补出使用未占用标签的默认行，保留原有表情标签及图片路径。
空标签或重复标签仍须修正后才能发布；未完成的行可保存在草稿中。
入口缺失时 editorData 收到 null，允许通过编辑重建；包外路径仍拒绝。
切换编辑器或工作区中止旧 signal，迟到结果不能写入新草稿；填充整份表单期间不自动保存。
添加资源、导入和导出期间锁定角色切换，完成后复核工作区。
重复选择当前形态保留原编辑器；更换默认立绘、修改标签或名称只更新相关控件和封面。
保存前冻结旧编辑器，保留静态内容；若原生端返回 `runtimeReload: requested`，工坊等待重启结果，再准备并替换
编辑器。结果事件可能先于保存回包，仍须接收。重启失败保留已保存结果并提示，不将失败写成编辑器就绪。
忙碌期间暂停编辑器状态轮询，避免保存、重启和后台探测互相争用请求槽位。

catalog 返回编辑器提供者的 scopeId，open 返回 providerScopeId。工坊在编辑器打开期间每秒检查状态；只有成功
返回的 catalog 证明 scope 失效时才撤销编辑器及其原生授权。模块加载不再重复同步请求 catalog，查询超时或拥堵
保留编辑区，等待后续检查。切换工作区、替换编辑器或关闭窗口撤销旧模块和资源授权。
成功的目录状态查询只记入调试日志，不占用运行日志列表；失败和超时仍保留原日志级别。
检查不是内容摘要，也不改变资源文件。插件不可用时允许继续保存公共资料，并保留私有资源。

私有草稿保存在 visualData；显式编辑旧内联立绘时才生成 `visuals/{resourceId}.json` 并更新该引用。
公共 manifest、card.md 与 voice 不是表现编辑器的写入目标。新资源由 editorData 产生初始配置。
立绘标签为空或重复时，私有草稿通过 `expressionRows` 保留完整图片行；切换和重开不丢行。修正后恢复普通
`expressions` 映射并移除该草稿字段。立绘服务拒绝把未完成的 `expressionRows` 当作可运行资源；插件可用时
发布校验也会拒绝它，插件不可用时仍遵循保留私有数据的规则。
保存和发布复用 Core 唯一写入者、staging、备份、恢复与取消事务；插件只提供类型数据。
普通保存保留未知 manifest 字段，通用清理不根据私有 JSON 猜测并删除资源文件。

完整新包沿用 `.char`，manifest version 2、kind `character`；旧 version 1 继续导入。
形态组件使用 `.visual` 后缀、version 2、kind `resource`，manifest 的 resource 为 `{type,entry,name?,pluginRequirements?}`，文件在 `resource/` 下；
可选名称随导出、导入保留，旧版 `.char` 形态组件仍可读取。导出统一写 `.visual`，完整角色保持 `.char`，语音包保持 `.voice`。
原生“导入形态”选择器提供 `.visual` 与旧版 `.char` 过滤项；归档内容仍按 format/version/kind 校验，改后缀不能把完整角色变成形态组件。
组件入口由 exportResource 投影，附件由 describe.assets 声明；导入生成新资源 ID，仅加入目标角色草稿并设为默认。
不覆盖目标人格、voice、角色 ID 或已安装包。未知类型组件仍可保存并随完整角色包转交，缺失插件不触发隐式安装。
组件可声明 `resource.pluginRequirements`，完整角色在 `character.pluginRequirements` 汇总需求；格式和检测规则见下节。
宿主按资源 `type` 匹配能力，插件 ID 是安装建议；完整角色的 `visuals.providers` 仍可指定实际提供者 ID。
缺少匹配插件时，工坊保留名称、配置和所有文件，并提示“尚未安装支持此形态的插件”；不能编辑、渲染或通过插件单独导出该形态。
导入仍将新形态设为包默认；保存后若实际选中它，绑定返回 `VISUAL_PROVIDER_MISSING`、visual 为 null，不自动回退其他形态。
用户可以选择已有可用形态，或安装并启用兼容插件后重新打开，无需重复导入。插件已安装但停用时单独提示未启用。
ZIP 穿越、重复路径、符号链接与超限归档被拒绝；中途取消清理临时文件，提交前最后一次取消检查防止覆盖既有导出。

## 角色包插件需求

完整角色的 `character.json` 可包含 `pluginRequirements` 数组，`.char` 将它保存在 `manifest.character`。
每项声明一种资源格式及建议安装的插件，例如：

```json
{
  "pluginRequirements": [
    {
      "kind": "tts",
      "type": "gpt-sovits.models@1",
      "plugins": [
        {"id": "sakura.tts.gpt-sovits", "name": "GPT-SoVITS"},
        {"id": "sakura.tts.genie", "name": "Genie"}
      ]
    }
  ]
}
```

`kind` 为 `visual` 或 `tts`，`type` 采用与表现资源相同的 `名称@格式版本` 规则。
每个包最多 64 项需求，每项最多 16 个插件建议；建议只含 `id` 和可选 `name`，不携带下载地址或执行指令。
同一 kind/type 合并为一项，建议 ID 去重。不同类型分别报告，允许为后续多 TTS 资源保留声明；本次不新增多模型播放流程。
这不是插件启动依赖列表：满足同一资源类型的任一兼容插件即可，包括未列入建议的第三方实现，无需安装全部建议插件。

插件在 `plugin.yaml` 的 `ttsResources` 声明可直接读取或转换的语音格式，最多 32 项。GPT-SoVITS 声明
`gpt-sovits.models@1`；Genie 同时声明该类型和 `genie.onnx@1`。形态继续使用已有 `visuals[].type`。
检测只读取插件 inventory，不启动服务、不转换模型、不选择引擎，也不改变用户配置。

工坊保存、包导出与需求查询会汇总顶层和形态内的声明，并从现有共享 GPT/SoVITS 模型对推导 TTS 需求。
因此旧包没有声明时也能得到基础提示；只有 `.pth`、缺少 `.ckpt` 时不能据此推断完整模型对可用。
显式 Genie ONNX 路径推导 `genie.onnx@1`；未知 extension 不推断为必装插件。
`.visual` 的需求放在 `manifest.resource.pluginRequirements`，只能声明本资源的 visual/type；导入、保存与再导出均保留。
`.voice` 的需求放在 `manifest.pluginRequirements`，只允许 TTS；当前导出仅声明实际携带的共享语音资源。
语音导入替换共享资源需求，保留形态和其他语音资源声明；不含语音的角色导出移除 TTS 需求。

`studio.plugin.requirements` 接受 `{workspaceId}`，返回 `{schemaVersion:1, items}`。
每项带原始声明、`reasonCode` 和候选插件的 `id/name/enabled/compatible/installId`。
状态为 `COMPATIBLE`、`PLUGIN_DISABLED`、`PLUGIN_INCOMPATIBLE` 或 `PLUGIN_MISSING`，分别表示已启用兼容插件、
兼容插件停用、已安装候选不兼容或未安装候选。工坊基础信息显示这些状态；设置页完成角色包或语音包导入后提示未满足的需求。
资源原样保留，缺失插件不阻止导入、保存或完整包转交。安装并启用插件后重新打开角色即可重新检查，无需重导入。

`COMPATIBLE` 只说明安装的插件声明支持该格式，不保证模型文件完整、服务已就绪或转换一定成功。
只有 GPT-SoVITS 原始 `.ckpt` + `.pth`、没有 ONNX 的角色包，在 Genie 已安装并启用时应显示兼容；
用户选择 Genie 后，现有语音准备流程按需转换。转换依赖、模型版本、参考语音等实际问题仍在准备或合成时报告。
形态编辑、渲染和 TTS 启用/选择继续遵循各自现有流程，需求查询不替代运行时检查。

## 失败诊断

表现插件复用统一运行日志的原始异常链路。主窗口、设置和工坊（`studio`）均可提交前端诊断；
前端捕获异常时可提交受控 `stage` 字段，原生日志保留并在详情中展示。绑定、预览和控制解析失败记录原因码、失败阶段、
脱敏后的原始异常与调用栈；控制失败只丢弃当前控制，保留文字和语音。原生模块、资源文件读取与
图片解码失败保留底层错误，资源协议的失败响应仍只返回稳定错误码。导入、导出及资源创建的
清理失败记录为 `recovery_diagnostic`，不得覆盖最初的失败。

渲染器可调用 `host.reportError(code, error)`，编辑器可调用 `host.error(error, stage?)` 交出原始异常。
宿主在加载模块、挂载、等待就绪、执行动作、回收实例与生成缩略图时使用同一前端诊断入口，保留
异常对象到日志边界后再脱敏、截断。短提示与可展开的诊断详情分别承担操作反馈和故障定位。

未绑定、插件未启用、可选封面未提供与旧 signal 的迟到结果不视为执行错误。实际封面失败仍记录，
但不阻止其他卡片加载或编辑。成功的 catalog 轮询只写调试日志；反复读取同一次绑定失败的快照
不重复报告。工坊没有入口 JSON 或入口不是 JSON 时仍可由插件重建，通用宿主不据此判定资源损坏。

## 验证入口

- `runtime/python.exe -m harness run journey-visuals`：资源、插件进程、回复、历史、归档和前端生命周期回归。
- `runtime/python.exe -m harness run journey-visuals-browser`：实际工坊与真实数值插件编辑器、普通/主动回复、浏览器状态/动作、取消和旧目标拒绝；使用生产样式验证非图片缩放与命中几何，并覆盖立绘异步取消、解码和提交失败后的画面保持。Windows 默认使用已安装 Edge，其他平台使用 Playwright Chromium，可用 `SAKURA_BROWSER_CHANNEL` 指定通道。
- `runtime/python.exe -m harness run docs`：文档检查。

浏览器 journey 使用隔离临时角色与真实 Core Boundary；替换了 Tauri invoke 传输和窗口表面服务。
该浏览器 profile 还包含两个独立入口：`settings-visuals.journey.py` 验证正式角色设置的选择、连续应用、
放弃和状态刷新；`studio-visuals.journey.py` 验证卡片、命名、添加、立绘编辑、草稿恢复、保存和三种窗口宽度。
设置测试加载正式角色功能与提交锁定逻辑，不初始化其他无关设置模块。
`studio-save.journey.py` 使用桌面 CSP、40 张立绘及真实有界 `ConcurrentHostRouter` 验证多图预览、保存后布局，
并覆盖重启结果早于或晚于保存回包；这里的原生重启事件仍由测试桥模拟，不代表启动了真实 Tauri 窗口。
Rust 单独验证资源协议、模块授权与 PNG 透明命中；这些证据不替代真实桌面 DPI、多显示器和穿透体验验收。
