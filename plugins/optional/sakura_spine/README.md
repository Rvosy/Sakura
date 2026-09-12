# Spine

Sakura 的可选表现插件，支持 Spine 3.6 JSON 骨骼、文本图集和 PNG/JPEG 贴图。
插件提供资源校验、实际皮肤与动画候选、提示词贡献、控制解析、WebGL 渲染和编辑模块。
正式 Assistant、桌宠及工坊的公共挂载仍在改造；当前可用本地预览工具运行同一套模块。

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

预览中的“保存草稿”保存到输出目录中的 `spine-draft.json`，重新选择形态或重启预览可恢复。
它不会写入已安装角色。编辑器贡献的 `getDraft()` 供未来工坊写入自己的草稿事务。
当前组件目录不是 `.char` 角色包，不应通过旧版角色导入器导入。

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
公共 RendererHost 尚未冻结，后续由公共宿主适配，不在 Core 中增加 Spine 分支。

## 运行库与范围

运行时无需 CDN。插件携带 Esoteric Software 官方 Spine Runtimes 3.6 WebGL 构建，来源和许可证见
[运行库说明](vendor/README.md)。仅接受 `3.6.x` JSON；`.skel` 二进制、其他 Spine 版本、Live2D 和
游戏特效的多骨骼编排不在当前实现范围内。音频口型、鼠标跟随和原生窗口穿透尚未接入。

```powershell
runtime/python.exe -m pytest -q tests/unit/test_spine_plugin.py
node --test desktop/frontend/tests/spine-plugin.test.js
```

Python 测试覆盖资源、校验、复制和真实 v4 安装/独立进程绑定；Node 测试使用官方动画运行库验证动作执行、
恢复、去重和销毁。WebGL 画面需另做浏览器验收，不能用 Node 测试代替。
