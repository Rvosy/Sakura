---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-05
---

# WP-4-05 TTS、播放与音频设备门禁规范

> ADR-0032 补充：GPT-SoVITS/Genie 配置在合成边界原位应用。活动合成使用旧配置完成；timeout、参考目录
> 等请求参数不重启服务；managed runtime 身份变化只停止对应子进程并懒启动，custom endpoint 永不由
> Sakura 终止。普通设置保存不得重启无关插件进程或清空已加载权重。

## 产品行为

- `assistant.tts-v1` 只为已完成聊天中的 `operationId + segmentIndex` 合成，`suppressTts` 和语言守卫必须
  fail closed。WebView 不得提交文本、路径、generation 或音频描述符。
- 音频实际开始后，以同一个 playback-start 边界同时启动当前段立绘切换和字幕打字；首段等待合成与播放启动时
  必须继续显示回复等待省略号，直到该门禁打开，不得在聊天文本终态到达时留下空白气泡。字幕和音频均终止后
  推进下一段。当前段开始后预生成下一段；任何合成、设备或播放失败立即降级为字幕并从同一门禁启动立绘与
  字幕，不改变聊天终态。历史导航不自动重播。
- 输出始终使用播放时的系统默认设备；不提供设备选择器。设备断开只结束当前项，下一次播放重新探测。
- Provider 插件拥有自身 Endpoint、健康检查、预热和 Managed Runtime；Runtime v2 Core 不读取 Provider 私有
  配置，也不构造具体实现。当前角色启用 TTS 且选中 Sakura 托管 Provider 时，Core 在启动期 Session 发布后
  通过 Hub 排队后台预热，由 Provider 启动服务并准备当前角色权重；该过程不阻塞 Core readiness，失败后首次
  合成仍可再次准备并只降级字幕。Custom Endpoint 启动预热不得连接、启动、接管或探测外部服务。设置页读取
  状态不得触发服务启动、旧进程清理或全 Provider 探测。
- Provider 列表来自 `sakura.tts` Hub，Core、Rust 和 Runtime v2 Voice 页面不得枚举具体实现 ID。角色级启用开关
  与已选 Provider 分别保存；关闭时保留选择和 Provider 配置。
- `sakura.tts` Hub、当前选中的 Provider 插件或角色级 TTS 开关被明确关闭时，Core 必须在段落授权阶段把
  该段投影为 `suppressTts=true`，WebView 直接走字幕且不得发起合成；插件仍启用但 Worker、Service 或
  Provider 异常时保留故障诊断，不得把运行故障伪装成用户关闭。
- Voice 页面把 Provider 作为“语音引擎”呈现，只显示 `pluginId == providerId` 的当前引擎设置区块；内置
  Provider 统一使用“服务来源”区分 `Sakura 内置` 与 `连接已有服务`。GPT-SoVITS 缺少显式模式的旧配置按
  `customBaseUrl` 推导，保存后写入 `endpointMode`，切换模式不得丢弃非活动服务地址。
- Genie 的 `Sakura 内置` 固定使用内部 loopback 端点并自动绑定当前 TTS 根下已安装的 `cpu` 整合包；不得要求
  用户填写地址或工作目录。`连接已有服务` 才读取用户地址，且不启动本地进程。Genie/GPT-SoVITS 已安装
  整合包的历史空或过期运行路径在插件启动时补齐；持久化和子进程边界不得暴露 Windows `\\?\` /
  `\\?\UNC\` 前缀。
- `availability` 只表示 Provider 根据自身配置判断当前可参与合成，不承诺 Endpoint 已可达。旧 Core 专用
  bundle/test 接口在 Plugin Kernel v3 原子切换时下线；`voice.bundle` 明确标记 `unavailable`，待模型安装、
  取消、进度和固定测试音成为 Provider/Hub 的普通插件贡献后再重新开放，不得恢复 TTS 专用 Bridge 分支。

## 进程与数据

- Managed GPT-SoVITS 只创建和回收本插件拥有的受控子进程；启动前遇到端口占用时返回 `TTS_PORT_OCCUPIED`，
  不扫描、接管或终止已有监听者。Custom Endpoint（包括 loopback）只允许连接探测和
  合成，永不启动、接管、重启、切模型或停止服务。
- macOS/Linux 的 Provider Managed Runtime 和转换进程必须继承 Rust generation process group，不得创建
  新 session 逃逸最终树回收；Provider 正常停止只清理自己创建的 PID 后代。
- 非 loopback Custom Endpoint 的参考音频路径固定映射为
  `<remote_reference_root>/<character_id>/<角色包内相对路径>`；根目录缺失或参考音频逃逸角色包必须返回
  `TTS_REFERENCE_AUDIO_UNAVAILABLE`，不得发送客户端本地路径。Runtime v2 不上传参考音频，也不管理远程模型。
- 成功的聊天合成原子写入 recording；测试音和失败/跳过请求不留存。每角色最多 100 条非收藏 recording，
  收藏不计入上限。损坏或未来 schema 只隔离对应记录。
- 持久 recording 与 generation 临时播放副本分离；启动清理只触碰临时目录。跨边界 DTO 不含裸路径。

## 接口、故障与回退

Core 只开放 TTS synthesis、动态 settings/status 和 playback-observe allowlist；合成只调用 `sakura.tts`
的 `begin/poll/cancel`，Hub/角色未配置、关闭或 Provider 不可用时明确失败。Core 不保留旧 Provider Registry、
合成队列或 Managed Runtime 实现。旧 TTS 配置只在显式导入时转换，并由当前 Provider 插件 parser 校验；
角色声线清单由各 Provider 插件读取，普通启动不读取旧 `api.yaml.tts`。
`tts.settings.get` 与 `tts.status.get` 返回 schema v1 的角色选择、动态 Provider 列表和 `surface=voice` 普通
Settings sections，不含音频路径、正文、凭据或 Provider 私有字段。Core 发布 synthesis 唯一终态；Rust
开放准备、播放、停止和设置 commands，并发布 playback 唯一终态。旧 generation、重复消费、逃逸/symlink、
超大或无效 WAV 必须拒绝。回退关闭 capability、停止服务和播放，但不得删除 recording、收藏、旧配置、
已安装 bundle、新插件配置或下载分片。没有当前角色时 schema v1 的 `character` 与 `selection` 为 `null`，
`providers` 与 `sections` 仍按 Hub 和通用 Settings surface 返回。已选角色但聊天 Provider 尚未配置时，TTS
设置必须使用当前 generation 已发布的角色身份，不能因 Assistant Session 尚未创建而把角色误报为未选择。

设置页必须由 Runtime v2 voice controller 独占 TTS 控件。Provider 选择器来自 Hub，Provider 私有字段只通过
`surface=voice` 声明式 Settings section 呈现；保存 Provider section 与角色选择不承诺跨文件事务，部分成功
必须返回逐步结果并刷新真实快照。旧固定 Provider 字段、bundle 轮询和测试命令不得在 Runtime v2 暗中继续
可调用。`sakura.tts` Hub 未安装、未启用，或当前没有已启用的 Voice Provider 时，Voice 页面不得保留禁用的
角色语音表单；页面统一显示“语音管理暂不可用”、重新检查和前往插件页入口。重新检查必须先刷新通用插件
Snapshot，再决定是否读取 Voice Snapshot。没有当前角色时仍须展示 Hub 返回的 Provider 列表和 Provider 全局
Settings section；角色级启用必须禁用，引擎选择仅用于切换全局配置区块且不得保存为角色选择，并明确提示先
导入、选择角色；不得把缺少角色伪装成插件不可用。

声明式 Settings 字段允许 `placement=advanced`；Voice controller 必须把这类字段放入默认收起的“高级设置”，
不得根据内置 Provider ID 或私有字段名硬编码布局。字段可用 `enabledWhen={field, equals}` 声明同区块内的
条件可编辑关系；条件不满足时保留字段值但禁用控件并呈灰色。内置 TTS 的外部服务设置仅在“连接已有服务”
时可编辑。展示 Windows 路径时移除 `\\?\` / `\\?\UNC\` 内部前缀，运行时仍接受历史值。

Managed Genie 的模型路径、参考表和语言按字段读取：`sakura.tts.genie` 显式配置优先，其次是
`sakura.tts.gpt-sovits`，最后兼容旧 `voice`。继承值只在准备语音时读取，不复制为 Genie 的持久覆盖值；
已有 Genie 字段（包括显式空值）和其他插件配置保持不变。未配置参考表或 ONNX 目录时，分别尝试角色包内
`voice/refs/ref.txt` 和 `voice/onnx`。角色包迁移补齐缺失扩展，已有 Hub 的启用状态和 Provider 选择不变。
Studio 更新共享源权重后，Genie 下次预热或合成读取新路径；显式 Genie 路径仍由用户管理。

预热与合成使用同一模型准备流程：优先复用含非空 ONNX 文件的目录；目录缺失、为空或只有零字节模型时，
完整 GPT/SoVITS 源权重进入既有转换队列。转换按源文件哈希复用缓存，成功后提交临时目录，失败或取消时
清理临时产物和转换进程。缺少参考资源、源权重或转换工具分别返回 `TTS_REFERENCE_UNAVAILABLE`、
`TTS_SOURCE_MODEL_UNAVAILABLE`、`TTS_ONNX_CONVERSION_UNAVAILABLE`；没有 ONNX 且未配置完整源权重时
返回 `TTS_ONNX_UNAVAILABLE`。同步错误通过 Provider 返回值跨进程传递，后台预热错误写入统一诊断日志。
预热返回 `accepted=true` 表示已入队，不能作为模型加载完成的证明。Genie 模型准备必须通过
`sakura.host.diagnostics` 发出 `tts.conversion.*` 事件：`checking`、`reused`、`cache_hit`、`started`、
`running`、`finished`、`failed` 和 `cancelled`。`started` 仅在转换进程启动后发出，`finished` 仅在缓存提交
成功后发出；运行期间每 5 秒报告一次耗时，不猜测进度百分比。所有事件写入 `data/logs/sakura-runtime.log`。
软件日志和 TTS 日志视图显示实际转换的开始、运行状态和终态，以中文说明当前阶段，失败保留原因码；普通
检查、缓存命中和模型复用事件仅写文件，避免每次合成都重复刷屏。同名事件若为警告或错误，仍须显示。

转换器以无缓冲模式运行，完整 stdout/stderr 实时写入 Provider 私有日志 `logs/genie-converter.log`。
该文件保留最近一次实际转换的输出，开始下一次转换时覆盖；缓存命中不覆盖。成功、失败或取消后均保留原始
日志，不依赖临时模型目录。统一 Runtime 日志只记录受控阶段、耗时及错误码，不转发原始转换器输出。

Custom Endpoint 仍必须显式提供 `remoteCharacterName`，不得把本地角色名或资源路径猜作远端映射。

每个启用的 Genie/GPT-SoVITS Provider 都必须注册一个 `surface=about` 的 bundle resource，不受当前角色所选
Provider 影响。Custom Endpoint 报告 `not_required`；无兼容包报告 `unsupported`；Genie 使用固定包，
GPT-SoVITS 按平台/GPU 规则只投影一个推荐包。下载线程、取消、续传、校验和原子安装由 Provider 插件实例
持有；成功后更新自身 `workDir/pythonPath/ttsConfigPath` 并原位重配置。Voice 页面和插件详情不得重复显示
bundle 下载入口。

Windows Managed GPT-SoVITS 必须用整合包自己的 Python/PyTorch 实测 CUDA、显存和 FP16，再在 staging 安装
目录或既有运行目录原子生成 Sakura 专用推理 YAML；启动 `api_v2.py` 必须显式传入该配置。空
`ttsConfigPath` 的既有 Managed 安装在下次冷启动自动生成；用户指定的其他 YAML 不得改写。专用配置已选择
CUDA 后若设备不可用，Provider 必须以 `TTS_ACCELERATOR_UNAVAILABLE` 明确失败并走既有字幕降级，不得静默
退回 CPU。macOS 继续使用安装器生成的已验证配置。

Python Provider startup/process cleanup/settings/synthesis/recording 与 Rust playback 的真实终态都写入统一
`data/logs/sakura-runtime.log`。日志只允许稳定标识、Provider、端口、状态/阶段、进度、字节数、HTTP 状态、
耗时和重试次数；不得记录文本、凭据、音频路径或完整 Provider 响应。Rust 必须先在音频回调源头落日志，Core
observe 或插件发布失败不得吞掉播放证据。托管 GPT-SoVITS 的后台预热、首次合成补启动和故障重启必须通过
受控 Host diagnostics bridge 依次报告服务启动、进程已启动并等待、服务就绪、角色权重加载和角色权重就绪；
HTTP 轮询不得逐次写 info。完成和失败携带 `elapsed_ms`，失败至少保留
`provider/reason_code/stage/error_type`，权重失败区分 GPT 与 SoVITS 阶段。自定义外部端点不发布托管启动事件。
Hub 必须保留 Provider
返回的有界稳定错误码，Core 对外仍可投影统一失败，但统一日志必须在 `provider_error_code` 中保留原始稳定码。

自动验证覆盖 Python/Rust/WebView 纵向链；Windows WASAPI、macOS CoreAudio 和 Linux PipeWire/Pulse/ALSA
真实默认设备及旧 GPT-SoVITS 清理是项目负责人验收前的硬门。
