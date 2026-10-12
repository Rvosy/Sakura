# 角色资源与表现插件接口

本页说明资源清单、表现服务和前端编辑器接口。角色数据、工坊保存、选择与局部刷新见[角色规格](../specs/characters.md)，播放时序、持续状态与原生表面见[窗口与表现规格](../specs/window-presentation.md)。

## 角色资源清单

表现资源在角色清单中声明：

```json
{
  "visuals": {
    "resources": [{
      "id": "main", "name": "日常形态", "type": "example.model@1",
      "root": "models/main", "entry": "resource.json"
    }],
    "default": "main",
    "providers": {"main": "example.model"}
  }
}
```

name 和 providers 可省略；root 相对角色包根，entry 相对资源根。文件引用使用可移植相对路径，不能读取包外文件。类型名称表示资源格式及版本，与资源 ID、提供者插件 ID 分开。旧立绘包通过立绘适配器读取，不由公共宿主解释表情映射。

## 表现插件

Manifest 在普通服务之外声明前端模块：

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

renderer 必填，editor 可省略；模块位于插件安装目录。无效的可选形态或编辑器只影响对应能力，插件其他合法服务仍可使用。资源缺少兼容提供者或存在多个未选定提供者时显示具体状态，不自动猜选。

通过 `context.provide` 导出以下接口：

| 方法 | 返回内容 |
|---|---|
| `describe(request)` | `{prompt, outputSchema, rendererData, parserData, assets?}` |
| `parseControl(request, parserData, payload, legacy)` | `{state?, actions?}` |
| `editorData(resource, raw)` | 编辑器使用的私有 JSON |
| `exportResource(resource, raw)` | `{entry, data}` |
| `previewImage(resource, raw)`，可选 | 资源内图片相对路径或 null |

request 为 `{characterId, resource: {id,type,root,entry}, segment?}`。插件通过 `sakura.host.character.resolve_resource(characterId, relativePath)` 读取授权文件；assets 把插件自定的 key 映射到包内相对路径，宿主不按 key 名解释业务。校验错误可返回 `{error: "VISUAL_RESOURCE_INVALID", message, field?}`，field 是以 `/` 开头的私有字段路径，工坊用它定位实际输入。

describe 提供模型控制说明和 JSON Schema，宿主组合进完整回复协议。模型 control 形状为 `{version: 1, resourceId, payload}`，payload 由插件定义。解析使用绑定时固定的 parserData，describe/parseControl 不执行动作。缺失控制不丢弃文字回复，也不为可选表现额外请求模型。

渲染模块导出 `mount({container, resource, host, signal})`，返回对象如下：

| 回调 | 责任 |
|---|---|
| `ready`，可选 Promise | 必需资源已经可以显示 |
| `applyState(state, context)` | 应用持续状态，必须提供 |
| `snapshotState()` | 返回可恢复的完整 JSON 状态，可选 |
| `perform(action, context)`、`cancel(context)` | 执行或取消一次性动作，可选 |
| `destroy()` | 释放画布、监听和资源，必须提供 |

host 提供 prepareSurface/setSurface/finishSurface/cancelSurface，支持动态命中时使用 setHitTest；reportError(code, error) 保留原始错误，unavailable 表示无法显示。signal 终止后不得继续更新旧容器或提交表面。

参考[内置立绘](../../plugins/builtin/sakura_portrait/plugin.py)和[可选 Spine](../../plugins/optional/sakura_spine/README.md)。

## 工坊编辑器

编辑模块导出 `mountEditor({container, data, host, signal})`，返回 collect/validate/destroy，可提供 ready、snapshotView/restoreView、focusField。data 的私有键原样传递，不做命名转换。`host.changed(data)` 提交完整未完成草稿，`host.error(error, stage?)` 展示原始错误；`host.importFiles({multiple?, folder?})` 通过宿主选择并复制文件，`host.assetUrl(relativePath)` 取得可读资源，`host.previewImage(relativePath或null)` 更新卡片。

编辑器保持输入法组合输入、焦点与滚动，不因普通刷新重建整个编辑区。样式通过 adoptedStyleSheets 等符合页面 CSP 的方式提供。关闭、切换角色或资源后 signal 失效，旧导入、校验和预览结果不得回写。

## 归档与组件

| 文件 | 内容 |
|---|---|
| `.char` | v2 清单 kind 为 character，完整角色；仍可读取旧 v1 |
| `.visual` | v2 清单 kind 为 resource，manifest.resource 含 type/entry/name?/pluginRequirements，资源放在 resource/ |
| `.voice` | 可独立分享的语音资源 |

插件使用 `character.declare_resources(character_id, {kind: "tts", paths: [...], pluginRequirements: [...]})` 声明自己的语音文件，声明保存于 `extensionResources[pluginId]`。路径相对角色包根。

pluginRequirements 的条目为 `{kind: "visual"或"tts", type: "format@1", plugins: [{id, name?}]}`。type 是资源兼容要求，plugins 给出已知实现。归档保留、共享文件和导入取消规则由[角色规格](../specs/characters.md#分享与归档)统一说明。
