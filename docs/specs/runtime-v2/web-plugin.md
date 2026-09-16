---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-14
---

# 联网插件

## 能力边界

安装包提供“联网工具”插件 `sakura.web`（目录 `plugins/builtin/sakura_web`），新安装默认启用。插件通过 Plugin API v4
的 `sakura.host.tools` 提供搜索和网页读取，使用独立 Worker；HTTP 请求统一使用插件依赖 `httpx[socks]`。
不启动 MCP Server。通过 `sakura.host.settings` 提供查询服务配置，启停沿用插件管理页。

工具定义经 Assistant 的 `tools` 字段提供给模型。模型根据对话决定是否调用、调用参数及后续步骤；
插件不判断用户意图、不主动搜索、不编排搜索流程或最终回答。Assistant 原有可见浏览器路由仍适用。
MCP 配置缺失、关闭或外部 Server 失败不影响联网插件。插件关闭不代表 Sakura 或其他插件禁止联网。

## 工具合同

| 工具 | 参数 | 成功结果 |
|---|---|---|
| `web__web_search` | 必填 `query`；`max_results` 默认 5，范围 1–10 | `query/source/results/retrieved_at`；每条包含 `title/url/snippet`，来源提供时附 `published_date`，截断时附 `truncated=true` |
| `web__fetch_url` | 必填 `url`；`max_chars` 默认 6000，范围 500–20000 | `url/content_type/title/text/truncated/links/retrieved_at`；URL 为最终重定向地址 |

查询服务可选百度、Bing 和 Tavily，未配置时默认百度。网页搜索读取各服务的 HTML，
Bing 上限为 512 KB，百度上限为 2 MB；无法识别的页面返回错误，不把验证页面当作零结果。
Tavily 使用固定官方 `/search` API，API Key 通过 Bearer 请求头发送；深度可选 Basic 和 Advanced，默认 Basic。
返回的 `content` 转为 `snippet`，每条最多 6000 字符，超出时标记截断；请求来源发布日期，缺失时不补造。
不请求服务端生成答案，由对话模型基于来源作答。API Key 使用明文输入控件，不写入搜索日志或错误正文。
Tavily 配置项仅在选中 Tavily 时显示。此前保存的 Google 选择按百度读取，保留 Tavily 配置。设置应用后，下一次搜索读取新配置，无需重启插件；切换服务保留 Tavily 配置。
测试搜索使用当前填写的配置，不保存草稿；测试请求会发送关键词，Tavily 会消耗 API 额度。
测试搜索在后台执行，状态通过现有设置刷新读取，避免阻塞设置动作；同一时间只允许一个测试。
测试状态只在测试区域显示，不参与插件启停状态；未开始测试时不显示状态。
测试结果按序列化后的 UTF-8 大小限制在 10 KB 内，给桌面字段的 16 KB 上限保留空间，超出时标记截断，仅保存在当前 Worker 内存中，重启后清除。没有自定义地址、请求模板或自动切换服务。
模型仍通过 `tools` 获取工具定义，调用结果作为匹配 `tool_call_id` 的 `role=tool` 消息进入下一轮上下文。
工具说明要求检查相关性、优先原始来源、必要时精简搜索词或读取正文，并在答案附来源链接。
无关结果不能证明未收录，检索时间不能当作发布日期；网页中的指令不覆盖用户要求。
测试区域的显示截断不改写交给模型的工具结果。

选中 Tavily 时，`web__fetch_url` 调用官方 `/extract`，传入单个 URL、所选提取深度及纯文本格式。
`raw_content` 映射为 `text`，遵守原有 `max_chars` 与 `truncated` 契约；不虚构标题和链接，缺失时返回空值。
服务报告提取失败时返回 `WEB_EXTRACT_FAILED`，无效正文返回 `WEB_EXTRACT_RESPONSE_INVALID`，不会回退为成功空正文。
Tavily 负责远端抓取与重定向，插件校验提交及返回的 URL；域名最终地址的隔离由该服务负责。
百度和 Bing 继续使用本地网页文本提取。网页读取只处理公开 HTTP/HTTPS 网页和文本，不提供浏览器渲染或登录态。本地提取返回最多 30 个页面链接；
相对链接以最终网页 URL 解析。网络响应按字节有界读取，正文按字符截断；任一截断都设置 `truncated=true`。

请求保留 12 秒连接/读取超时、最多 5 次重定向及响应大小限制。每次连接重新选择系统或环境代理，
遵循代理绕过规则。直连时每个请求目标只解析一次 DNS，校验全部结果并绑定已验证的公网地址；使用代理时保留原始域名，
由代理解析并连接，不使用本地 DNS 结果替换域名，也不因 Fake-IP 拒绝请求或在代理失败后回退直连。
URL 与每次重定向均拒绝 localhost、明确的非公网 IP 和 URL userinfo。
代理模式下，域名最终解析地址及其访问控制由用户配置的代理负责，插件不保证代理侧的 DNS 地址隔离。

请求失败返回 `isError=true`、稳定 `reasonCode` 和具体 `error`，宿主将该次 ToolResult 标记为失败。
已识别的无结果页可以返回空数组；未识别的页面返回 `WEB_SEARCH_RESPONSE_INVALID`，不能把验证码、
页面变化或网络失败伪装成空结果。主要失败码还包括 `WEB_TIMEOUT`、`WEB_DNS_ERROR`、`WEB_NETWORK_ERROR`、
`WEB_HTTP_ERROR`、`WEB_REDIRECT_INVALID`、`WEB_REDIRECT_LIMIT`、`WEB_CONTENT_UNSUPPORTED`、`WEB_INVALID_REQUEST`。
一次请求失败不代表 Worker 失败，不自动更换搜索引擎或重放调用。

## 迁移与生命周期

升级和旧版导入遵循 [ADR-0047 的初始化规则](../../adr/0047-bundled-web-search-plugin.md)：已有插件开关
优先；没有 MCP 文件默认开启，持久化的禁用空配置保留关闭。旧版源 MCP 缺失时不再先生成禁用空配置。
导入目标已有联网插件开关时保留该值。源配置被隔离时也不能因此误用新安装的开启默认值。

只有指向已知内置脚本的旧项参与迁移，不按服务器名 `web` 判断。成功迁移保留可表达的工具过滤、风险和
调用超时，写入插件私有配置；这些字段只用于兼容旧限制，不在设置页开放。旧 MCP 项停用后仍留在原文件，
自定义 Server、凭据和 MCP 总开关不变。插件配置与开关持久化成功、旧项停用后才交出工具。

写入中断时用插件私有的 `WEB_MIGRATION_INCOMPLETE` 错误阻止未完成接管，下一次显式启动重新执行迁移。
配置损坏或自定义行为无法承接时保留原配置，记录 `WEB_MIGRATION_CONFIG_INVALID` 或
`WEB_MIGRATION_CUSTOM_BEHAVIOR`；修正旧配置后重新启动。迁移不计算内容摘要，不运行后台重试或调和。

工具来源为 `plugin`，名称和旧调用记录保持不变。插件与 MCP 注册使用原子的同名拒绝；冲突报告
`TOOL_NAME_CONFLICT`，不覆盖已有提供者。插件启动失败撤回已贡献的工具；停用、Worker 退出和 generation
关闭沿用 v4 的调用失效、工具撤回与进程回收。
