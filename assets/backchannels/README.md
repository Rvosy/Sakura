# 接话模板资产(backchannels manifest)

本地快速接话层的角色模板清单。与框架代码分离单独维护:框架只读取
`characters/<id>/backchannels/manifest.json`,这里存放各角色的最新清单
作为分发源,避免把具体角色内容混进框架。

## 安装

把对应角色的 manifest 复制到角色包,并在 `character.json` 引用:

```bash
mkdir -p characters/sakura/backchannels
cp assets/backchannels/sakura/manifest.json characters/sakura/backchannels/manifest.json
```

`characters/sakura/character.json` 增加一行(缺该字段即视为该角色 opt-out):

```json
"backchannel": "backchannels/manifest.json"
```

重启后在设置页开启「本地快速接话」即生效。

## 当前清单

- `sakura/manifest.json` — 夜乃桜,16 个模板 / 84 条变体,含 greeting
  社交子类(报到/早安/晚间/睡前)与相位条目(tool_running/long_wait/
  repeated_issue)。schema 与 `app/backchannel/manifest.py` 加载器对齐。

## 语音

manifest 的 `audio` 字段为空时,运行期按当前角色 TTS 现合成并持久化到
`data/backchannels/<id>/audio/`(声线指纹失效);也可离线预合成后填入
`audio` 路径随包分发。
