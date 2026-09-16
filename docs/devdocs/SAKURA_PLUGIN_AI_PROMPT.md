---
kind: devdoc
status: current
audience: plugin-author
source_of_truth: ../specs/runtime-v2/sakura-plugin-runtime-v4.md
updated: 2026-09-06
---

# 用 AI 快速开发 Sakura 插件

这份文档专门提供给 AI 使用的插件开发提示词，帮助开发者通过 AI 快速制作或适配 Sakura Plugin API v4
插件。它约定开发步骤和交付要求，具体接口以[开发指南](SAKURA_PLUGIN_SDK.md)和
[Runtime v4 规范](../specs/runtime-v2/sakura-plugin-runtime-v4.md)为准。

复制下方整段提示词，填入需求后交给能读写代码的 AI。让 AI 在 Sakura 仓库中工作时，它可以直接读取引用
文件；如果只有独立插件目录，请同时提供开发指南和规范，并说明目标 Sakura 版本。需要日志能力时，目标
宿主必须提供 `sakura.host.logging`。不要只发这一份提示词，再让 AI 凭印象猜接口。

## 可复制提示词

```text
请为 Sakura 实现一个可安装、可验证的 Plugin API v4 插件，或按我的要求适配现有插件。
请直接完成调查、代码、README 和必要验证，不停在方案或伪代码；保留工作区里已有的无关修改。

我的需求：
- 模式：新建插件 / 适配现有插件
- 插件名称、稳定 ID、作者：<填写；未指定时提出合理命名>
- 目标 Sakura 版本或源码分支：<填写>
- 插件输出目录或现有插件路径：<填写>
- 主要功能和触发方式：<用户如何使用，期望得到什么结果>
- 设置项：<字段、默认值、必填条件；未指定时给出必要的最小配置>
- 外部依赖和资源：<API、本地程序、模型、Python 库；没有则写“无”>
- 数据与副作用：<保存什么、保存多久、是否联网、写文件或启动进程>
- 分类和图标偏好：<可选，由你从宿主支持的值中选择>
- 验收场景：<至少一个成功场景和一个实际可能发生的失败场景>

一、先核实接口与需求

1. 阅读工作区适用的 AGENTS.md，以及以下当前文档：
   docs/devdocs/SAKURA_PLUGIN_SDK.md
   docs/specs/runtime-v2/sakura-plugin-runtime-v4.md
   需要类型提示时查看 app/plugin_sdk/sakura_plugin_api.py。
   不把 docs/archive/ 中的旧 API 或前端原型当作当前合同。
2. 按功能选择最接近的现有插件阅读：
   plugins/optional/playwright_browser/plugin.py：工具、Artifact、普通设置和日志。
   plugins/builtin/sakura_mobile/plugin.py：后台服务、事件、配置热应用和清理。
   plugins/builtin/sakura_mem0/plugin.py：记忆、Context、Collection、模型槽位。
   plugins/builtin/sakura_gpt_sovits/plugin.py：语音 Service、资源下载和页面放置。
   它们是参考实现，不是可从另一插件目录导入的公共库。
3. 先确认哪些是 Host Service、哪些由其他插件提供、哪些是本插件业务。缺失信息只有在会实质改变
   功能、数据处理或外部操作范围时才询问；其余采用简单合理的默认值，并在交付中说明。
   若拿不到当前 SDK 或规范，说明缺少什么，不虚构接口，也不宣称插件已经兼容。

二、插件包与运行边界

1. 包根目录放 plugin.yaml 和入口代码；按需要附 config.json、依赖声明、README 和测试。
   Manifest 使用 api: 4、稳定 id、entry: Python模块:类名，第三方插件默认 enabled: false。
   使用 name/author/description/version，避免旧 plugin_id、api_version、optional 字段。
2. 在 requires 中声明固定 Host 和插件 Service 依赖；provides 与 setup 中 context.provide() 完全一致。
   只提供 Contribution 的插件可以 provides: []，不必为了形式创建空 Service。
   exports 是唯一导出方法表；不要把插件 ID、priority 或展示分类当成 Service 选择机制。
3. 每个插件使用自己的 Python dependency root。需要第三方库时提供合适的 requirements.lock、
   requirements.txt 或 pyproject.toml，按目标平台核实依赖；不要修改 Sakura 主 Runtime。
   不导入 app.*、Core 私有模块或其他插件源码。不要在 setup 或普通启动时下载或安装依赖。
4. 普通 Service 参数和结果使用有界 JSON；跨进程不传 Python 对象、callable、异常、文件句柄或裸路径。
   大文件交给 sakura.host.artifacts。Host Contribution 的回调通过公开 register() 登记，
   不调用私有 callback/transport 方法。
5. 默认配置放 config.json，用户配置经 context.config 保存；私有数据经 context.data_path() 保存。
   不向插件安装目录写用户状态，不从数据路径反推宿主根目录。
6. 线程、子进程、连接和临时资源登记 context.effect()，清理幂等且有界，等待生产者停止后再释放资源。
   不依赖独立 shutdown() 入口。需要进程树清理时使用公开 sakura_process 工具。
   不在 reload、停用或 Core generation 变化后复用旧 ServiceProxy、callback 或 artifact。

三、设置、分类、图标和资源

1. 普通设置使用 sakura.host.settings.register(descriptor, load=..., save=..., actions=...)。
   Sakura 负责渲染独立“插件设置”窗口，不编写插件 HTML、JS、CSS 或另一套保存接口。
   保持 sectionId、字段 key、actionId 稳定；适配已有插件时保留存量配置键和数据。
2. 选择支持的字段类型：string/password/boolean/integer/number/select/readonly/status/resource。
   给出合法默认值、必要范围和简短说明；较少使用的参数放 placement: advanced。
   enabledWhen 使用同区块字段及字符串 equals，不用它替代后端业务校验。
   status/resource 是只读投影；Action 必须有同名回调，当前 danger 只支持 false。
3. load 快速读取配置与状态，不产生业务副作用。save 校验并调用 context.config.update/replace，
   如实返回 applied、restart_required 或 error，可用 {applicationState: ...} 包装。
   只有已经热应用才返回 applied；没有 on_change 时默认需要重启插件。
4. “完成”只保留草稿，设置页底栏“应用”或“保存并关闭”才提交普通配置。
   “取消”、关闭和 Esc 恢复本插件打开时的可编辑字段，不能撤销已执行的 Action、CRUD 或下载。
   Action 使用传入的表单草稿；返回 values 只更新投影。确需立即保存时显式更新配置，并在文案说明。
5. 默认不注册 surface，普通设置自动进入插件窗口。确实需要宿主页面时，先注册区块，再使用
   sakura.host.settings.surface-v0：voice 复用语音页控件，memory 用于记忆内容管理。
   plugin 是普通插件窗口；about 是历史兼容的只读资源区块，新资源不必使用它。
   不自创 surface 名称，也不承诺任意新页面、窗口或输入栏扩展。
6. Manifest 的 presentation 声明 kind/category/icon：
   kind: extension、provider 或 infrastructure。
   category: model、voice、memory、tools、connectivity 或 other。
   icon: 从 desktop/frontend/core/icons.js 的 iconNames 选择，例如 brain、smartphone、
   audio-lines、wrench；名称匹配 [a-z][a-z0-9-]{0,63}，不含 .svg 后缀。
   这些字段只影响展示。图标不能是 URL、路径、SVG 或自带图片；未收录名称会回退默认图标。
7. 有下载需求时，用 resource 字段声明 actionIds，值包含 applicability、subtitle、ready、taskState、
   message、detail、progress、availableActionIds。可用动作必须属于声明集合并有回调。
   Action 只启动有界后台任务并及时返回，提供真实进度、取消和明确失败；状态读取不偷偷联网。
   “关于 → 组件”自动聚合已启用插件的 resource，只读并跳回插件设置，不需要重复注册下载界面。
8. 需要数据列表时用 settings.collection-v0，提供有界分页和必要 CRUD，删除配置确认文案。
   需要模型选择时用 model_slots，不复制宿主凭据管理；不要把 resolve 返回的凭据暴露给界面或日志。

四、主动接入日志

1. 默认给插件实现宿主日志，在 requires 中声明 sakura.host.logging，并取得：
   logger = context.get("sakura.host.logging")
   logger.info("插件已初始化")
   logger.error("操作失败", fields={"operation": "refresh", "reason_code": "REFRESH_FAILED",
                                  "error_type": type(error).__name__})
   上述 error 来自实际捕获的异常；不要照抄成未定义变量。
2. 记录初始化结果、实际配置变化、业务失败和清理异常。info 记录有用状态变化，warning 记录可恢复
   问题，error 记录失败；轮询、进度和缓存检查使用 debug 或不记录。同一终态避免重复输出。
3. 只使用 debug/info/warning/error(message, *, fields=None)，不套用标准 logger 的格式化参数、
   exc_info 或 exception()。返回值只是本地队列是否接收，不代表落盘，不能据此改变业务结果。
4. 日志写自编短消息和稳定码、异常类型、耗时、计数。禁止 str(error)、原始异常对象、完整堆栈、
   配置、凭据、环境变量、请求头、聊天正文、Prompt、工具参数和模型输出。
   字段保持精简有界，不依赖宿主清洗替你判断私密内容。
5. 不自建统一日志 writer 或轮转器，也不假设 print、标准 logging、stderr 或外部程序输出会被接管。
   插件主动记录进入 data/logs/sakura-plugins.log，宿主加载诊断在 sakura-runtime.log。
   README 告诉用户可在运行日志窗口“插件”页筛选，并写明新日志 Service 的宿主兼容要求。

五、实现、验证与交付

1. 完成最小可用功能，不为尚无需求的能力建立框架。不要夹带 Sakura 宿主功能改写；现有扩展点无法
   满足需求时，明确缺口及可实现范围，不调用私有接口冒充支持。
2. 针对用户可观察行为做必要验证：成功与失败、配置保存与热应用、资源或后台任务清理、日志隐私。
   适配设置时验证“编辑 → 取消”“编辑 → 完成 → 应用”，有资源任务时验证刷新保留草稿及取消。
   回归测试应能发现实际缺陷，不只断言源码字符串、文件存在、CSS 数值或完整文案。
3. 在仓库里使用当前平台 bundled Runtime：Windows 为 runtime\python.exe，macOS/Linux 为
   runtime/bin/python。先用 -m harness list 查看入口，按改动风险选择相关测试或插件 journey。
   只改文档时运行 -m harness run docs 即可。没运行的窗口、平台或设备验证必须如实列出。
4. README 写清安装 ZIP/文件夹、启用、插件设置与底栏应用、依赖和资源下载、日志查看、已知限制。
   修改源码或依赖后说明需要重新安装；新 config.json 默认值不会覆盖用户已保存的同名配置。
5. 最后交付可用插件目录、改动摘要、运行过的检查及结果、剩余限制。若为旧插件适配，另列需要用户
   更新的宿主能力或配置步骤。不要虚构测试通过，不擅自提交、发布或覆盖真实用户数据。
```

## 如何填写需求

例如：“新建本地笔记插件。用户通过聊天工具新增和检索笔记，在插件设置中分页管理；按当前角色分别保存，
不联网。设置只有每次检索条数，默认 5，范围 1–20。图标用 `sticky-note`。验证不同角色的数据不会混在一起，
删除前会确认，日志不出现笔记正文。”

适配已有插件时，把插件路径、当前配置示例和已有用户数据的保留要求一起提供，要求 AI 按开发指南的
[适配清单](SAKURA_PLUGIN_SDK.md#插件管理与日志适配清单)检查。不要在示例里放真实 API key、个人笔记或对话。
