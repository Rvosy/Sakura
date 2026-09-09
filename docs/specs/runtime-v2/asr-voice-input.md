---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-09
---

# ASR Hub 与点击式语音输入

## 目标与实现状态

本文定义点击式语音输入。实现由正式输入栏、Rust/Tauri 麦克风采集、Core 输入协调与音频授权、独立 ASR Hub
和 SenseVoice Provider 组成，沿用 Plugin Runtime v4 的进程与依赖隔离。模型需要用户显式安装；关闭 ASR
插件不影响文字输入。验证范围与实际设备限制见本文末尾。

用户点击发送按钮左侧的麦克风开始录音，再次点击结束录音并识别。完整识别结果写入输入栏，用户可以修改后自行
发送。语音输入只生成聊天草稿，不直接创建消息、触发 Assistant 或绕过现有发送入口。

第一版包含可替换的 ASR Hub 插件、官方 SenseVoice 引擎插件、宿主录音和实时音量波形。暂不包含常驻监听、
唤醒词、自动发送、边说边出字、说话人分离、情绪驱动桌宠或多引擎并行识别。

## 职责与不变量

```text
输入栏麦克风按钮
    → Rust/Tauri 采集 → Host 管理的临时音频
    → sakura.asr Hub → 选中的 ASR Provider
    → 完整文本 → 当前输入栏草稿 → 用户发送 → 现有聊天与 TTS
```

| 所有者 | 职责 |
|---|---|
| Rust/Tauri | 系统麦克风、设备权限、采样、录音时长、音量摘要和设备释放 |
| Core 语音输入消费者与 Host Service | 协调录音与识别、签发音频资源、绑定输入上下文、处理取消与过期结果 |
| `sakura.asr` Hub 插件 | Provider 登记与展示、全局引擎选择、任务路由、统一状态和取消 |
| ASR Provider 插件 | 模型和依赖、预热、音频预处理、VAD、识别及引擎自己的设置 |
| 输入栏 | 状态呈现、录音操作、草稿保护和识别结果插入 |

Core 与 UI 只消费稳定 ASR 能力，不读取 SenseVoice、Qwen 或其他模型的私有配置。通用
`PluginRuntimeManager` 不新增 ASR 分支。麦克风由用户操作驱动，ASR Provider 不能因启用或预热自行开始收音。
Host 音频输入能力由 `sakura.host.audio_input` 提供；UI 通过类型化 Tauri 命令操作，不接触录音路径或 PCM。

ASR Hub 和各 Provider 均遵循 API v4、独立插件进程和独立 dependency root。Hub 可以关闭或替换；官方引擎
使用与第三方相同的登记、调用和资源访问接口。

## 点击式录音与输入栏

ASR Hub 提供活动的 `sakura.asr` 服务时，麦克风紧邻发送按钮左侧。关闭 Hub 后隐藏麦克风并恢复原有输入栏
布局，不保留空列；该显隐与 TTS Hub 无关。已启用 Hub 但模型未就绪时保留入口，点击后明确说明原因。
点击开始、再次点击结束，不要求持续按住，也不叠加长按语义。

| 状态 | 显示与操作 | 退出条件 |
|---|---|---|
| 可输入 | 原有输入框、麦克风、发送按钮 | 点击麦克风后检查引擎与设备 |
| 正在准备 | 整条输入栏中央显示「正在准备」，右侧加载状态，左侧可取消；禁止发送 | 引擎可用且麦克风成功打开后才进入录音 |
| 正在录音 | 左侧取消，中间真实音量波形，原麦克风位置变为停止按钮；发送禁用 | 点击停止后识别，取消则丢弃本次录音 |
| 正在识别 | 整条输入栏中央显示「正在识别」，右侧加载状态，左侧可取消；禁止发送 | 成功回填，失败提示，或用户取消 |

录音布局参考用户提供的横向波形条：安静时是短线或圆点，有声音时出现上下对称的圆角竖条。正常输入框在录音
时临时由该区域替代，已有草稿保存在原会话中。准备、录音和识别期间暂时隐藏草稿与附件，输入栏保持单行高度；
完成或退出后恢复原内容、编辑与焦点。准备和识别不在输入栏外显示独立提示框。
发送箭头在录音和识别期间保持禁用，停止按钮只表示“结束录音并识别”。

保留现有立绘加输入栏形态，沿用当前角色、用户主题、字体、布局和毛玻璃效果。未跟踪原型里的 N.A.V.I.、
粉色和固定尺寸只是配置快照，状态预览面板不进入产品。复用最左侧的功能扩展按钮元素与点击区域：「＋」在
160 毫秒内旋转 225° 作为「×」取消，结束后转回并恢复扩展操作。停止按钮用浅灰底，小方块跟随当前主题色。

产品行为：

- X 和 Esc 均取消当前准备、录音或识别，恢复原草稿。取消后再开始是新的录音会话。
- 在开始录音时保存草稿版本、光标位置与选择范围。草稿未变化时在保存的光标位置插入完整文本；有选择范围时
  插入到范围末尾，不自动删除已选文字。已有附件保留。结果不逐段填入，回填至多一次。
- 若同一会话的草稿被其他入口修改，不用旧快照覆盖新内容；将结果追加到最新草稿末尾，并保留全部新内容。
  自动插入的分隔空白只用于避免两段文字直接粘连。
- 切换会话或角色、关闭或隐藏输入窗口、重建 Core generation 时取消任务并失效回填资格。其他窗口短暂获得
  焦点不等于关闭录音；系统权限弹窗不能导致录音启动后立刻被错误取消。
- 开始采集前停止当前 TTS 播放；采集期间新到的 TTS 不开始播放，结束后不补播这期间跳过的声音。聊天文本正常
  更新。这只是避免应用自身播放污染录音，不承诺消除扬声器回声、其他应用声音或环境噪声。
- 输入设备可选择“系统默认”或具体麦克风。选择系统默认时每次开始录音重新读取默认设备；具体设备按宿主
  设备标识匹配，不存在时明确报错，不切回默认设备。活动录音不自动切设备。设备断开、系统休眠或
  权限失败时终止本次录音并提示，恢复草稿，不自动识别不完整录音或重新收音。
- 第一版录音上限为 60 秒，达到上限后结束录音并识别，仍不发送。界面不显示录音时间或上限；权限等待不计入时长。
- Hub 未活动时隐藏麦克风；未选引擎或引擎缺少资源时，保留的入口显示原因与配置入口，不先录音再让用户补装模型。

## 输入状态过渡

麦克风按钮使用同一 inline SVG，在麦克风、准备加载环、录音停止方块和识别加载环之间连续形变。
只有成功回填当前录音的文字后才显示短暂完成勾，再恢复麦克风；取消、失败及过期结果不显示成功反馈。
新录音可立即打断完成动画。草稿、状态文字和真实波形同步淡入淡出；隐藏的草稿不可编辑或获得焦点，
结束后恢复原内容和选择位置。窗口高度沿用现有原生/WebView 几何事务，不由图标或波形驱动。

错误提示保留“打开设置”入口，指引用户检查插件设置。点击“重试”可重新开始一轮语音输入；不会自动重录或自动发送。
左侧加号继续使用原版 225° / 160ms 旋转，在工具入口与取消语音之间切换。

## 实时音量波形

参考图展示的是随时间变化的音量包络，不需要声纹身份识别、频谱分析或额外神经网络。

Rust 从正在采集的 PCM 计算短时间窗的 RMS 或峰值，经归一化和平滑生成音量摘要。建议约每秒 20 次推送
`recordingId + sequence + level`，WebView 使用固定容量的历史队列和 Canvas 或 SVG 绘制。新竖条从右侧进入，
历史向左移动；没有历史的位置保留低幅圆点。条数随可用宽度调整，不固定依赖参考图片的像素尺寸。

波形通道只传音量摘要，不传原始 PCM，不经 ASR Hub 或模型推理。采集线程不等待 UI；UI 来不及消费时可以合并
或丢弃旧音量帧，队列不随录音时长增长。会话 ID 和顺序号用于忽略已取消会话和乱序摘要。

音量效果遵循以下边界：

- 安静时保留低幅基线，随真实音量变化，不用随机动画假装收到声音。
- 只有成功打开麦克风后才显示录音波形；停止采集后冻结或移除波形，识别等待使用独立状态。
- 动画不能改变按钮位置或让输入窗口持续重新计算大小；沿用现有圆角、透明背景、深浅主题与 DPI 适配。
- 按钮提供可访问名称与键盘焦点，录音状态提供可访问文字，不能只靠颜色或动画表达。
- 系统减少动态效果设置不改变图标形变、加载旋转、文字过渡或波形刷新节奏。若波形绘制出现平台问题，可降级为状态文字与简单音量指示，
  保留真实录音和停止操作，不以波形效果阻塞基本语音输入。

约 20 Hz、条宽和视觉平滑仅为实现起点，不作为固定 CSS 数值或性能验收阈值。实际流畅度需在真实窗口验证。
绘制以平滑、合并音量摘要和放慢历史移动取得原型的缓慢节奏，采集速率不受动画速度影响，不能使用随机或
预录的模拟包络替代真实音量。

## Hub 与第三方 Provider 契约

### 登记与选择

官方 Hub 包建议为 `sakura_asr_hub`，插件 ID 与公共 Service 为 `sakura.asr`。默认引擎包建议为
`sakura_asr_sensevoice`，插件 ID 为 `sakura.asr.sensevoice`，提供 `sakura.asr.provider.sensevoice`。
普通 Provider 声明硬依赖 `sakura.asr`，Hub 不硬依赖某一个 Provider，避免循环依赖。

多个 Provider 可以同时启用，分别提供独立 Service 并登记普通 JSON descriptor，例如：

```json
{
  "providerId": "sakura.asr.sensevoice",
  "serviceKey": "sakura.asr.provider.sensevoice",
  "label": "SenseVoice",
  "processingLocation": "local"
}
```

`providerId` 对应贡献插件身份；登记时核对 `serviceKey` 的活动所有者，不能冒用其他插件。相同所有者重复登记
相同 descriptor 幂等，不覆盖其他所有者。正常停用时注销；进程退出使代理和该次绑定失效。`processingLocation`
为 `local` 或 `remote`，只供用户理解当前配置的数据去向，不构成安全沙箱或代码可信证明；可切换本地/远端的
Provider 在用户保存配置后更新该信息。
登记与注销还核对 Core 提供的 `context.caller_id`，防止其他插件用完整但不属于自己的 ID 和 Service key
修改展示信息或注销登记。

Hub 只管理应用级 `selectedProviderId`。识别语言及其可选项由各 Provider 通过既有设置贡献管理，互不影响。
主设置的“语音”页不展示语音输入。Hub 的独立插件设置只选择引擎；识别语言、模型资源和私有参数只在
对应 Provider 的独立插件设置中展示。输入引擎列表及 Hub 插件身份来自运行时，不在前端固定插件 ID 或模型名单。
插件弹窗的“完成”保留草稿，“取消”还原本次编辑；外层“应用”保存更改。
Hub 不重复展示本地处理或模型安装的正常状态；选择远端引擎时保留录音去向提示。
引擎模型已安装但尚未加载是正常待用状态，不标记为“未就绪”；缺少模型、加载中和失败按实际状态标注。
SenseVoice 首次建立独立语言配置时，若它是旧配置选中的引擎，则继承 Hub 中受支持的语言；之后以自己的配置为准。

### 麦克风设置与语音输入测试

已登记 Provider 的独立插件设置提供麦克风选择与输入测试。Rust 枚举设备并提供设备 ID 和名称，
Core 在宿主系统配置的 `audio_input.device_id` 保存选择，空字符串表示系统默认；不写入角色、TTS 或模型私有配置。
设备列表只作枚举，不开始采集。选择暂不可用的设备仍保留配置，由用户重新选择。
设置保存只提交改动字段；单独修改麦克风不依赖 Hub 活动，也不覆盖 Hub 保存的引擎或插件的语言。

测试由用户点击开始，再次点击停止并识别，准备、录音和识别期间都可取消。测试使用当前插件和
麦克风草稿，识别语言等插件参数使用已应用的配置；临时麦克风选择不改变全局配置。沿用正式采集、Hub 路由、音频授权、TTS 暂停和清理链路，
结果仅展示在设置中，不进入聊天草稿、附件、消息或历史，也不触发 Assistant。

测试与聊天输入共享单任务限制。Rust 将任务绑定到发起窗口，音量和状态只发送到该窗口；其他窗口不能轮询、
停止或取消不属于自己的任务。关闭设置窗口取消其测试，迟到结果失效，并按读取租约释放临时录音。

全新配置可以选择随包官方默认引擎，之后保留用户选择，包括暂不可用的选择。引擎失效时明确报错，不按优先级
选赢家，不自动尝试其他本地引擎或云服务。用户明确选择远端引擎后，该引擎才按自身配置处理提交的录音。

每次语音输入绑定开始时的 Provider 身份和配置版本。识别中切换引擎或修改引擎配置只影响下一次输入；若修改
要求重启当前 Provider，则当前任务明确取消或失败，不能把旧音频悄悄交给新引擎。

### 服务方法与任务

Hub 接口：

```text
registerProvider(descriptor)
unregisterProvider(providerId, serviceKey)
listProviders()
status(providerId?)
configure(values)
warmup(providerId?)
begin(request) -> { state: "running", requestId, providerId } 或失败结果
poll(requestId)
cancel(requestId)
```

Provider 只需导出：

```text
status()
warmup()
begin(request) -> jobId
poll(jobId)
cancel(jobId)
```

`status` 是只读状态，不下载模型或探测所有远端服务。`warmup` 排队准备模型并快速返回准备状态，完成情况由
后续 `status` 读取；不能把模型加载时间变成一次长时间阻塞的 Service RPC。只准备选中的引擎，不预热所有
已安装引擎。未就绪时处于准备状态，用户可取消进入录音；模型下载必须由显式安装或重试发起。

Hub 状态返回 `providerId`、`serviceKey`、`configVersion`、`state`、`available`、`language`；`language` 来自对应 Provider，未声明时为 `auto`。就绪为
`state: "ready", available: true`。可选 `providerId` 用于继续准备已绑定的引擎。Provider 在私有配置
失效时更新 `configVersion`；全局切换选择不修改旧 Provider 的配置版本。

`begin` 快速接受任务，推理在插件内后台执行；`poll` 是短调用。沿用 v4 的有限 JSON、调用 deadline 和显式
失败，不跨边界传 Python 对象、音频数组、callback 或整段 base64 音频。
Core 的 `begin` 请求携带 `requestId`、`providerId`、`configVersion`、`audio` 和 `language`。

请求包含 Host 签发的音频 descriptor 和 `language`，默认 `auto`。公共输入为 16 kHz、单声道、PCM16 WAV，
设备可以按支持的原生格式采集，再在宿主统一转换。descriptor 包含不透明 `resourceId`、`mediaType`、
`byteLength`、`sampleRate`、`channels` 和 `durationMs`；Host 验证其真实性，插件不能仅信任调用方填写的字段。

Provider 的 `poll` 使用与现有 TTS 相同的终态命名：

```json
{"state": "running"}
{"state": "succeeded", "text": "今晚陪我打游戏吧", "language": "zh"}
{"state": "failed", "errorCode": "ASR_PROVIDER_UNAVAILABLE"}
{"state": "cancelled"}
```

Hub 为结果补充 `requestId` 和 `providerId`。识别语言未知时允许 `language: null`，指定语言不支持时明确
报错。成功文本不含模型控制标签；无有效人声或文字使用 `ASR_NO_SPEECH` 失败结果，不向草稿插入空白或提示语。
第一版结果不要求时间戳、情绪、热词或流式 partial 字段，不能要求所有第三方模型实现这些能力。

Hub 同时最多接收一个活动识别请求，重复提交返回 `ASR_BUSY`。任务绑定 Hub request、Provider job、插件
scope 和 Core generation，取消后的结果不再有草稿写入资格。已成功的任务不逆转 Provider 终态；如果用户在
回填前取消，输入消费者仍必须丢弃结果。取消、成功和迟到 poll 之间的竞争不能导致重复回填或提前删除文件。

Provider 终态保留到消费者读取；重复 poll 返回相同结果。未消费任务受容量限制，不无限积累；插件 scope 或
generation 关闭时统一回收。任务回收不依赖 UI 继续轮询，不增加自动重启、失败重放或后台健康调和。

## 输入音频资源与生命周期

现有 `PluginArtifactStore` 主要处理插件生成文件后交给 Core 的输出链路，不能直接假定它已支持 Host 录音向
其他插件授权读取。实现需要补齐受控输入资源链路，复用既有 generation、scope 和临时目录管理原则。

`AudioInputResources` 独立管理输入资源。Host Service 提供 `verifyProvider(providerId, serviceKey)`、
`authorize(descriptor, serviceKey)`、`acquire(resourceId)`、`release(leaseId)` 和 `revoke(resourceId)`。
登记核对声明 Service 的当前进程 scope；读取授权只接受已发布的活动 Service。`acquire` 向授权 Provider
返回路径与读取租约，`release` 结束租约。资源撤销后禁止新增读取，已有租约退出后才删除文件。

Core 的 `asr.input.prepare/poll/cancel` 绑定录音 ID、输入上下文、草稿版本及选区；`capture_target` 只向
Rust 分配私有文件路径，设备成功打开后 `capture_ready` 进入录音。停止写入后 `submit` 转交 Core，异常
或取消由 `capture_discarded` 结束生产者占用。Core 后台持续处理准备、识别与清理，不依赖 UI 继续轮询。
采集故障通过 `capture_discarded.errorCode` 提交稳定错误码，使尚未结束的任务进入可轮询的 `failed` 状态；
随后才发送前端故障事件。清理和迟到的取消不覆盖失败原因，已取消的任务也不会因迟到故障改为失败。
Rust 录音期间通过只读 `capture_status` 观察任务失效，该接口不消费转写结果。
成功结果通过 `poll` 至多交付一次，后续返回 `consumed`；前端仍核对本地任务和上下文，保留最新草稿与附件。
`asr.input.availability` 只查询 Hub 服务是否活动，不探测 Provider 或下载资源。设置窗口的 `prepare` 使用
`purpose: "test"`，允许临时 `providerId` 和 `inputDeviceId`；聊天使用 `purpose: "draft"`，不允许这些覆盖。
`capture_target` 向 Rust 返回本次绑定的 `inputDeviceId`，之后修改设置只影响下次录音。

- Host 拥有录音文件。完成写入和格式校验后才签发只读资源 descriptor；采集中资源不能提交识别。
- Hub 请求 Host 把当前录音的读取资格绑定到选中的 Provider Service 所对应的活动插件 scope。Host 核对当前
  录音、Hub 身份和目标 Service 所有权，只授权本次选定引擎。Provider 通过 Host 接口取得受控读取入口；如果
  底层必须提供本机路径，该路径只由 Host 返回给被授权的读取者，不进入 UI 或插件间 Service DTO。
- Hub 与 Provider 停用、录音取消或 generation 更换时撤销后续访问；旧 scope 的资源不能被重启后的插件复用。
  这些是协作协议边界，仍沿用 v4“可信本地插件不是恶意代码沙箱”的限制。
- 取消先使结果失效，再停止生产者/消费者。正在执行的 native 推理无法立即中断时，保留它仍在读取的文件，
  待退出读取后清理；不能仅因 UI 停止 poll 就删除。插件退出后 Host 负责收尾，正常退出沿用受控进程树回收。
- 输入音频只作临时资源，成功、失败和取消后释放，退出或 generation 收尾清理剩余资源；不自动加入 TTS
  recording、聊天附件、历史记录或遥测。日志不记录原始录音、完整转写或私有路径。

## 默认 SenseVoice 引擎

官方默认 Provider 使用 `sherpa-onnx + SenseVoiceSmall INT8`，优先 CPU 推理。Silero VAD 放在 Provider 内，
用于剔除无声录音、裁剪静音及把长录音分为有界片段；不决定 UI 何时停止或发送。分段结果按顺序汇总后只返回
一次完整文本，分段边界需要真实语音样本验证，避免丢字或重复。

语言范围为普通话、粤语、英语、日语、韩语和自动检测。模型控制标签在插件内清理。不能把非自回归、模拟流式
或官方速度对比直接当成 Sakura 的实时字幕能力和延迟保证。

插件随产品提供，权重通过显式安装下载并校验；离线发行形式可以预装匹配资源。插件自身设置展示资源是否齐全、
下载进度、取消、错误和手动重试。普通启动和预热不静默下载。资源按明确版本、必要文件和尺寸判断是否齐全，安装完成后原子发布，
保留原可用版本直到替换成功。预热由原生推理库验证模型可加载性，不另行扫描内容计算摘要。
旧 `complete.json` 的 SHA 字段直接忽略；现有版本目录保持可读，不因字段退役要求重装或下载。
分发需记录代码、权重和 ONNX 转换产物各自的来源与许可。

逐插件依赖隔离不解决基础 CPython ABI、平台 wheel、系统库或驱动兼容。默认引擎在目标 bundled Runtime 验证
导入、模型加载和短句识别；依赖失败归因于该插件，不安装到 Core，也不引入系统 Python 回退。

## 兼容性

现有纯文字输入、附件、发送快捷键和 TTS 选择保持原有行为；只有活动语音输入期间应用上述编辑和播放限制。
关闭 Hub 或默认引擎不会阻止文字聊天。ASR 选择属于应用级输入配置，不写入角色包，不迁移或复用角色 TTS
Provider 配置；第一次启用只补充缺省值，不覆盖既有用户设置。

不同 ASR Provider 使用不同 Service key，可以共存；两个启用插件同时提供 `sakura.asr` 时，仍按 Runtime v4
让冲突参与者明确失败。替换 Hub 必须继续提供本文公共契约，不引入通用 Runtime 的 Provider 注册表。

## 验证

### 运行日志

正常的 `asr.input.availability`、`asr.input.poll` 和 `asr.input.capture_status` 查询使用调试级日志，
默认不按轮询频率刷屏；请求失败或超时仍保留。Core 记录准备、识别和终态，Rust 记录麦克风采集阶段与设备故障，
同一录音使用 `recording_id` 关联，均显示在“软件”页。

ASR Hub 与 SenseVoice 通过宿主插件日志接口记录关键事件，在“插件”页按插件名称查看，并写入
`sakura-plugins.log`。安装、加载和识别失败保留错误码与阶段，正常取消不算错误。日志不包含转写文字、草稿、
录音内容或设备与模型的私有路径；轮询和音量帧不产生普通业务日志。
插件识别日志的 `request_id` 对应宿主的 `recording_id`，可据此关联同一次输入。

### 自动与设备验证

自动验证入口为 `journey-asr`，包含 Core/独立插件进程、资源生命周期、前端行为与宿主音频处理测试。
真实设备测试与模型样本测试需要显式执行，不因离线测试通过而视为完成。验收需要覆盖：

- 通过真实输入消费者和测试 Provider 验证两次点击、取消、至多一次回填、手动发送、原草稿/附件保留，以及
  同会话草稿变化、角色切换、窗口关闭和 generation 更换时的结果隔离。
- 用独立插件进程验证多个 Provider 登记与显式选择、资源授权、失效代理、Provider 崩溃、识别中切换设置和
  native 推理未结束时取消；证明不会转交给别的引擎，也不会让文件提前删除或永久泄漏。
- 在隔离临时根验证设备拒绝、断开、休眠、60 秒上限、纯静音和格式错误，以及资源下载失败不破坏已有模型。
- 默认引擎使用普通话、日语、中英混合、短停顿与低音量样本验证可用性。记录目标机器、冷启动、热识别耗时与
  峰值内存，不以未经实测的宣传速度作为承诺。
- 真实窗口验证波形随输入音量变化、安静基线、取消后停止、窄宽度、主题、DPI、键盘，以及系统减少动态效果设置下仍持续播放；同时验证
  录音期间 TTS 不播放。Windows/macOS/Linux 的设备权限与打包能力分别记录，不用单平台证据替代其他平台。

行为测试沿真实调用链选择主要证据层；不单独断言 CSS 数值、竖条数量或完整提示文案。普通测试不会下载
模型、打开麦克风或使用真实用户数据。原生推理验证见 `tests/unit/test_asr_sensevoice_inference.py` 中的
隔离资源环境变量说明；真实采集使用 Rust 的显式 ignored 设备测试。

2026-09-09 在 Windows bundled Python 3.12 与默认麦克风上已验证真实采集、RMS 摘要、WAV 转换和设备释放；
固定模型完成五语言、静音、低音量及中英短暂停顿样本识别，独立 Hub/Provider 进程与 Host 租约链完成真实
转写并清理音频。公共普通话短句重复构成的 28.080 秒连续样本与 29.280 秒短停顿样本均经两段真实识别，
六句文字无遗漏或重复；这不替代自然长句的质量评测。正式 HTML/CSS 的离屏视觉检查覆盖默认和深色主题、
宽窄输入栏。真实 Tauri 窗口完整操作、
设备拔出与休眠实测，以及 macOS/Linux 设备和签名包仍需分别验收。

同日新增麦克风选择与设置测试后，Windows 实测枚举到 4 个输入设备，设备 ID 在刷新后保持一致；指定设备完成
采集、WAV 清理与再次打开，不存在的设备明确失败。Core 独立进程测试覆盖临时 Provider/设备选择不改保存配置、
测试与聊天互斥、至多一次结果和取消后的租约清理；设置窗口完整识别操作尚未实测。

## 相关决策与资料

- [ADR-0045：ASR Hub、可替换识别引擎与宿主录音](../../adr/0045-asr-hub-and-host-audio-input.md)
- [Plugin Runtime v4](sakura-plugin-runtime-v4.md)
- [TTS、播放与音频设备](WP-4-05-tts-playback-audio-device-gate.md)
- [ADR-0037：可替换默认插件与隔离 Python 运行环境](../../adr/0037-replaceable-default-plugins-and-isolated-python-runtimes.md)
- [SenseVoice 官方说明](https://github.com/FunAudioLLM/SenseVoice)
- [sherpa-onnx SenseVoice 模型与用法](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)
