---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-11
---

# 联网插件

## 能力边界

安装包提供“联网工具”插件 `sakura.web`（目录 `plugins/builtin/sakura_web`），新安装默认启用。插件通过 Plugin API v4
的 `sakura.host.tools` 提供搜索和网页读取，使用独立 Worker。实现只依赖标准库，不导入 `app.*`，
不启动 MCP Server、不安装额外依赖，也不贡献自定义设置区块。启停沿用插件管理页。

工具定义经 Assistant 的 `tools` 字段提供给模型。模型根据对话决定是否调用、调用参数及后续步骤；
插件不判断用户意图、不主动搜索、不编排搜索流程或最终回答。Assistant 原有可见浏览器路由仍适用。
MCP 配置缺失、关闭或外部 Server 失败不影响联网插件。插件关闭不代表 Sakura 或其他插件禁止联网。

## 工具合同

| 工具 | 参数 | 成功结果 |
|---|---|---|
| `web__web_search` | 必填 `query`；`max_results` 默认 5，范围 1–10 | `query`、`source`、`results`；每条包含 `title/url/snippet` |
| `web__fetch_url` | 必填 `url`；`max_chars` 默认 6000，范围 500–20000 | `url/content_type/title/text/truncated/links`；URL 为最终重定向地址 |

搜索固定请求 Bing 搜索结果页并解析自然搜索结果，不提供搜索引擎、语言、地区、代理或提示词自定义。
网页读取只处理公开 HTTP/HTTPS 网页和文本，不提供浏览器渲染或登录态。返回最多 30 个页面链接；
相对链接以最终网页 URL 解析。网络响应按字节有界读取，正文按字符截断；任一截断都设置 `truncated=true`。

请求保留 12 秒连接/读取超时、最多 5 次重定向及响应大小限制。每次连接校验 DNS 解析结果并绑定已验证
的公网地址；重定向再次校验目标。拒绝本机、私有网络地址和 URL userinfo，不提供绕过开关。

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
