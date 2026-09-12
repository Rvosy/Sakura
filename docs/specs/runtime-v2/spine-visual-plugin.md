---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-11
---

# Spine 表现插件

## 范围

`plugins/optional/sakura_spine` 实现 Spine 3.6 JSON 表现，插件 ID 与 Service 均为 `sakura.visual.spine`，
资源类型为 `spine.json@1`。后端遵循[表现插件边界](visual-plugin-boundary.md)，前端模块可由本地预览工具调用。
本规范只定义该插件自己的数据和模块行为。正式 Assistant、桌宠、工坊尚未挂载此插件，
这里的前端方法不提前冻结公共 RendererHost、播放协议或 `character.json` 新格式。

## 资源配置

资源引用的 `entry` 指向 UTF-8 JSON 配置，所有路径相对该资源根，使用 `/`：

```json
{
  "version": 1,
  "skeleton": "room/room.json",
  "atlas": "room/room.atlas.txt",
  "defaultSkin": "normal",
  "defaultAnimation": "idle",
  "speed": 1,
  "modelControls": ["skin"],
  "premultipliedAlpha": false
}
```

`version`、`skeleton`、`atlas` 必填，其余字段可省略。默认循环优先选 `idle`，否则选第一项；
默认皮肤优先选 `default`，否则选第一项；默认速度为 1，默认不使用预乘 alpha。
示例资源导入工具遇到 `normal` 皮肤会显式设为初始皮肤，避免把仅含共享部件的 `default` 当作完整表情。
配置只接受上述字段。皮肤和动画名必须存在，速度是 0.1–3 的有限数，布尔值不作为数字接受。

`modelControls` 是当前资源开放给模型的字段列表，只能包含下表中的字段，不允许重复。
省略时，只有一个动画的资源默认只开放 `skin`；多动画资源保留四个字段。作者也可显式选择子集，
空列表表示自动播放、没有模型控制。这个选择不依赖角色编号、目录名或 Room 命名。

Room 配置使用 `modelControls: ["skin"]`，始终循环 `defaultAnimation`，默认速度为 1。
速度保留在角色工坊的 Spine 编辑区，随资源草稿保存；不增加全局设置项。

骨骼必须是完整的 `3.6.x` JSON，包含非空 bones、skins、animations；元数据缩略文件不可用。
插件检查图集页面及其贴图是否存在，只接受资源内 PNG/JPEG 路径，禁止路径穿越、绝对路径、URL 和 NTFS 数据流。
骨骼最多 16 MiB，图集最多 2 MiB，单张贴图最多 64 MiB，最多 32 页；皮肤和动画各最多 256 项。
渲染端还会实际解码贴图、检查 WebGL 纹理尺寸上限及可绘制范围。版本不符不尝试转换。
资源自带的 `hash` 不参与身份、缓存或完整性校验。

`describe()` 返回当前资源的皮肤和动画列表、对应 JSON Schema、控制说明及相对文件引用。
快照不传绝对文件路径或整份骨骼 JSON，前端通过宿主授权的资源 URL 加载。插件无逐帧 Python 调用。

## 控制数据

模型提供的 `payload` 是对象，插件支持以下字段，实际允许范围由资源的 `modelControls` 决定：

| 字段 | 类型 | 行为 |
|---|---|---|
| `skin` | 当前皮肤名称 | 更换持续皮肤 |
| `animation` | 当前动画名称 | 更换循环动画，结束旧动作 |
| `speed` | 0.1–3 的有限数 | 设置循环和动作的播放倍速 |
| `action` | 当前动画名称 | 播放一次，结束后恢复循环动画 |

提示词和输出 Schema 只贡献已开放的字段及其候选；解析器使用同一份范围。Room 的提示词与 Schema
只有 `skin`，模型发送 `speed`、`animation` 或 `action` 时拒绝载荷，不能靠手写字段绕过限制。
表情切换保持动画进度和编辑页设置的速度。

省略字段保持当前状态。未开放字段、未知名称、类型错误和越界值拒绝整个 Spine 载荷。
校验使用绑定时的 `parserData`，不在每次解析时重读文件。旧 `portrait`/`tone` 返回空状态和空动作。
宿主继续负责保存有效文本、语音和控制目标，错误按公共边界转换为 `VISUAL_CONTROL_REJECTED`。

Room 解析结果示例：

```json
{"state":{"skin":"smile"},"actions":[]}
```

`state` 最多包含 `skin`、`animation`、`speed`，`actions` 为空或仅含一个动画对象。
模型不能指定骨骼脚本、JavaScript、纹理路径或绑定 ID。

## 前端模块

`renderer.mjs` 导出异步 `createRenderer({container, rendererData, resolveAssetUrl, bindingId, resourceId,
signal?, onLayout?, onError?})`。`resolveAssetUrl(relative)` 返回当前资源根下的授权 URL，可返回 Promise。
运行库在插件内离线提供，角色资源只作为数据加载。

成功返回以下方法：

| 方法 | 行为 |
|---|---|
| `applyControl(control, {sequence})` | 接受宿主解析结果；版本、资源和绑定必须相符，序号必须是严格递增的非负安全整数 |
| `cancel()` | 清除动作及其混合，恢复当前循环、皮肤和速度 |
| `setPaused(boolean)` | 暂停或恢复逐帧推进，不清除当前状态 |
| `resize()` | 按容器尺寸适配视口 |
| `snapshot()` | 返回当前循环、播放动画、皮肤、速度及销毁状态，供预览诊断 |
| `dispose()` | 终止加载、帧回调和监听，释放纹理、位图、shader、batcher 与 WebGL 上下文 |

一个实例从挂载到销毁使用同一套序号。重复或过期序号不重放动作；序号由宿主分配，不能采用模型字段。
取消后宿主须丢弃在途旧结果。切换资源、停用插件或结束绑定须 abort/dispose 旧实例，不能仅隐藏 canvas。
加载失败和加载途中取消也清理已分配资源。WebGL 上下文丢失时结束实例并报告错误，由用户重新加载。

`onLayout` 目前报告 CSS 像素尺寸和初始可见范围，仅供预览布局，不宣称已支持原生 alpha 命中和鼠标穿透。
视口按初始动画姿态固定，动作不会驱动相机缩放。音频、交互驱动和多骨骼特效编排后续另行接入。

`editor.mjs` 导出 `createEditor({container, rendererData, onChange?, onPreview?})`，返回 `getDraft()` 和 `dispose()`。
编辑器提供皮肤、循环动画和速度编辑，动作按钮只触发预览，不把一次动作写为默认设置。
`onChange` 得到独立配置副本。`onPreview(payload)` 是用户在编辑页的操作，由宿主调用插件的
`parse_preview_control()` 校验实际资源名称和参数范围，再交给渲染器。编辑预览可调整速度，
不扩展模型的控制范围；不能把模型产生的数据送到编辑预览入口。
单动画资源省略动画选择和一次动作按钮，保留表情与速度编辑。
编辑器使用独立 DOM 区域，不执行文件操作；草稿、恢复和发布属于宿主。

## 开发预览与验证

`tools.spine_preview prepare` 在新目录准备组件及 `catalog.json`，原始资源保持不变。`serve` 只监听
`127.0.0.1`，使用同一插件的说明、解析器、renderer 和 editor。保存仅写输出组件的 `spine-draft.json`，
重新选择资源或重启后读取草稿。它不连接实际角色目录、模型服务或聊天会话。
`/api/control` 使用模型解析器，`/api/preview` 使用编辑预览校验；二者不共用模型权限。
原始游戏中独立特效的触发位置、时间和层次无法从骨骼名单推断，工具跳过没有独立布局的特效并记录原因。

[Python 测试](../../../tests/unit/test_spine_plugin.py)覆盖复制、资源安全、参数快照和生产 v4 安装/进程调用；
[Node 测试](../../../desktop/frontend/tests/spine-plugin.test.js)使用真实 Spine 动画状态和骨骼验证动作、恢复和生命周期。
浏览器画面与原生窗口是不同证据层，浏览器通过不代表 Tauri 窗口和完整聊天链路已验收。

运行库来源与许可见[运行库说明](../../../plugins/optional/sakura_spine/vendor/README.md)。
