# 角色接话 manifest 制作要求(面向开发者)

本文档说明如何为你的角色编写「等待期接话」模板 manifest,使其与桜的 **probe(意图分类器)** 相容。

## 1. 它是怎么运作的

用户发消息后,主回复要几秒到十几秒才生成。这段等待里,角色先说一句简短的过渡(filler)。

```
用户消息 → probe 分类出 intent → resolver 按 intent 在你的 manifest 里挑模板 → 角色说出来
```

- **probe**:一个共享的分类器(版本特异,不是角色特异),产出 `intent`。
- **你的 manifest**:角色特异,按 `intent` 提供台词。

二者的**契约就是 intent 词表**。probe 会 emit 的每个 intent,你都得有对应模板,否则会降级到兜底池。

## 2. intent 契约(版本化)

权威定义见 `app/backchannel/data/intent_schema.json`,当前版本 **`2026-06-15.v5`**。

**普世 intent(任何角色都该覆盖):**

| intent | 含义 |
|---|---|
| `support` | 用户表达自身负面情绪,求安慰 |
| `complaint` | 对外部人/事吐槽不满 |
| `positive` | 分享成功/喜悦 |
| `affection` | 对角色亲昵/想念 |
| `error` | 报告技术故障 |
| `greeting` | 程式化社交礼仪;含子家族 `greeting_return/morning/evening/goodnight` |

**能力耦合 intent:**

| intent | 含义 |
|---|---|
| `request` | 任务句,**且落在该 build 支持的能力上**(见 §5) |

**不 emit 的:** 闲聊 / 无信号 / 不支持的任务 → probe 判 `none`,**不进具体 intent**,直接落 `fallback` 池。

> greeting 子家族:有基类 `greeting` 模板即可经 resolver 家族回退覆盖全部子类;想区分早安/晚安/回来了再单独加子类模板。

## 3. manifest 必备结构

```jsonc
{
  "schema": "yourchar.backchannels.manifest",
  "version": 1,
  "character_id": "yourchar",
  "display_name": "你的角色名",
  "requires_intent_schema": "v5",          // 声明你针对的 intent 契约主版本
  "templates": [
    {
      "id": "yourchar_support_sad",
      "intent": "support",
      "emotion": "sad",                      // 可选:精确到 (intent, emotion)
      "tone": "低声安慰",
      "variants": [                          // zh/ja 配对;运行时按语言选
        { "zh": "嗯…我在呢。", "ja": "うん…ここにいるよ。" }
      ]
      // "phase": "long_wait"                 // 可选:repeated_issue/tool_running/long_wait
    },
    {
      "id": "yourchar_fallback_neutral",
      "intent": "fallback",                  // ★ 兜底池:必备
      "variants": [ { "zh": "嗯~", "ja": "ん~" } ]
    }
  ]
}
```

## 4. 覆盖要求(硬性)

1. **probe 会 emit 的每个具体 intent 至少 1 个模板**(`support/complaint/positive/affection/error/greeting/request`)。缺失不致命——resolver 会降级到 `fallback` 池——但体验会变差。用 §6 的工具自检覆盖率。
2. **必须有 `fallback` 兜底池**。闲聊与低置信输入有意落这里。
3. **fallback 必须零预设、零承诺**:拿「我回来了」「帮我查天气」「今天好热」三句逐条套,三个语境都不违和才允许进池。不得出现事实回答、不得承诺工具结果。
4. **不要留死模板**:probe 不 emit 的 intent(如旧版的 `question`)不要写,会永远不触发。

## 5. ⚠️ `request` 的能力耦合(最容易踩的坑)

`request` 是**与你的工具集绑定**的 intent。当前 v5 probe 的判据假设 build 支持:
**时间 / 提醒(timer)/ 待办 / 网络搜索 / 笔记 / 打开网址或文件夹 / 记忆**。

它据此把「查天气→request、放首歌→none」烤进了权重。**如果你的 build 工具集不同**(比如没有搜索、却有音乐播放),这个边界就会反:

- 推荐做法 A:**工具集与 Sakura 对齐**,直接复用本 probe;
- 推荐做法 B:用相同标注标准(能力感知)**重训一枚属于你 build 的 probe**;
- 长期方向:把 `request` 从 probe 移到**运行时查工具注册表**判定(build 最清楚自己能干啥),probe 只管普世情绪/社交 intent。

## 6. 自检:确认你的角色支持目标 probe

用离线工具 `tools/probe_dist.py`(纯标准库单文件,可直接拷走):

```bash
python tools/probe_dist.py check-compat \
  --probe-manifest app/backchannel/data/probe_manifest.json \
  --character-manifest characters/yourchar/backchannels/manifest.json \
  --build-capability time --build-capability web_search   # 你的 build 实际支持的能力
```

输出会列出:schema 版本是否相容、**未覆盖的 intent**(会掉兜底,建议补)、**死模板**(可清理)、以及 `request` 能力耦合提醒。`✗ 不相容` 时退出码非 0,可纳入 CI。

## 7. 文案风格(经验)

- 短句、克制、符合人设;一句话以内。
- **不要事实回答、不要承诺工具结果**(那是主回复的事,filler 只是过渡)。
- 同一 intent 多写几条 `variants`,避免重复感。
- greeting/error 等高精度信号由规则层直接命中,文案要稳。

---

相容性契约由 `tools/probe_dist.py` 的 `check-compat` 机检保证;probe 产物的完整性/溯源见 `tools/probe_dist.py`。
