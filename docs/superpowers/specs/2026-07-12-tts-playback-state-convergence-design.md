# TTS 播放状态收敛设计

## 1. 背景

当前 TTS 播放链在 `QAudioSink` 成为默认后端后，仍保留了旧 `QMediaPlayer` 主后端时期的配置选择器、启动预热和完成兜底。正常配置始终使用 `QAudioSink`，但宿主仍会无条件预热备用 `QMediaPlayer`，并从只读配置键 `playback_backend` 恢复一个 UI 从不保存、仓库配置从不声明的内部开关。

完成状态也存在两处真实性问题：

1. 已知时长超过约 58.5 秒的合法音频仍会在 60 秒被 watchdog 强制完成，字幕与队列会提前推进，剩余音频被停止。
2. `AudioSinkPlayer.finished` 已携带音频路径，但上层忽略该值；旧播放器的迟到完成信号理论上可以结束已经切换后的新音频。

此外，`AudioSinkPlayer` 同时维护 `_finishing`、`_finished_emitted`、`_ever_active` 等状态，其中后两者没有提供独立判定价值，文件内还有重复 `@Slot()` 和未使用的局部状态。

## 2. 目标与非目标

### 目标

- 保持用户可见的 TTS 开关、Provider、播放顺序、字幕回调、错误提示和 UI 不变。
- `QAudioSink` 是唯一主播放路径；`QMediaPlayer` 只在 sink 无法启动时惰性创建，继续承担真实兼容兜底。
- 删除未形成产品能力的 `playback_backend` 配置字段、常量和加载兼容层。
- 删除只会预热备用 `QMediaPlayer` 的启动/设置保存 warmup 链。
- 已知时长的合法音频按完整时长加宽限期安排 watchdog，不受 60 秒上限截断。
- sink 完成信号必须与当前音频路径一致才可推进队列。
- 删除不参与完成判定的 sink 状态和死测试表面，生产代码删除量大于新增量。

### 非目标

- 不删除 `QMediaPlayer` 格式/设备兼容兜底。
- 不改变 TTS 合成、服务启动、预生成音频、接话音频或 Provider 切换逻辑。
- 不新增播放设置 UI，不改变角色包和 `api.yaml` 正常配置格式。
- 不重写 `QAudioSink` 的 PCM 写入算法或 Qt 线程模型。
- 不触碰 ResourceManager 退出竞态；该主题单独进入下一轮。

## 3. 方案比较

### 方案 A：单一主路径、惰性兼容兜底（采用）

所有音频先进入 `AudioSinkPlayer`。只有 `start()` 返回 `False` 时才创建并使用 `QMediaPlayer`。删除外部后端选择和备用播放器预热。

优点：正常播放语义不变，备用能力仍在；删除隐藏配置和无效启动工作；状态边界最清楚。缺点：真正触发兼容兜底的第一段音频会承担一次 `QMediaPlayer` 冷初始化。

### 方案 B：只保留 QAudioSink

删除整个 `QMediaPlayer` 后端。

优点：净删最大。缺点：压缩 WAV、非 16-bit PCM 或默认设备不接受目标格式时将直接失败，改变现有兼容边界。

### 方案 C：继续暴露双后端内部配置

保留 `playback_backend`，为两个主路径分别补状态和测试。

优点：保留手工强制旧后端的能力。缺点：该能力没有 UI、没有保存路径、没有仓库配置使用者，却要求永久维护两套主路径和预热状态；与删除优先目标相反。

## 4. 播放后端所有权

`TTSPlaybackEndpoint._play_next()` 不再读取后端配置，完成音频校验后直接调用 sink 路径。sink 启动失败时，现有 `_play_next_with_media_player()` 继续接管同一个 `_current_audio`，因此回调、清理、队列顺序和 watchdog token 不变。

删除以下虚构配置表面：

- `GPTSoVITSTTSSettings.playback_backend`
- `TTS_PLAYBACK_BACKEND_AUDIO_SINK`
- `TTS_PLAYBACK_BACKEND_MEDIA_PLAYER`
- `AppSettingsService.load_tts_settings()` 对 `tts.playback_backend` 的只读恢复
- `TTSPlaybackEndpoint` 的构造参数和 `_playback_backend` 状态

设置服务从未保存该键，设置 UI 和仓库数据也从未提供该键，因此正常程序配置不发生变化。历史手工键会像其他未知 YAML 字段一样被忽略。

## 5. 删除备用播放器预热

当前 `warm_up_playback()` 只创建 `QAudioOutput + QMediaPlayer`，没有预热默认 `QAudioSink`。在主路径改为 sink 后，它已经从“当前播放器预热”退化为“无条件提前创建备用播放器”。

删除 Provider 协议、Null Provider、真实 Provider、播放端点以及 `PetWindow` 中整条 playback warmup 调用链。TTS 服务的 `ensure_ready()` 后台预热继续保留，它负责真实的服务进程和角色权重准备，与播放器 warmup 不同。

## 6. 完成状态真实性

### Watchdog

音频已经在进入播放前通过 WAV 校验。若时长可解析，watchdog 延迟固定为：

```text
max(2000 ms, duration_ms + 1500 ms)
```

不再对合法长音频套用 60 秒上限。只有时长仍意外不可解析时，才使用 60 秒未知时长兜底，避免永久挂起。

### 迟到 sink 信号

`_on_sink_finished(reason, audio_path)` 在推进前比较 `Path(audio_path)` 与 `_current_audio`。不一致代表信号已过期，只记录并忽略；不得停止当前音频、触发完成回调或推进队列。

现有 `_playback_finish_token` 继续保护 watchdog；路径校验保护 sink 自身完成信号，两者覆盖不同来源。

## 7. AudioSink 状态收敛

`AudioSinkPlayer._finishing` 已经从首次完成开始永久为真，足以保证 `_finish_once()` exactly-once。删除同义的 `_finished_emitted`。

`_ever_active` 不参与当前完成条件；完成条件实际是“PCM 全部写入后收到 IdleState”或 drain timer 到期。删除该字段、赋值和过时文档。同步删除：

- 重复的 `@Slot()`；
- `_finish_if_drained()` 中只计算但从未使用的 sink 状态名；
- `_on_sink_state_changed()` 中未使用的 `is_active` / `is_idle`；
- 无生产调用的 `cancel()` 别名及只证明内部 helper 的空壳测试。

保留 `stop()`，它仍由播放端点在完成、错误和关闭路径调用。

## 8. 错误处理与兼容边界

- sink 不支持格式、设备格式不兼容或启动失败：继续 fallback 到 `QMediaPlayer`。
- 两个后端的错误仍显示现有 TTS 错误并完成当前字幕回调。
- watchdog 只负责播放器未给出完成信号时推进，不再截断已知合法长音频。
- 迟到信号被忽略，不向用户显示错误；它属于内部时序噪声。
- Provider 关闭、迟到合成结果、临时文件清理语义不变。

## 9. 测试策略

严格 TDD：

1. 结构测试先证明 `playback_backend` 字段和后端选择常量仍存在；生产修复后断言它们退出配置模型和加载路径。
2. 播放路径测试先要求 `_play_next()` 无条件尝试 sink，现有可配置分支因此失败；保留 sink 启动失败转入 media player 的行为测试。
3. UI/Provider 结构测试先要求 playback warmup API 消失，并确认 TTS 服务 warmup 仍被调用。
4. 长音频测试把 120 秒 WAV 的期望改为 `121500 ms`，先看到当前 60000 ms 截断失败。
5. 新增迟到 sink 完成测试：旧路径不得完成当前路径。
6. sink 状态测试覆盖重复完成只 emit 一次、Idle/drain 完成和 start 失败不误完成；结构扫描确认冗余字段与 `cancel()` 退出生产代码。

每个提交后运行最相关的 unit/UI，先规格 review 再代码质量 review；发现问题后 amend。最终运行 compileall、TTS/unit、integration、UI 与全量 pytest。

## 10. 提交组织与验收

计划提交：

1. `refactor: make media player a lazy tts fallback`
2. `fix: preserve truthful tts completion`
3. `refactor: consolidate audio sink completion state`

验收标准：

1. 正常音频固定先走 AudioSink，media player 只在 sink 启动失败时创建。
2. 无 `playback_backend` 配置字段、加载分支或后端选择状态。
3. 无 playback warmup 链；TTS 服务 warmup 保留。
4. 已知长音频不会在 60 秒被提前完成。
5. 旧 sink 完成信号不能推进新音频。
6. sink 完成只有一个布尔状态来源。
7. 相关与全量测试通过，`app/`、`plugins/` 生产删除量大于新增量。
8. 工作树只保留用户原有 `link_sakura_runtime_tts.bat`。
