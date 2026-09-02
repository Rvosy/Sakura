---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0042-remote-diagnostics-telemetry
updated: 2026-09-02
---

# ADR-0043：Telemetry Edge 使用 CDN 后的单机 FastAPI 与 SQLite

## 背景

ADR-0042 选择 Cloudflare Worker + D1 承载远程诊断。这个方案完成了 schema 和写入 PoC，但旧生产入口曾在请求到达 Worker
handler 前持续返回 1101；预览环境和另一账号下的部署成功也不能证明原入口可用。Sakura 需要一条能够从公网请求一直验收到数据库
记录的生产链路。

项目已有一台运行 Nginx 的 VPS，用户量只有几百。把遥测部署成独立的小型应用，可以复用现有运维环境，同时避免把写接口塞进
`sakura.cialloo.cn/service/v1/` 静态控制面。Phase 2 已通过公网写入、SQLite 查询、非法请求、Privacy Sentinel 和资源占用验收。

客户端与产品边界没有因此改变：匿名统计仍默认开启，用户可以用一个开关立即停止全部发送；Rust 仍是唯一 HTTP 出站 owner；聊天、
Prompt、Memory、工具内容、完整 Runtime Log 和 Agent Trace 仍不得上传。

## 决策

- 生产入口固定为 `https://telemetry.cialloo.cn/`。请求经过多吉云 CDN 和只属于 CDN → VPS 链路的 Origin Secret，再进入独立
  Nginx vhost；FastAPI 只监听 `127.0.0.1:8765`。
- Telemetry Edge 与静态 Sakura Service 共用 VPS，但不共用域名路径、vhost、应用进程或数据。静态控制面继续只读，也不接收
  installation ID 或诊断内容。
- 服务端使用 FastAPI、严格 Pydantic model、Python `sqlite3` 和一个 Uvicorn 进程。首版不引入 ORM、迁移框架、独立数据库、
  队列或容器编排。
- `/v1/errors` 接受一份 32 KiB 以内的报告；`/v1/events` 接受 1 至 10 条、总计 8 KiB 以内的 batch；
  `/v1/model-calls` 接受 1 至 10 条、总计 16 KiB 以内的 batch。hard cap 在 JSON 解析前执行。
- 所有 Pydantic model 使用 `extra="forbid"`。服务端拒绝未知字段、非法 enum、超长值和绝对路径，只使用参数绑定；batch 在完整
  校验后用单个 transaction 写入，不能部分成功。
- SQLite 位于 `/var/lib/sakura-telemetry/telemetry.db`，启用 WAL、`synchronous=NORMAL`、5 秒 busy timeout 和 foreign keys。
  三张明确表仍是 `error_events`、`telemetry_events`、`model_call_metrics`。
- `error_events.report_id` 唯一；重复报告不重复写入，但仍返回 `202`。除 report ID 外，只为 error 的 installation ID、接收时间、
  error code 和 app version 建索引。
- `received_at` 由服务端生成。数据库不保存 raw body、IP、User-Agent 或任意 JSON attributes。Nginx access log 关闭，Uvicorn 使用
  `--no-access-log`；错误日志可以保留，但不得记录请求 body、凭据或 Origin Secret。
- 系统 cron 每天 03:17 删除三张表中超过 90 天的原始记录，并执行被动 WAL checkpoint。首版不做每日 VACUUM、Dashboard、
  Admin API 或长期聚合管线。
- `GET /health` 必须同时检查 FastAPI 和 SQLite；数据库不可用时返回 `503`，不泄露内部路径或异常正文。
- 设置页仍只有一个默认开启的“发送匿名统计数据”开关。随机 UUID v4 诊断 ID、关闭/重新生成语义、Rust 单出站、有界内存队列、
  不重试和不落盘的客户端决定继续有效。
- 开源客户端不携带固定 Secret 或 HMAC key。CDN 会接触连接层 IP，但应用不提取转发 IP，SQLite 不保存 IP 或 User-Agent。
  遥测记录只能用于诊断和粗略统计，不能用于计费、权限或证明请求来自官方客户端。

## 放弃的方案

### 继续把 Cloudflare Worker + D1 作为生产入口

PoC 已证明应用 schema 可以工作，但旧生产入口的失败发生在 handler 之前，需要额外的供应商控制面排查。现有 VPS 数据面已经完成
公网端到端验收。对当前规模而言，继续维护两套生产候选只会增加排障路径；Worker + D1 保留为旧 PoC，不再是客户端合同。

### 把写接口并入静态 Sakura Service

静态控制面由 Nginx 直接提供只读 JSON，发布权限也限制为原子替换版本文件。遥测需要动态校验、数据库写入和定时清理。两者可以在
同一台 VPS 上运行，但必须保留独立 vhost、进程、数据库和权限边界。

### 使用 ORM、外部数据库或通用可观测平台

三张固定表和一个写进程不需要 SQLAlchemy、Alembic、PostgreSQL、Redis、Sentry 或 OpenTelemetry。Python `sqlite3` 的参数绑定和
短 transaction 已满足当前请求量，也让维护者可以直接检查 schema 和查询结果。

### 保存原始请求后再清洗

raw body、任意 attributes 和完整日志一旦落盘，后续清洗不能撤销隐私暴露。服务端必须在写入前完成第二次字段白名单和长度校验，
无法识别的数据直接拒绝。

### 保留访问日志用于滥用分析

访问日志会长期复制 IP、method 和 path，与当前只保存内容无关指标的范围不相称。源站关闭 Nginx/Uvicorn access log，滥用成本控制
放在 CDN 限流、严格 schema 和体积上限；Nginx error log 只用于服务故障，不能记录请求正文。

### 在客户端内置鉴权秘密或持久发送队列

开源客户端中的固定秘密无法证明请求来源。持久队列和重试会扩大本地数据面，并把非关键遥测带进启动和退出路径。两项都不采用。

## 后果

生产链路可以从公网接口一直验证到 SQLite 行，依赖和资源占用适合当前规模。维护者需要负责 VPS 进程、SQLite 文件、cron、CDN
配置和数据库备份；当前尚未配置独立数据库备份策略。

单机 SQLite 没有跨节点高可用，公开写接口也可能收到伪造数据。发送失败继续允许丢失，统计只能作为排查线索。以后若要加入账号、
详细诊断包、长期聚合、管理后台或另一套生产入口，必须新增 Spec 和 ADR，不能静默扩大当前开关的范围。

本 ADR 取代 ADR-0042，但不取代 ADR-0012、ADR-0013 或 ADR-0041。产品合同见
[远程诊断与匿名统计](../specs/runtime-v2/remote-diagnostics-telemetry.md)。
