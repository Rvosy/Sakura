---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-02
---

# ADR-0042：使用独立 Telemetry Edge，并默认开启匿名统计

## 背景

Sakura 已有结构化 Runtime Log 和私密 Agent Trace，但维护者仍要依赖用户主动提供截图、日志和复现步骤。只有错误码通常看不出
错误前的状态，也无法判断问题是否集中在某个版本、系统或升级路径。模型调用虽然已有 Provider usage 和 Context 构建摘要，这些
信息目前只保存在本机，无法用于比较版本和发现普遍的 Context 膨胀。

上传完整 Runtime Log 或 Agent Trace 会带出聊天、Memory、工具内容和本地路径。当前用户量也不需要 Sentry、OpenTelemetry
Collector、Kafka 或专用分析平台。仓库已有一个 Cloudflare Worker + D1 的错误写入 PoC，可以在不扩大 VPS 控制面权限的前提下
验证严格的结构化上报。

## 决策

- 使用独立的 Cloudflare Worker + D1 作为 Telemetry Edge。它与 `sakura.cialloo.cn/service/v1/` 静态控制面分开部署，
  静态控制面继续只读。
- Rust Shell 是唯一 HTTP 出站 owner。Core 只产生 schema 固定、无正文的本地候选；WebView 和插件不能自行上传。
- 设置页只提供一个“发送匿名统计数据”开关，同时控制错误报告、基础运行事件和模型运行指标。新安装和升级安装均默认开启；用户
  明确关闭后保持关闭。
- 不增加首次运行弹窗。开关旁的 `?` 按钮打开固定的 GitHub 用户文档，设置行直接说明主要数据边界。
- 每个安装生成随机 UUID v4 诊断 ID，每个错误另有 report ID。两者都不是账号、机器指纹或鉴权凭据。
- 错误报告只上传稳定错误分类、安全化 stack 和 RuntimeLogService 同源的白名单 breadcrumbs。客户端不读取磁盘日志。
- 模型指标在内存中从实际 Provider usage 和 Context summary 投影，二者分开保存。不得解析 Agent Trace 文件。
- 发送使用单后台任务和有界内存队列。每条最多尝试一次，不落盘、不补传；遥测故障不影响 Sakura。
- Worker 使用三个独立写 schema，D1 使用三张明确表。原始数据最多保留 90 天，D1 不保存客户端 IP 或完整 User-Agent。
- 开源客户端不携带固定 Secret。首版以严格 schema、请求大小和 Cloudflare 边缘限额控制滥用，不把 telemetry 当可信计费或权限数据。
- 首版不建设 Dashboard、账号系统、长期聚合管线、跨地域同步或大陆备用入口。

## 放弃的方案

### 默认关闭或拆成两个开关

默认关闭会让维护数据长期稀少。错误报告和模型指标分开控制更细，但增加了设置解释和状态组合。当前只收集内容无关的白名单字段，
因此采用一个默认开启、可随时关闭的开关。完整字段必须在用户文档中公开，未来扩大数据范围需要重新修改规范。

### 自动上传 Runtime Log 或 Agent Trace

Runtime Log 仍可能包含自由诊断，Agent Trace 明确保存聊天、Memory、工具和模型正文。重新脱敏整份文件的风险和维护成本都高于
直接投影少量结构化字段。

### 让 Core、WebView 和插件分别发送

多出站 owner 会产生不同的同意状态、代理行为和故障边界，也无法覆盖 Core 启动前或崩溃后的错误。Rust Shell 已拥有应用生命周期和
系统网络能力，统一发送最简单。

### 在客户端内置请求签名秘密

Sakura 是开源客户端，固定 Secret 或 HMAC key 可以从源码或二进制中取得。它不能证明请求来自官方客户端，只会制造错误的安全感。

### 使用磁盘队列保证送达

遥测不是业务数据。为少量统计增加持久队列、重试和恢复会扩大用户数据面，也会把网络故障带到启动和退出路径。允许丢失更符合当前
用途。

### 把写接口放进 VPS Sakura Service

静态控制面当前由 Nginx 提供公开 JSON，没有应用数据库和公开写入口。把 telemetry 加进去会扩大 VPS 权限与攻击面。Worker + D1
已经满足当前请求量，两个服务保持独立更容易维护。

### 引入完整 Observability 平台

当前只需要按版本、错误码和随机 ID 查询少量记录。Sentry、OpenTelemetry Collector、Kafka、ClickHouse、Redis 和管理后台会增加
部署、权限、备份和升级工作，没有对应收益。

## 后果

维护者能按版本、平台、诊断 ID、report ID 和模型 Context 指标定位问题，同时不要求用户先导出日志。代价是匿名统计默认开启，
因此设置页和用户文档必须清楚说明数据范围、关闭方法、保留期和删除方式。

网络不可达时记录会丢失，统计也可能被伪造。这两点是有意接受的限制。数据不能用于计费、权限、精确活跃度或官方客户端证明。
以后若要上传详细诊断包、Agent Trace、细粒度行为、长期聚合或用户账号，必须另立规范和决策，不能沿用当前开关静默扩大范围。

本 ADR 扩展但不取代 ADR-0012、ADR-0013 和 ADR-0041。产品合同见
[远程诊断与匿名统计](../specs/runtime-v2/remote-diagnostics-telemetry.md)。
