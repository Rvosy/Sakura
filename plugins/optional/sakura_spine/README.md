# Spine

Sakura 的可选表现插件，支持 Spine 3.6 JSON 骨骼、文本图集和 PNG/JPEG 贴图。
插件提供资源校验、实际皮肤与动画候选、提示词贡献、控制解析、WebGL 渲染和编辑模块。
插件使用正式 RendererHost 和工坊编辑器接口，也保留独立预览入口。

插件 ID 为 `sakura.visual.spine`，资源类型为 `spine.json@1`。后端使用现有 v4 Service，
无需额外 Python 依赖。插件安装后按现有流程启用；仅安装插件不会改变当前角色的显示方式。

## 本地预览

在仓库根目录运行（macOS/Linux 将解释器替换为 `runtime/bin/python`）：

```powershell
runtime/python.exe -m tools.spine_preview prepare "原始资源目录" "artifacts/spine/my-character"
runtime/python.exe -m tools.spine_preview serve "artifacts/spine/my-character" --port 8786
```

打开 `http://127.0.0.1:8786`。工具把完整骨骼、对应图集及贴图复制到新目录，生成资源引用和
`spine-resource.json`。输出目录已存在时停止，不覆盖原始资源。只有元数据的 JSON 和没有独立布局的
特效会记录到 `catalog.json` 的 `skipped` 中。角色文件不执行脚本，也不会上传。

只使用 Room 时，把第一条命令的输入指向解包目录中的 `spine/room`，就只导入 Room 的动画和皮肤。
其他角色也可以按同样方式选定所需子目录，插件不依赖角色编号或文件名。

本次 DeepOne Room 的贴图已经预乘透明度，且 `default` 只有基础部件，五官在 `normal` 和各表情皮肤中。
准备这些素材时使用：

```text
runtime/bin/python -m tools.spine_preview prepare 原始room目录 artifacts/spine/room --premultiplied-alpha --exclude-skin default
```

生成配置会设置 `premultipliedAlpha: true`，默认选择 `normal`，通过 `selectableSkins` 排除基础皮肤。
基础附件仍保留，供完整表情使用；不叠加多个表情。其他素材按自身的透明编码和皮肤结构选择参数。

已经导入的旧包可在编辑器把“贴图透明方式”改为“预乘透明（PMA）”，表情选择“平静”后保存。
更改透明方式会重建预览并保留表情和速度，导出也会保留配置。修正版组件直接导入即可。


预览中的“保存草稿”保存到输出目录中的 `spine-draft.json`，重新选择形态或重启预览可恢复。
它不会写入已安装角色；正式工坊通过 `mountEditor` 和自己的草稿事务保存配置。
组件目录需先导出为 `.visual` 文件，再通过工坊的“导入形态”添加。

## 形态组件

解包目录只作为素材来源。准备工具把每套骨骼整理成独立组件，游戏目录和角色编号不参与运行时判断：

```text
spine-resource.json       # 默认皮肤、动画、速度、模型控制范围与文件引用
model/skeleton.json      # 原始骨骼
model/skeleton.atlas     # 原始图集
model/room.png           # 保留图集引用的贴图名称
```

组件的显示名称、类型和入口由 `.visual` 中的 `manifest.json` 声明（version 2、kind `resource`）。
同一角色可以拥有多套组件；编号只是准备工具生成的初始名称，可在工坊改名。
骨骼、图集和贴图内容保持原样，只调整组件内的文件组织和配置引用。

```json
{
  "format": "sakura.character.archive",
  "version": 2,
  "kind": "resource",
  "resource": {
    "name": "日常形态", "type": "spine.json@1", "entry": "spine-resource.json",
    "pluginRequirements": [{
      "kind": "visual", "type": "spine.json@1",
      "plugins": [{"id": "sakura.visual.spine", "name": "Spine"}]
    }]
  }
}
```

归档中的配置与 `model/` 统一放在 `resource/` 下，角色包内的资源 ID 和目录由工坊导入时分配。

准备后执行以下命令生成可导入文件：

```text
runtime/bin/python -m tools.spine_preview export artifacts/spine/my-character artifacts/spine/importable
```

在插件设置中从本仓库的 `plugins/optional/sakura_spine` 安装并启用 Spine，然后进入
“角色工坊 → 角色形态 → 导入形态”选择输出的 `.visual`。完整角色包继续用 `.char`，语音包用 `.voice`；旧版 `.char` 形态组件仍可导入。
也可以“添加形态 → Spine”，再在编辑器中“导入模型目录”，选择一套组件目录或仅包含一套完整骨骼的素材目录。
导入文件写入工坊草稿；修改表情、速度后按“保存”。工坊的“导出形态”保留本次保存的配置与全部依赖。
尚未安装支持 `spine.json@1` 的插件时仍可导入和保存，工坊提示缺少插件，暂不能编辑或显示该形态。
导入会把它设为包默认；可先改用已有形态，安装并启用插件后再选择它，无需重新导入。组件声明所需的 `spine.json@1` 格式，并建议安装 `sakura.visual.spine`；其他声明兼容该格式的插件也可满足需求。

## 控制和前端接入

Room 只向模型开放 `skin` 表情选择，待机动画始终循环，默认 1 倍速。播放速度保留在角色工坊的 Spine
编辑页，随草稿保存，不增加程序设置项。表情切换保持当前动画进度和速度。

其他资源可用 `modelControls` 选择开放的字段：`skin` 切换皮肤，`animation` 设置循环动画，
`speed` 设置 0.1–3 倍速，`action` 播放一次动画。省略配置时，单动画资源只开放 `skin`，
多动画资源保留全部字段。所有名称取自当前资源，提示词、Schema 和模型解析器使用相同范围。
省略控制字段保持原状态，动作完成后回到当前循环动画。编辑页预览独立校验，不受模型开放范围限制。
旧 `portrait`/`tone` 没有 Spine 含义，插件忽略这些旧字段。

后端接口遵循[表现插件边界](../../../docs/specs/runtime-v2/visual-plugin-boundary.md)。
插件前端导出 `createRenderer()`、`createEditor()`，具体参数、交付序号、取消与销毁要求见
[Spine 插件规范](../../../docs/specs/runtime-v2/spine-visual-plugin.md)。这些方法目前是本插件的前端接口，
正式入口为 `mount` 和 `mountEditor`，通过公共宿主获取授权资源和提交草稿，Core 无 Spine 专属分支。

## 运行库与范围

运行时无需 CDN。插件携带 Esoteric Software 官方 Spine Runtimes 3.6 WebGL 构建，来源和许可证见
[运行库说明](vendor/README.md)。仅接受 `3.6.x` JSON；`.skel` 二进制、其他 Spine 版本、Live2D 和
游戏特效的多骨骼编排不在当前实现范围内。音频口型和鼠标跟随尚未接入；原生表面使用矩形命中，不生成动画逐帧透明遮罩。

```powershell
runtime/python.exe -m pytest -q tests/unit/test_spine_plugin.py
node --test desktop/frontend/tests/spine-plugin.test.js
runtime/bin/python desktop/frontend/tests/spine-plugin.journey.py
```

Python 测试覆盖资源、校验、复制和真实 v4 安装/独立进程绑定；Node 测试使用官方动画运行库验证动作执行、
恢复、去重和销毁。浏览器 journey 使用真实工坊和插件进程，在桌面 CSP 下验证组件导入、目录导入、
保存重开、WebGL 播放和绑定生命周期；可用 `--components 准备目录` 检查实际素材。原生窗口服务由测试桥代替。
