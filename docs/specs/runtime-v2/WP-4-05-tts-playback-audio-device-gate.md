---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-20
---

# WP-4-05 TTS、播放与音频设备门禁规范

## 产品行为

- `assistant.tts-v1` 只为已完成聊天中的 `operationId + segmentIndex` 合成，`suppressTts` 和语言守卫必须
  fail closed。WebView 不得提交文本、路径、generation 或音频描述符。
- 音频实际开始后，以同一个 playback-start 边界同时启动当前段立绘切换和字幕打字；首段等待合成与播放启动时
  必须继续显示回复等待省略号，直到该门禁打开，不得在聊天文本终态到达时留下空白气泡。字幕和音频均终止后
  推进下一段。当前段开始后预生成下一段；任何合成、设备或播放失败立即降级为字幕并从同一门禁启动立绘与
  字幕，不改变聊天终态。历史导航不自动重播。
- 输出始终使用播放时的系统默认设备；不提供设备选择器。设备断开只结束当前项，下一次播放重新探测。
- Provider 插件拥有自身 Endpoint、健康检查、可选预热和 Managed Runtime；Runtime v2 Core 不再读取
  Provider 私有配置，也不在 session-ready 时构造具体实现。首次合成可以触发 Provider 自有启动；失败只降级
  字幕，不阻塞 Core readiness。设置页读取状态不得触发服务启动、旧进程清理或全 Provider 探测。
- Provider 列表来自 `sakura.tts` Hub，Core、Rust 和 Runtime v2 Voice 页面不得枚举具体实现 ID。角色级启用开关
  与已选 Provider 分别保存；关闭时保留选择和 Provider 配置。
- `availability` 只表示 Provider 根据自身配置判断当前可参与合成，不承诺 Endpoint 已可达。旧 Core 专用
  bundle/test 接口在 Plugin Kernel v3 原子切换时下线；`voice.bundle` 明确标记 `unavailable`，待模型安装、
  取消、进度和固定测试音成为 Provider/Hub 的普通插件贡献后再重新开放，不得恢复 TTS 专用 Bridge 分支。

## 进程与数据

- Managed GPT-SoVITS 启动前终止同一用户且精确匹配当前配置的旧进程树，等待端口释放后创建当前 Core
  generation 的受控子进程。未知端口占用者不得终止。Custom Endpoint（包括 loopback）只允许连接探测和
  合成，永不启动、接管、重启、切模型或停止服务。
- macOS/Linux 的 Provider Managed Runtime 和转换进程必须继承 Rust generation process group，不得创建
  新 session 逃逸最终树回收；Provider 正常停止只清理自己创建的 PID 后代。
- 非 loopback Custom Endpoint 的参考音频路径固定映射为
  `<remote_reference_root>/<character_id>/<角色包内相对路径>`；根目录缺失或参考音频逃逸角色包必须返回
  `REFERENCE_AUDIO_UNAVAILABLE`，不得发送客户端本地路径。Runtime v2 不上传参考音频，也不管理远程模型。
- 成功的聊天合成原子写入 recording；测试音和失败/跳过请求不留存。每角色最多 100 条非收藏 recording，
  收藏不计入上限。损坏或未来 schema 只隔离对应记录。
- 持久 recording 与 generation 临时播放副本分离；启动清理只触碰临时目录。跨边界 DTO 不含裸路径。

## 接口、故障与回退

Core 只开放 TTS synthesis、动态 settings/status 和 playback-observe allowlist；合成只调用 `sakura.tts`
的 `begin/poll/cancel`，Hub/角色未配置、关闭或 Provider 不可用时明确失败，不得回落 legacy factory。
`tts.settings.get` 与 `tts.status.get` 返回 schema v2 的角色选择、动态 Provider 列表和 `surface=voice` 普通
Settings sections，不含音频路径、正文、凭据或 Provider 私有字段。Core 发布 synthesis 唯一终态；Rust
开放准备、播放、停止和设置 commands，并发布 playback 唯一终态。旧 generation、重复消费、逃逸/symlink、
超大或无效 WAV 必须拒绝。回退关闭 capability、停止服务和播放，但不得删除 recording、收藏、旧配置、
已安装 bundle、新插件配置或下载分片。

设置页必须由 Runtime v2 voice controller 独占 TTS 控件。Provider 选择器来自 Hub，Provider 私有字段只通过
`surface=voice` 声明式 Settings section 呈现；保存 Provider section 与角色选择不承诺跨文件事务，部分成功
必须返回逐步结果并刷新真实快照。旧固定 Provider 字段、bundle 轮询和测试命令不得在 Runtime v2 暗中继续
可调用。

Python Provider startup/process cleanup/settings/synthesis/recording 与 Rust playback 的真实终态都写入统一
`data/logs/sakura-runtime.log`。日志只允许稳定标识、Provider、端口、状态/阶段、进度、字节数、HTTP 状态、
耗时和重试次数；不得记录文本、凭据、音频路径或完整 Provider 响应。Rust 必须先在音频回调源头落日志，Core
observe 或插件发布失败不得吞掉播放证据。

自动验证覆盖 Python/Rust/WebView 纵向链；Windows WASAPI、macOS CoreAudio 和 Linux PipeWire/Pulse/ALSA
真实默认设备及旧 GPT-SoVITS 清理是项目负责人验收前的硬门。
