---
kind: devdoc
status: current
audience: character-author
source_of_truth: self
updated: 2026-07-31
---

# 角色接话 Manifest 开发指南

本指南面向角色作者和维护本地快速接话功能的开发者。接话层会在用户发消息后、主回复生成前，先播放
一句简短的角色化过渡反应，包括字幕、表情和可选语音。

## 运行入口

框架只读取 `characters/<id>/backchannels/manifest.json`。角色包没有声明 manifest 时，运行时降级到
系统级中性兜底，不会自动加载仓库中的示例。

仓库示例位于 [`examples/backchannels/sakura/manifest.json`](examples/backchannels/sakura/manifest.json)。
开发角色包时，把它复制到目标角色目录并在 `character.json` 中声明：

```json
"backchannel": "backchannels/manifest.json"
```

然后在 Sakura 设置中启用“本地快速接话”。`rules` 模式只使用规则分类；`hybrid` 模式会在规则无命中
时使用本地 probe，模型不可用时仍安全降级到规则模式。

## Manifest 结构

清单顶层包含 schema、角色身份和模板数组。每个模板使用 `intent`、可选 `emotion` 或 `phase` 作为
匹配条件，并提供表现语气、立绘和一组中日文变体：

```jsonc
{
  "schema": "yourchar.backchannels.manifest",
  "version": 1,
  "character_id": "yourchar",
  "requires_intent_schema": "v5",
  "templates": [
    {
      "id": "yourchar_support_sad",
      "intent": "support",
      "emotion": "sad",
      "tone": "低声安慰",
      "portrait": "站立待机",
      "variants": [
        {"zh": "嗯……我在呢。", "ja": "うん……ここにいるよ。", "audio": null}
      ]
    },
    {
      "id": "yourchar_fallback_neutral",
      "intent": "fallback",
      "tone": "中性",
      "portrait": "站立待机",
      "variants": [{"zh": "嗯。", "ja": "うん。", "audio": null}]
    }
  ]
}
```

权威 intent、emotion 和 phase 词表位于
[`app/backchannel/data/intent_schema.json`](../../app/backchannel/data/intent_schema.json)，加载与容错行为由
[`app/backchannel/manifest.py`](../../app/backchannel/manifest.py) 定义。不要在文档或角色包中另建一份词表。

## 编写约束

- probe 可输出的每个具体 intent 应至少有一个模板，并必须提供 `fallback` 兜底池。
- `portrait` 必须存在于角色 `character.json` 的表情词表中，否则该条目会被跳过。
- `ja` 和 `zh` 必须成对且非空；`audio` 留空时运行时按日文文本合成并缓存。
- fallback 应保持零预设、零事实回答、零工具结果承诺，确保在闲聊和低置信输入中都自然。
- `phase` 用于 `tool_running`、`long_wait`、`repeated_issue` 等等待状态，匹配优先于普通意图。
- `request` 与当前构建实际提供的工具能力耦合；删减工具集时必须重新检查请求类模板是否仍可达。

匹配顺序为：相位、意图与情绪精确匹配、同意图、意图家族根、fallback。未知 tone 可以由 TTS
降级，但未知 portrait 会直接跳过，以避免引用不存在的角色资源。

## 语音与验证

预合成音频应使用角色包内相对路径。没有预合成音频时，运行期缓存位于
`data/backchannels/<character_id>/audio/`，角色包本身保持只读。

提交角色清单前至少验证：JSON 可解析、词表版本匹配、每个变体中日文成对、portrait 可解析、fallback
存在，以及加载器能在单条坏数据出现时跳过该条而不使整个角色不可用。
