---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-12
---

# Spine 表现插件

## 范围

`plugins/optional/sakura_spine` 实现 Spine 3.6 JSON 表现，插件 ID 与 Service 均为 `sakura.visual.spine`，
资源类型为 `spine.json@1`。后端遵循[表现插件边界](visual-plugin-boundary.md)，前端模块可由本地预览工具调用。
插件使用公共 `mount` / `mountEditor` 接口接入桌面渲染器和角色工坊；独立预览与正式挂载复用同一运行库。
安装插件后仍需添加形态组件并选择显示方式，不自动改变当前角色。

## 资源配置

资源引用的 `entry` 指向 UTF-8 JSON 配置，所有路径相对该资源根，使用 `/`：

```json
{
  "version": 1,
  "skeleton": "model/skeleton.json",
  "atlas": "model/skeleton.atlas",
  "defaultSkin": "normal",
  "selectableSkins": ["normal", "smile"],
  "skinLabels": {"normal": "平静", "smile": "开心"},
  "defaultAnimation": "idle",
  "speed": 1,
  "modelControls": ["skin"],
  "premultipliedAlpha": false
}
```

`version`、`skeleton`、`atlas` 必填，其余字段可省略。默认循环优先选 `idle`，否则选第一项；
默认皮肤从可选列表中优先选 `default`，否则选第一项；默认速度为 1，默认输入为普通透明贴图。
示例资源导入工具遇到 `normal` 皮肤会显式设为初始皮肤，避免把仅含共享部件的 `default` 当作完整表情。
配置只接受上述字段。皮肤和动画名必须存在，速度是 0.1–3 的有限数，布尔值不作为数字接受。

`selectableSkins` 可选，用于声明可切换的完整表情；必须是实际皮肤名称的非空子集，不允许重复，
`defaultSkin` 必须在其中。省略时使用全部皮肤。编辑器、提示词、Schema、模型解析和前端控制使用同一候选范围。
隐藏皮肤仍保留在骨骼内，继续参与 Spine 的附件回退。例如只有基础部件的 `default` 可以隐藏，
由 `normal` 提供正常五官；不会把多个表情叠加。界面将原始 `default` 标为“基础皮肤”。

`skinLabels` 是可选的皮肤 ID 到显示名称的映射。键必须存在于骨骼中，值必须是最多 120 字符的字符串；
允许重复名称。未设置或留空时使用插件已有名称，未知皮肤使用原始 ID。
导入保留包内名称；工坊为当前表情提供“表情名称”输入框，修改时按钮文字同步更新。
切换表情、草稿、保存重开和形态导出保留各自名称，隐藏皮肤的名称也不丢失。
模型提示词提供当前可选皮肤的非空自定义名称，并将 ID 和显示名称标为资源数据；
控制值继续使用原始皮肤 ID，改名不会修改骨骼、皮肤绑定或动画。

`premultipliedAlpha` 声明输入贴图的颜色编码，已预乘的素材必须设为 `true`，不通过文件名或像素猜测。
渲染器将普通透明输入转换为预乘纹理，已预乘输入直接上传；两者统一使用预乘颜色合成到透明画布，
避免半透明部件重叠时重复衰减透明度。原始贴图文件不改写。

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
`assets` 声明骨骼、图集和所有贴图：键是资源根内路径，值是角色包根内路径。
`editorData` 保留配置草稿，入口缺失时返回空配置供目录导入；`exportResource` 投影配置入口，附件由 `assets` 声明。
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

正式渲染入口为 `mount({container, resource, host, signal})`。它使用 `resource.data` 和 `resource.assets`，
向宿主声明按模型比例计算的矩形表面，返回 `ready/applyState/perform/cancel/destroy`。
公共宿主负责 operation/segment 去重，适配层为内部控制器的状态和动作调用分配递增序号。
绑定 signal 中止时停止加载、动画与监听，保留静态 canvas，替代实例就绪后由 destroy 释放 GPU 资源。
挂载失败和加载途中取消直接释放资源。这里的矩形表面不等于逐帧 PNG alpha 命中。

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
取消后宿主须丢弃在途旧结果。切换资源、停用插件或结束绑定须 abort 旧实例，替换完成后 dispose，不能仅隐藏 canvas。
加载失败和加载途中取消也清理已分配资源。WebGL 上下文丢失时结束实例并报告错误，由用户重新加载。

`onLayout` 目前报告 CSS 像素尺寸和初始可见范围，仅供预览布局，不宣称已支持原生 alpha 命中和鼠标穿透。
视口按初始动画姿态固定，动作不会驱动相机缩放。音频、交互驱动和多骨骼特效编排后续另行接入。

`editor.mjs` 导出 `createEditor({container, rendererData, onChange?, onPreview?, onRenderingChange?})`，返回 `getDraft()` 和 `dispose()`。
编辑器提供皮肤、表情名称、循环动画、速度和贴图透明方式编辑，动作按钮只触发预览，不把一次动作写为默认设置。
`onChange` 得到独立配置副本。`onPreview(payload)` 是用户在编辑页的操作，由宿主调用插件的
`parse_preview_control()` 校验实际资源名称和参数范围，再交给渲染器。编辑预览可调整速度，
不扩展模型的控制范围；不能把模型产生的数据送到编辑预览入口。
单动画资源省略动画选择和一次动作按钮，保留表情、速度与贴图透明方式编辑。
透明方式变化通过 `onRenderingChange(config)` 重新加载预览，保留当前表情和速度；保存、重开与导出保留该配置。
旧包可以在编辑器中修正透明方式并选择完整表情，无需重新导入。
编辑器使用宿主内的普通 DOM，复用工坊的 `primary-button`、`secondary-button`、`layout-slider` 和输入框样式。
插件样式只补充带 `spine-` 前缀的布局，随销毁移除；不使用 Shadow DOM 隔断公共控件，也不固定背景或文字颜色。
编辑器不执行文件操作；草稿、恢复和发布属于宿主。

正式编辑入口 `mountEditor({container, data, host, signal})` 返回 `ready/collect/validate/destroy`，
通过 `host.assetUrl` 读取骨骼、图集和贴图，复用运行库预览；配置变化交给 `host.changed`。
目录选择和复制交给 `host.importFiles({folder:true})`。组件目录优先读取 `spine-resource.json` 并调整导入路径前缀；
原始目录必须恰有一套完整骨骼及同名图集，多套骨骼要求用户缩小选择范围。样式使用 adoptedStyleSheets，遵循桌面 CSP。

## 开发预览与验证

`tools.spine_preview prepare` 在新目录准备组件及 `catalog.json`，原始资源保持不变。`serve` 只监听
`127.0.0.1`，使用同一插件的说明、解析器、renderer 和 editor。保存仅写输出组件的 `spine-draft.json`，
重新选择资源或重启后读取草稿。它不连接实际角色目录、模型服务或聊天会话。
`/api/control` 使用模型解析器，`/api/preview` 使用编辑预览校验；二者不共用模型权限。
准备已预乘贴图时显式使用 `--premultiplied-alpha`；通过可重复的 `--exclude-skin NAME`
排除不完整表情，例如 `--exclude-skin default`，不删除对应骨骼数据。
原始游戏中独立特效的触发位置、时间和层次无法从骨骼名单推断，工具跳过没有独立布局的特效并记录原因。

准备后的组件独立声明 `spine-resource.json`，骨骼和图集统一为 `model/skeleton.json`、`model/skeleton.atlas`，
贴图位于 `model/` 并保留图集引用的名称，原文件内容不变。`catalog.json` 只供开发预览选择组件。
`tools.spine_preview export` 将准备目录中的配置及依赖交给公共归档写入器，生成 version 2、kind `resource`
的 `.visual` 形态组件，包含名称、类型、入口，以及建议安装 `sakura.visual.spine` 的 `resource.pluginRequirements`；不携带人格和语音。正式工坊可导入、编辑、保存和再次导出，仍可读取旧版 `.char` 形态组件。
未安装 Spine 或其他兼容 `spine.json@1` 的插件时，导入和保存保留资源，编辑区提示缺少插件；安装并启用后可使用原资源。

[Python 测试](../../../tests/unit/test_spine_plugin.py)覆盖复制、资源安全、参数快照和生产 v4 安装/进程调用；
[Node 测试](../../../desktop/frontend/tests/spine-plugin.test.js)使用真实 Spine 动画状态和骨骼验证动作、恢复和生命周期。
浏览器画面与原生窗口是不同证据层，浏览器通过不代表 Tauri 窗口和完整聊天链路已验收。
[Spine 浏览器 journey](../../../desktop/frontend/tests/spine-plugin.journey.py)使用隔离角色、真实插件进程和正式工坊，
覆盖组件与目录导入、表情速度保存重开，以及公共 RendererHost 的播放、去重、冻结和销毁；可传入实际组件目录。

运行库来源与许可见[运行库说明](../../../plugins/optional/sakura_spine/vendor/README.md)。
