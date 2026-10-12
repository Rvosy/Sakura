# Cloudflare 遥测示例

本目录提供独立的 Worker/D1 示例，当前 Sakura 在线服务使用 [services/sakura](../../services/sakura/README.md)。示例不接入桌面客户端，不代表客户端实际发送范围。

示例只接收 `/v1/errors` 的固定标识字段，提供 `/health`。在隔离 Cloudflare 环境运行 `npm install`，配置 `wrangler.jsonc` 的 D1 绑定后，通过 `npm run db:migrate:remote` 建表、`npm run deploy` 部署。

真实诊断协议与运维见[在线服务指南](../../docs/development/services.md)。
