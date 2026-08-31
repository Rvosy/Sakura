---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-09-01
---

# Sakura Service 静态控制面上线记录（2026-09-01）

## 已发生事实

- `https://sakura.cialloo.cn/service/v1/releases.json` 已发布正式版 `1.0.2` 元数据。
- `announcements.json` 与 `known-issues.json` 已发布 schema 1 空集合。
- 三个端点由阿里云主机上的 Nginx 直接读取静态文件；没有新增常驻应用、数据库或容器。
- 版本文件写入由 `sakura-release` 专用账号的 forced command 校验，GitHub 仓库已配置专用部署 key 与固定 host key
  Secret。临时私钥在配置和验证后已从维护机删除。
- 自动发布改动位于 [PR #163](https://github.com/Rvosy/Sakura/pull/163)；本记录创建时 PR 可合并，Frontend 与
  Python Unit Tests 均通过，但尚未合并到 `main`。

## 变更与备份

- 站点：`sakura.cialloo.cn`
- 静态根：`/www/wwwroot/Sakura/service/v1/`
- 发布命令：`/usr/local/sbin/sakura-release-deploy`
- Nginx 片段：`/www/server/panel/vhost/nginx/snippets/sakura-service-location.conf`
- 部署前备份：`/var/backups/sakura-service/20260901T1930Z/`

第一次候选配置把 `location` 片段放入宝塔全局扫描的 `vhost/nginx/*.conf`，`nginx -t` 明确失败并阻止 reload；
线上旧进程未中断。片段移入 `snippets/`、修正 include 后再次校验通过，随后 reload 成功。

## 验证结果

- Nginx 配置语法通过且服务为 active。
- Sakura 网站、三个静态端点与既有 `new-api` 站点均返回 `200`；`new-api` 容器保持 healthy。
- JSON 响应具有 `application/json`、CORS、5 分钟公共缓存和 `nosniff`；POST 被拒绝为 `403`。
- 专用 key 可以幂等发布合法的 `1.0.2` 元数据；尝试通过该 key 执行 `id` 时 forced command 因空 JSON 拒绝，
  没有获得 shell。
- 服务端校验固定字段、稳定 SemVer、GitHub URL、版本一致性、32 KiB 上限和禁止自动降级。

## 未包含范围

本次没有修改桌面客户端来读取公告或已知问题，没有改变 Tauri Updater 的 GitHub endpoint，也没有部署 telemetry、
诊断上传、反馈、Agent Trace 上传、管理后台或 Cloudflare。相关能力若继续实施，必须另行设计和验证。

长期合同见 [Sakura Service 静态控制面合同](../../specs/runtime-v2/sakura-service.md)。
