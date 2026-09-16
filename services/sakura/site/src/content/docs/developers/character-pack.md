---
title: 角色包制作
description: 角色包的目录结构、character.json 字段、立绘与表情、主题色和语音权重。
---

# 角色包制作

Sakura 的角色以**角色包**为单位组织。一个角色包把人格卡、立绘、主题色、语气和语音权重打包在一起，可以独立分发。用户在设置里一键导入，即可切换整个角色的对话风格、外观和声音。

本页讲如何从零做一个可分发的角色包。可以直接复制内置的 `Sakura` 角色目录作为模板，按需替换其中的资源。

## 打包与导入

角色包是一个**角色文件夹**，打包成 `.char` 文件（本质是压缩包）后分发。导入流程：

1. 在 Sakura 设置中选择导入角色包，选中 `.char` 文件。
2. Sakura 解包后读取 `character.json`，注册角色、立绘、语气和语音。
3. 在角色列表中选中该角色即可启用，UI 主题色跟随角色配置切换。

> 开发期间也可以直接把角色文件夹放进项目根目录的 `characters/`，由 `CharacterRegistry` 在启动时扫描加载。

## 目录结构

```text
你的角色/
├── character.json        # 角色清单：元数据、立绘映射、主题、语气、语音
├── card.md               # 人格卡（系统提示词，Markdown 纯文本）
├── portraits/            # 立绘图片（建议透明 PNG）
│   ├── A020.png
│   ├── A061.png
│   └── ...
└── voice/
    ├── refs/
    │   └── ref.txt       # 参考音频文本
    └── models/
        ├── 角色-e15.ckpt      # GPT-SoVITS GPT 权重
        └── 角色_e8_s7176.pth  # GPT-SoVITS SoVITS 权重
```

`character.json` 里的所有路径都相对于角色文件夹根目录。

## character.json 字段

以内置 `Sakura` 角色为例：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 角色唯一标识，需与目录名一致 |
| `display_name` | string | UI 中显示的角色名，可用本地化名称（如「夜乃桜」） |
| `initial_message` | string | 启动时的首句问候 |
| `card` | string | 人格卡文件路径，通常是 `card.md` |
| `portrait.default` | string | 默认/待机立绘路径 |
| `portrait.expressions` | object | 表情标签 → 立绘路径的映射（见下文） |
| `theme` | object | UI 配色 token（见下文） |
| `reply.tones` | string[] | 可用语气标签列表 |
| `voice` | object | GPT-SoVITS 参考音频与权重（见下文） |

## 立绘与表情

`portrait.expressions` 把**表情标签**映射到立绘图片：

```json
"portrait": {
  "default": "portraits/A020.png",
  "expressions": {
    "站立待机": "portraits/A020.png",
    "开心脸红": "portraits/A061.png",
    "害羞脸红": "portraits/A081.png",
    "吃醋不满": "portraits/A230.png"
  }
}
```

这套标签直接和**分段回复**联动。模型的每段回复都是一个 JSON 段，包含日文原文、中文字幕、语气（`tone`）和立绘标识（`portrait`）；其中 `portrait` 取值就是 `expressions` 里的某个标签，UI 据此切换立绘。所以：

- 表情标签命名要**语义清晰**，方便模型按情绪选择（如「难过沮丧」「自信拍胸」）。
- 人格卡里描述情绪时，最好与这些标签呼应，让模型更稳定地选对表情。
- 模型给出的、不在 `expressions` 中的标签都会回退到 `default`。

## 人格卡 card.md

`card.md` 是角色的系统提示词，纯 Markdown，没有固定 schema。它决定角色的性格、说话风格、关心方式和边界。建议覆盖：

- **人格核心**：性格、动机、与用户的关系定位。
- **说话风格**：句子长短、语气、口头表达习惯，是否使用某种语言。
- **行为逻辑**：什么情况下主动开口、追问、提醒、表达不满或保持沉默。
- **边界**：明确拒绝的内容（如不替代现实人际/医疗/工作责任）。
- **口吻参考**：给几句示例台词，但提示模型不要机械复读。

> 人格卡用第二人称书写（"你正在扮演……"）效果通常更稳定。

## 主题色

`theme` 只贡献 UI 配色，在角色启用时应用到窗口、气泡和输入栏：

```json
"theme": {
  "primary_color": "#d55b91",
  "primary_hover_color": "#bf3f7a",
  "accent_color": "#b13e73",
  "text_color": "#3d2b35",
  "secondary_text_color": "#7a3656",
  "muted_text_color": "#9b4f72",
  "page_background_color": "#fff6fa",
  "panel_background_color": "#ffe8f1",
  "input_background_color": "#ffffff",
  "bubble_background_color": "#ffe8f1",
  "border_color": "#eeacc8"
}
```

注意：外观模式（纯色 / 高斯模糊 / 亚克力 / macOS 毛玻璃）是**用户级偏好**，不随角色包分发；角色包只决定配色。

## 语气

`reply.tones` 列出模型可用的语气标签，对应每段回复里的 `tone` 字段：

```json
"reply": {
  "tones": ["中性", "不满", "害羞", "请求", "惊讶"]
}
```

语气标签影响字幕呈现，启用 TTS 时用于挑选对应的参考音频。

## 语音权重

语音基于 GPT-SoVITS，权重与参考音频随角色包分发：

```json
"voice": {
  "tone_refs": "voice/refs/ref.txt",
  "ref_lang": "ja",
  "text_lang": "ja",
  "gpt_model": "voice/models/角色-e15.ckpt",
  "sovits_model": "voice/models/角色_e8_s7176.pth"
}
```

| 字段 | 说明 |
|---|---|
| `tone_refs` | 参考音频文本，供 TTS 做音色参考 |
| `ref_lang` | 参考音频语言（如 `ja` / `zh`） |
| `text_lang` | 合成文本语言 |
| `gpt_model` | GPT-SoVITS 的 GPT 权重（`.ckpt`） |
| `sovits_model` | GPT-SoVITS 的 SoVITS 权重（`.pth`） |

不需要语音的角色可以省略 `voice`，启用 TTS 才需要它。语音默认关闭，配置方式见[技术架构](/developers/architecture/)的 TTS 一节。

## 制作建议

- **立绘**用透明背景 PNG，同一角色各表情保持相同画布尺寸与站位，切换时不跳动。
- **命名**保持稳定：`character.json` 用相对路径引用，改名会让映射失效。
- **表情数量**够用即可，至少覆盖待机和几种核心情绪，让模型有表达空间。
- **打包**时保留上面的目录结构，把角色文件夹整体压缩为 `.char`。
- 想从能跑的样例起步，直接复制内置 `Sakura` 角色目录，逐项替换资源。
