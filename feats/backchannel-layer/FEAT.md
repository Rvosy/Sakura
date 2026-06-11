# Feat: 本地快速接话层(Local Backchannel Layer)

> 状态:提案中 | 原始提案:[Rvosy/Sakura#41](https://github.com/Rvosy/Sakura/issues/41) | 本文档 = 提案 + 评审意见 + 文献调研的合并版(2026-06)

## 1. 背景与目标

用户发消息后,主 LLM(商业 API + 视觉 + 工具循环)返回前存在数秒空窗。目标是在等待期显示一句**很短的、带角色味道的过渡反应**(字幕 + 表情 + 可选预合成语音),降低等待感。

**非目标**:不替代主 LLM、不生成事实性回答、不承诺工具执行结果、不引入本地生成式 LLM。

这一行为在口语对话系统研究中有正式名称:**backchannel / conversational filler**。HRI 经典实验(Shiwa et al. 2008,见 §6)证实:用户对机器人响应延迟的偏好峰值在 1 秒,而语气词填充能显著缓和长延迟的负面印象——这是本功能的直接实证依据。

## 2. 总体架构

```
用户消息 ──┬─→ 主 LLM 请求(完全不变)
           └─→ BackchannelController(延迟 delay_ms)
                  ├─ 主 LLM 已返回 → 跳过
                  └─ 三层决策:
                       ① 硬规则短路(高精确:报错栈、强烈情绪标点)
                       ② 加权融合(intent 嵌入原型 + emotion 规则 + FSM 相位先验 + 相容性矩阵)
                       ③ 低于阈值 → 角色 fallback 模板
                  → 临时 ChatSegment(字幕 + tone/portrait + 可选预合成音频)
                  → 正式回复到达 → 立即抢占(音频 ~100ms 淡出)
```

三个模块(对应原提案):`BackchannelClassifier` / `BackchannelTemplateResolver` / `BackchannelController`,外加一个微型会话相位 FSM(见 §4)。

**关键接入点**:现有代码在发消息后已显示 `"......"` 占位符(`pet_window.py` `cancel_reply_flow("......")`)。本功能本质是把这个占位符升级为分类驱动的角色化反应,UI 通路已存在。

## 3. 硬性约束(评审补充,必须写进实现)

1. **临时 ChatSegment 不进任何持久层**:不进回复历史翻页(`_remember_reply_history_segments`)、不进 JSONL 聊天记录(`_record_history`)、**不进发给 LLM 的 messages 上下文**(否则模型会把"我看看"当成自己说过的话接着演)。
2. **不进分段播放队列**:接话走轻量字幕路径(`set_speech` 级),正式分段绝不能排在接话后面等播完;正式回复到达即取消接话。
3. **防重复是规格不是建议**(Shiwa 后续研究证实 filler 效果随重复衰减):同句不连续出现两次;同 `(intent, emotion)` 短期内轮换;支持触发概率配置(如 0.6)。
4. **作用范围**:仅用户主动消息;proactive care/提醒事件不接话;`startup_initializing`、历史翻页查看模式中不触发。
5. **性能预算**:规则 <10ms;嵌入分类 warmup 后 <50–100ms;分类超时直接跳过,绝不阻塞主流程;分类/encode 不跑 UI 主线程。

## 4. 分类设计:意图 + 情绪混合

学术依据:对话行为与情绪联合建模研究(DCR-Net、DARER)表明两个任务互相增益;ISO 24617-2 标准确认对话行为是**多维并行标签**(本功能的接话行为本身 = 该标准 Time Management 维度的 Stalling + Auto-Feedback)。

**分工(关键评审结论)**:
- **intent → 嵌入原型分类**:复用记忆系统已加载的 `all-MiniLM-L6-v2`(`app/agent/memory.py`),零额外下载。离线为每个意图标签写 10–30 句典型**用户说法**,向量化取均值得原型;运行时一次 encode + 余弦相似度。原型向量预计算缓存(按内容 hash 失效)。注意:**不是**拿用户输入和模板回复文本匹配——输入和回复在语义空间不相近,必须匹配"标签的例句原型"。
- **emotion → 规则分类**:sentence embedding 是话题主导的,且 MiniLM 本质是英文模型,捕捉不了中文情绪 nuance;而情绪线索(标点 !!!/……、语气词"唉/呜/靠/气死"、emoji/颜文字、叠字)恰好是规则最擅长的。词典可用大连理工情感词汇本体。

**融合公式**(第二层):

```
score(label_pair) = 0.45·sim_intent + 0.25·score_emotion + 0.15·prior_context + 0.15·compat(intent, emotion)
```

- `prior_context`:微型 FSM 相位先验。状态 ≤5 个:`fresh / followup / repeated_issue / tool_running / long_wait`。解决纯分类器盲区:用户第三次发"还是不行",intent 不变,但接话必须从元气的"我看看!"降级为"唔……我再仔细看看"。这是信息状态更新(ISU)思想的最小实现;POMDP 级对话管理对本场景是过度设计(不确定性低、动作空间小、错误代价小)。
- `compat`:手写意图×情绪相容性矩阵(`complaint+angry` 常见、`question+happy` 罕见),否决不合理标签对——来自联合建模研究的可借鉴思想。
- 权重为起手值,靠 debug 日志(记录输入、各路得分、最终选择)线下调。

## 5. 角色模板与预合成语音

模板跟随角色包:`characters/<id>/backchannels.json`,**缺文件即视为 opt-out**(不用全局兜底句,保护角色感)。

文本与音频绑成对(不用平行数组,避免错位);`audio` 可选,缺失静默降级为纯字幕:

```json
{
  "intent": "question", "emotion": "confused",
  "zh": [
    {"text": "我先帮你理一下。", "audio": "audio/zh_question_01.wav"},
    {"text": "等一下，我看看问题在哪。", "audio": "audio/zh_question_02.wav"}
  ]
}
```

**预合成语音规格**(替代原提案"v1 无 TTS"——反对的是运行时合成延迟,预合成无此问题且沉浸感更好):
- 离线用该角色运行时同一套 GPT-SoVITS 参考音频合成(最好按 tone 用对应参考);`tools/` 下提供批量合成脚本,角色作者改 voice ref 后重跑。
- 导出 16-bit PCM wav、与运行时 TTS 同采样率(走 `AudioSinkPlayer` 直写路径,避免触发 QMediaPlayer 回退);离线做响度归一,对齐运行时 TTS 音量。
- 单条 ≤2 秒;正式回复 TTS 到达时接话音频 ~100ms 淡出让位,绝不让正式回复等接话播完。
- 受双层开关:全局 TTS 开关 AND `backchannel.tts_enabled`。
- tone/portrait 值须与角色包词表对齐,加载时校验 portrait 存在,缺失回退 fallback。

## 6. 文献依据(PDF 见 references/,索引见其 README)

| 主题 | 文献 | 对本功能的意义 |
|------|------|----------------|
| 延迟与 filler 实证 | Shiwa et al. 2008(ACM,链接见索引)+ 2009 期刊版 | 响应偏好峰值 1s;filler 缓和长延迟;**习惯化效应 → 防重复是必要机制** |
| LLM 虚拟人延迟掩盖 | llm-iva-response-delay-2025.pdf | 2025 年同题工作:LLM 驱动虚拟人的填充策略,最接近本功能的现代实现 |
| 工业实现 | patent-us11245646(Google)、patent-us8355484(Nuance) | 预测性 filler 注入;"正式回复就绪即抢占 filler"先例 |
| 规则反馈有效性 | gratch2007-rapport-agent.pdf | 纯规则驱动的即时反馈在建立融洽感上不输真人;**关键是 contingency 不是智能** |
| 对话行为分类标准 | iso24617-2-dialogue-acts.pdf | 多维并行标签;接话 = Stalling + Auto-Feedback |
| 意图+情绪联合建模 | dcrnet2020 / darer2022(+MIRER,链接见索引) | 两任务互相增益;相容性矩阵的依据 |
| 轻量意图分类参考 | diet2020-rasa-intent.pdf | 工业级轻量 NLU 架构;本项目用原型分类即够(无需训练栈) |
| 情绪→表现映射 | bartneck2002-occ-model.pdf、fatima2019-emotion-agent.pdf | 评估(user_emotion)与表达(sakura_tone)分离原则 |
| 对话管理谱系 | survey2023-dialogue-management-hri.pdf | FSM→ISU→POMDP;论证本功能停在微型 FSM 即可 |
| 语音 backchannel 前沿 | vap2024 / fukunaga2025 / lm-backchannel-filler-repr-2025 | 语音流实时预测(比本场景难);文本回合制是简化版问题 |

## 7. 路线图

- **v1(规则)**:配置项 + 硬规则分类 + 模板解析 + 占位符替换 + 防重复 + 预合成语音播放/抢占。默认关闭。
- **v2(混合)**:intent 嵌入原型分类(复用记忆模型,共享 encode 入口需线程安全)+ 融合公式 + debug 日志。
- **v3(相位)**:会话相位 FSM;`tool_running` 状态的工具循环进度接话("还在查……")——**最长的等待其实是工具循环(几十秒),这是后续收益最大的方向**。

## 8. 配置(汇总)

```yaml
backchannel:
  enabled: false        # 默认关
  mode: rules           # off | rules | hybrid
  delay_ms: 600
  timeout_ms: 80
  probability: 1.0      # 触发概率,防罐头感可调低
  show_subtitle: true
  show_portrait: true
  tts_enabled: false    # 播放预合成音频(还受全局 TTS 开关约束)
```

设置页只暴露开关+模式,其余留 YAML 高级配置。

## 9. 数据结构与存储(2026-06-11 定稿)

**语音位置:两层存储。** 角色包层(随角色卡分发,运行时只读):`characters/<id>/backchannels/{manifest.json, audio/}`,`character.json` 加可选 `backchannel` 字段引用,缺省即 opt-out。本地覆盖层(用户机器可写):`data/backchannels/<character_id>/{overlay.json, candidates.jsonl, audio/}`。Resolver 运行时合并,overlay 可新增模板、禁用 pack 条目。分层理由:`characters/` 是不可变分发资产(CLAUDE.md + .gitignore),角色包升级会整体覆盖;作者迭代写包、用户侧模型自更新写 data/。

**注意:合成音频不能放 `data/cache/tts`** —— 该目录启动时被 `purge_tts_cache()` 清空(app/voice/tts.py)。

**manifest 条目**(2026-06-11 草案审查后修订):稳定 `id`(供 overlay 禁用/候选关联/防重复轮换引用)+ `intent/emotion/tone/portrait`(tone、portrait 用角色包自己的词表)+ 可选 `phase`(相位键:`repeated_issue/tool_running/long_wait`,带 phase 的条目按 phase 优先匹配、可不带 intent/emotion)。**变体必须 zh/ja 配对**(与 ChatSegment 同构,音频只从 ja 合成、字幕可显示 zh,独立语言池无法对应"正在播的音频↔显示的字幕"):

```json
{ "id": "q_confused_01", "intent": "question", "emotion": "confused",
  "tone": "困惑", "portrait": "张嘴疑问",
  "variants": [
    { "ja": "少し整理してみる。", "zh": "我先帮你理一下。",
      "audio": "audio/q_confused_01_0.wav", "voice_fp": "sha256:…", "synth_at": "…" }
  ] }
```

fallback 用 `intent: "fallback"` 的普通条目表达(兜底池可多句轮换),不再用顶层单句对象。`voice_fp = sha256(gpt_model 名 + sovits_model 名 + ref.txt 内容)` 截断——声线变更检测,驱动增量重合成。

**resolver 匹配优先级**:phase 命中 > (intent, emotion) 命中 > fallback 池。**词表对齐是硬约束**:分类器输出标签集与模板键标签集必须同一张表(草案审查发现两套词表漂移会产生永远不可达的死条目)。

**词表定稿(2026-06-11)**:意图 = `question / request / error / complaint / support / positive / affection`(+`fallback` 兜底池标记);情绪 = `neutral / confused / anxious / frustrated / sad / angry / happy / playful / embarrassed`;相位 = `repeated_issue / tool_running / long_wait`(纯相位条目不带 intent/emotion)。`chat`(闲聊)**有意不设标签**——分类器低置信或闲聊输入直接落 fallback 池,fallback 的中性确认句即闲聊接话。

**三条更新通路,收敛到同一合成函数**(复用 `GPTSoVITSTTSProvider.prepare(text, tone)`,tone→参考音频选择已内置):

1. **人工(作者)**:`tools/backchannel_synth.py` 离线脚本——筛 audio 缺失或 voice_fp 不匹配的条目,逐条合成、响度归一、从 cache 拷入角色包、回写指纹。幂等增量。
2. **用户确认**:设置页审核候选,确认时触发合成,落 data/ overlay。
3. **模型提案**:内置工具 `backchannel_propose(intent, emotion, zh_text, ja_text, tone)`(走 ToolRegistry,risk=medium)→ 仅追加 `candidates.jsonl`。照搬记忆系统 candidate→confirmed 治理:模型提案的话术+语音与写入长期记忆同风险级,必须人工确认;模型没有直接写模板库的路径。

**代码锚点**:character_loader.py(仿 `CharacterVoice` 加载 `BackchannelManifest`)、app/storage/paths.py(新路径)、tts.py `prepare()`(合成复用)、app/agent/tools/builtin/provider.py(propose 工具)、app/agent/memory.py(候选模式参照)。

## 10. 与流式输出的关系

真正降低(而非掩盖)延迟的是流式输出——分段 JSON 协议天然适合收到一个完整 segment 就先播。但流式与工具循环、JSON 修复重试的改造量大得多。接话层是低成本互补方案,二者不互斥;若未来实现流式,接话窗口缩短但机制不变。
