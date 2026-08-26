---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-26
---

# Runtime v2 发行与存储合同

## 两个所有权根

Runtime v2 只接受 `distribution_root` 与 `user_root`。Shell 必须通过
`--distribution-root` 和 `--user-root` 把两者传给 Core；生产启动合同不存在 `--app-root`。

`distribution_root` 包含 `VERSION`、`runtime-manifest.json`、`python/`、`core/` 和
`plugins/builtin/`，由安装器和更新器拥有，运行时只读。`user_root` 包含 `config/`、`data/`、
`characters/`、`plugins/user/` 和默认 `tts/`，由用户拥有，不进入发行 staging。

平台解析固定为：

- Windows Setup/Portable：两根均为用户选择的 Sakura 安装目录；
- macOS：发行根为 `.app/Contents/Resources`，用户根为
  `~/Library/Application Support/Sakura`；
- Linux（仅保留编译）：用户根为 `${XDG_DATA_HOME:-~/.local/share}/Sakura`。

macOS `.app` 是不可写且可整体替换的签名资产。Updater 不得修改 Application Support；首版不提供 macOS
Portable，也不把 `data/cache` 分拆到 `~/Library/Caches`。由于 Memory 发行依赖 `onnxruntime 1.28` 的
arm64 wheel，首版最低系统版本冻结为 macOS 14.0。

## 干净首次启动与角色

发行包不包含角色。缺少角色是受支持的 `CHARACTER_REQUIRED` 状态：Core 和设置可用，桌宠隐藏，设置窗口
打开到角色页，托盘点击重新打开设置。角色导入使用已有 `.char` 原子 importer，通过类型化命令完成；不存在
默认 `sakura` 角色、首角色 fallback 或默认角色 prompt。

主程序自带默认浅蓝主题。当前角色携带主题时覆盖它，否则所有窗口都使用主程序默认主题。
角色无主题时也不得从角色名、旧 prompt 或内置角色资源推断默认外观。
第一方 WebView 界面只使用纯色、透明度、边框与中性阴影建立层级，不得定义 CSS 或 SVG 渐变，也不得
使用主题主色或强调色绘制模糊光晕；角色资源、用户导入图片和应用图标中的原始像素内容不在此限制内。

## TTS 存储

`config/storage.json` schema 1 只保存可选的 `ttsRoot`。最终路径为：

```text
configured_tts_root or user_root/tts
```

默认目录可以创建；自定义目录必须已经存在、是绝对路径且可写。自定义目录失联时不得回退或创建替代目录，
TTS 返回 `TTS_STORAGE_UNAVAILABLE`，设置快照通过 `TTS_ROOT_MISSING`、`TTS_ROOT_NOT_DIRECTORY` 或
`TTS_ROOT_NOT_WRITABLE` 给出原因。切换目录不自动移动已有 TTS 文件。

## 发行内容

六个内置插件为 `playwright_browser`、`sakura_mem0`、`sakura_mobile`、`sakura_tts_hub`、
`sakura_genie` 和 `sakura_gpt_sovits`。它们默认启用、允许禁用、不可卸载。

发行 Python 携带插件依赖与原生 `site-packages`；`uv`、`uvx`、`7zz` 位于 `python/tools/`。主 Python
运行时只读且不执行 pip。Playwright 不携带 Chromium，Memory 不携带约 91 MB 模型，Genie/GPT-SoVITS
不携带本体、环境或模型。

Windows 生成 Setup 与带 `portable.flag` 的 ZIP；前者使用 Tauri Updater，后者只检查并下载新版 ZIP。
macOS 生成 `.app`、DMG 与 updater artifact。正式公开产物必须签名，开发 staging 可以无签名。
