# 接话(backchannel)模板资产与启用说明

本地快速接话层:用户发消息后、主回复生成前的等待期,桌宠先说一句很短的角色化
过渡反应(字幕 + 表情 + 可选预合成语音),减少冷场。

框架代码只读取 `characters/<id>/backchannels/manifest.json`。**没有 manifest,
接话就只剩系统级中性兜底("嗯……"),不会产出角色化模板。** 这里存放各角色的
清单作为分发源,与框架分离,避免把具体角色内容混进框架代码。

## 启用步骤

### 1. 安装角色清单

把对应角色的 manifest 复制进角色包,并在 `character.json` 里引用:

```bash
mkdir -p characters/sakura/backchannels
cp assets/backchannels/sakura/manifest.json characters/sakura/backchannels/manifest.json
```

`characters/sakura/character.json` 增加一行(缺该字段即视为该角色 opt-out):

```json
"backchannel": "backchannels/manifest.json"
```

### 2. 设置里开启

重启后,设置页 →「本地快速接话」开启。**接话模式**:

- **rules**(默认,零依赖):纯规则分类(关键词 + 情绪词典),开箱即用,
  **不下载任何模型**。
- **hybrid**(可选增强):规则优先;规则无命中时由 **probe**(bge-small-zh 句向量
  + 标注数据训练的逻辑回归头)补足中文情感/意图泛化。需在设置页「接话模型」处
  安装 bge-small-zh;**未安装会自动降级到纯规则,不报错、不强行接话**。

## 当前清单

- `sakura/manifest.json` — 夜乃桜,16 个模板 / 84 条变体,含 greeting 社交子类
  (报到 / 早安 / 晚间 / 睡前)与相位条目(tool_running / long_wait /
  repeated_issue)。schema 与 `app/backchannel/manifest.py` 加载器对齐。

## 自定义 / 新增角色清单

清单顶层是 `{"schema": "...", "templates": [ ... ]}`,每个模板形如:

```json
{
  "tone": "中性",
  "portrait": "站立待机",
  "intent": "support",
  "emotion": "sad",
  "variants": [
    { "ja": "うん……", "zh": "嗯……", "audio": null }
  ]
}
```

- `intent`:命中意图,取值 question / request / error / complaint / support /
  positive / affection / greeting(及 greeting 子类)。
- `emotion`:命中情绪,取值 neutral / confused / anxious / frustrated / sad /
  angry / happy / playful / embarrassed。
- `portrait`:**必须在该角色 `character.json` 的表情词表内**,否则该条会被加载器
  跳过(避免引用不存在的立绘)。
- `phase`(可选):tool_running / long_wait / repeated_issue,相位匹配优先于意图。
- `variants`:`ja` 为日语原文(TTS 用),`zh` 为中文字幕;`audio` 留空则运行期
  现合成,见下。

匹配优先级:相位 > (意图, 情绪)精确 > 同意图 > 意图家族根 > 兜底池。

## 语音

`audio` 为空时,运行期按当前角色 TTS 现合成并持久化到
`data/backchannels/<id>/audio/`(声线指纹失效会自动重合成);也可离线预合成后
把路径填进 `audio` 字段,随包分发免首次合成延迟。
