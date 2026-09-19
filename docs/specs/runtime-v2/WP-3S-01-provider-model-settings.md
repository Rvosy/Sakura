---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-19
---

# 模型服务、连接与模型选择

## 所有权

默认模型来源为远端 API 插件 `sakura.model.openai_compatible`。本阶段不引入本地模型 runner、权重管理或离线发行组合。

- 模型插件拥有连接地址、凭据、请求超时、模型目录和模型能力，执行发现、连接测试及推理请求。
- Assistant 拥有对话上下文上限、`temperature`、`top_p`、`max_tokens`，通过自己的 Settings Contribution 展示在“模型”页。
- Core 只保存对话和视觉对话的模型引用，聚合插件贡献的目录与用途；不读取模型凭据、不构造协议客户端，也不执行模型网络探测。
- Mem0 等消费者保存自己的用途选择，经 SDK `ModelClient` 调用同一模型服务；不得获取其他插件的凭据。

## 引用与目录

模型引用固定为：

```json
{"serviceKey":"sakura.model.openai_compatible","profileId":"connection-id","modelId":"model-id"}
```

三个字段必须同时非空或同时为空。模型 ID 相同但服务或连接不同，是不同的选择。空的可选用途继承当前对话模型；视觉对话也按此处理。

模型提供方通过 `sakura.host.model_slots.register_provider()` 注册公开目录回调；目录按服务、连接、模型组织。
`catalog()` 只读取已生效的本地目录，不发网络请求。`describe()` 返回 `contextWindowTokens`、`contextWindowSource`、
`inputModalities` 和 `supportsTools`。未知能力保持 `null`，不能根据模型名称猜测；未配置上下文窗口时使用 32768 Token 回退值。
模型可显式配置 4096–2000000 Token 的窗口。服务不可用或模型已删除时，保留原引用并显示 `MODEL_REFERENCE_UNAVAILABLE`。

Core 的 `settings.provider_model.get` 返回 schema 2，包含嵌套 `providers`、带 owner 的 `model_slots` 和 `setup_complete`。
`settings.provider_model.save` 只接受 `draft.model_slots`，键为 `core:chat`、`core:vision_chat` 或插件槽位 identity。
Core 引用写入 `config/model_slots.json`；插件选择交给对应插件保存。部分保存失败须返回已保存用途、失败用途和 `save_state=partial`，
不得把已完成的写入报告为全部失败。Rust 和 WebView 消费公开引用并校验 generation，不能通过 DTO 带回密钥。
交接尚未完成时允许读取空目录，但保存返回 `MODEL_CONFIGURATION_NOT_READY`，不能用空选择覆盖待交接的旧配置。

## 设置与生效时机

模型插件通过公开页面放置机制，在“模型服务”页贡献宿主 `connection-editor`，保留原连接列表与详情、名称、API 地址、API Key、
模型标签、手动添加、模型发现勾选弹窗和连接测试。“模型”页保留用途下拉与继承，Assistant 的上下文上限和生成参数、
提供方的请求超时组成原“高级参数”折叠区。主动屏幕感知插件通过相同接口贡献“交互”页区块，不改变调度策略。

连接、模型目录和参数统一持有窗口草稿，底部“应用”“保存并关闭”才提交。先提交连接，再提交引用该连接的模型用途；
刷新目录时保留尚未提交的用途选择。保存逐项确认，部分成功或应用失败时报告实际结果并保留未完成修改。
界面读取只返回 `configured` 和空的密钥输入值。凭据动作仅支持 `keep`、`replace`、`clear`；`replace` 必须提供新值，其他动作不得夹带新值。
修改目录保留已存在模型的元数据；移动页面不改变配置归属。全局超时只有显式编辑时才更新已有连接，新连接继承该值。

提供方验证整份连接草稿后一次写入，返回 `restart_required`，由现有 Core 保存链重载插件。插件持久化不直接改变当前实例的有效配置。
保存前检查服务是否仍有请求；忙碌时返回 `MODEL_BUSY`。Core 从确认聊天空闲到完成重载期间暂停接收新对话，跨插件 RPC 时不持有聊天锁。
后台消费者或探测可能在检查后启动；重载会取消或终止这些旧实例任务，不能把任务重放到新实例。记忆整理保留已完成页及未处理游标。
应用成功后旧 Assistant 模型绑定失效；Host 在本次应用操作内重新准备并发布会话，旧会话不能悄悄采用新连接。

探测读取当前草稿中的地址、凭据和超时，不自动保存。发现结果经用户勾选进入草稿，取消弹窗不修改模型目录。
请求 ID、当前实例和窗口生命周期隔离过期结果；关闭窗口、修改探测连接或撤下组件时取消仍在执行的任务。

连接测试和模型发现由提供方的 `begin_probe/poll/result/cancel/release` 操作完成；Settings Action 返回后，后台任务更新状态。
测试连接必须调用用户指定模型并取得有效消息，不能以目录可达代替模型调用。取消和插件退出须结束请求并释放其资源；失败只展示安全错误码，
不回显密钥或未经清洗的供应商响应。Core 不保留专用 `list_models/test_connection/cancel` 设置命令。

Assistant 生成参数保存沿普通 Settings 保存链重新加载 Assistant。温度支持 0–2，Top P 支持 0–1，最大输出 Token 为正整数；
缺省温度恢复为 0.8，缺省 Top P 和最大输出 Token 不发送。合法的数值 0 不得丢失。

## 旧配置交接

插件启动前，执行一次旧 `config/api.yaml` 的交接：

- `api_profiles` 转到模型插件私有 `config.json`，请求超时转为连接字段；槽位上下文窗口转为对应模型元数据。
- `llm` 中的生成参数与对话槽位上下文上限转到默认 Assistant 私有配置。
- 对话和视觉槽位转为三字段引用，最后原子写入 `config/model_slots.json` 作为交接完成标记。

既有目标字段优先，进程中断后可以继续交接，不覆盖期间用户已保存的插件配置。旧 YAML 保持原内容；交接完成后旧文件的后续修改不再改变运行配置。较早插件版本已把对话上限放在模型元数据中时，只在 Assistant 缺少该字段时接收当前对话模型的旧值；显式清空的值不会再次回填。
旧版本导入在隔离 payload 中执行相同交接，生成文件随原有导入事务提交。Core 就绪读取器只读取系统版本与角色选择；
模型缺失由 Assistant 的准备阶段报告，不应使角色、插件管理或本地 Memory 管理不可用。
旧 API 或新引用文件损坏时，插件管理仍启动，模型页显示错误并保留编辑入口。用户明确选择有效对话模型后，
保存可重建引用文件；该操作不修改旧 `api.yaml`，也不自动用空配置覆盖损坏数据。

## 网络与验证

OpenAI 兼容协议、Google 官方 URL 规范化、TLS、代理和错误清洗由模型提供方拥有。SDK 不自动重试网络失败；
HTTP 错误只提取公开错误字段，在 JSON 解码后、诊断截断前清除实际使用的密钥，避免转义后的凭据进入远程异常栈。
消费者明确发起的新操作与供应商参数兼容调整须可区分。任务绑定提供方实例，取消、结果读取和释放不得重新绑定到新实例。

回归证据覆盖旧配置交接与中断续接、密钥三态、保存与应用隔离、模型元数据、失效引用、插件部分保存、真实 Core 设置往返，
以及 Assistant 和 Mem0 共用 Model Service。测试使用隔离数据；未执行的真实窗口体验或平台条件须明确说明。

## 历史验收记录（2026-07-29）

> 以下记录当时的实现、测试与任务安排，不作为当前流程要求。

生产实现和本地自动门已完成，工作包总表已进入 `stabilizing`。2026-07-30 验收回归修复后的本地证据为
Python unit 1182 passed/6 skipped、canonical frontend 99 passed、locked Rust 210 passed/23 ignored、Smoke Harness
2/2 cases（25 tests）和 Runtime v2 Shell Harness 7/7 cases（166 tests）；locked check、Rust fmt 与 diff
完整性检查通过。该段保留的是 ADR-0032 之前的历史验收事实；当时自动门还覆盖 Qt service -> v2 -> Qt
service 回读、unknown/non-target/secret 保持、稳定超时与保存错误码、重复保存串行化、探测关窗取消、真实
Core get/save 往返和 restart 后新 generation 重新绑定。当前规范改由同 generation 热应用测试取代该重启路径。

验收阻断回归“保存并应用后角色变成粉色默认背景，重启后窗口不可见”的根因是：新增但未完成模型列表的
非当前 Provider 被 Core 配置读取器错误提升为全局 `PROVIDER_SETUP_REQUIRED`，导致已经有效的聊天槽和
`current_character_id` 一起失去发布。读取器现允许未被槽引用的 Provider 保持空模型草稿，只以实际聊天槽
能否解析为启动条件；跨层回归固定了“有效聊天槽 + 未完成 Provider + N.A.V.I.”保存后仍为 `READY`。
同时设置端在新 Core generation 发布后通知主桌宠重新装载角色资源，冷启动无法取得角色 DTO 时也不再等待
无效占位图片才 reveal，确保错误态仍可见、可恢复。真实 `data/**` 只做了脱敏只读诊断，未被测试或修复改写。
首次修复复验仍会丢失的第二层原因是主桌宠 WebView reload 后前端布局 revision 从 1 重新计数，而原生
`WindowGeometrySession` 仍保留旧 revision，导致重载首个布局被当作 stale、页面停在隐藏的 loading 状态。
前端现先读取原生已应用 revision，再从下一值继续；真实 Windows 窗口已执行整页 reload，重载前后均确认
`N.A.V.I.` 名称、主题和立绘可见。

项目负责人于 2026-07-31 在当前开发会话中明确声明 WP-3S-01 已亲自验收通过，并授权开始后续 Harness
改造。Work Package 总表据此登记 accepted；本规范不补写负责人未提供的设备组合、CI run ID 或候选
SHA 细节。当时将 WP-H-01 排在 WP-3-04 之前；这项历史安排已不约束当前开发。
