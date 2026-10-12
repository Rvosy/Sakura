# 服务部署配置

本目录保存 Nginx、systemd、证书续期及 CI 草稿导入配置。配置中的路径与域名需要对照部署环境，认证文件、密钥、数据库和在线版本 JSON 不入库。

- `sakura-site.conf`：官网站点。
- `sakura-admin.conf`、`sakura-api.conf`：私人控制台与公开接口。
- `sakura-admin-locations.conf`、`sakura-service-location.conf`：供站点引用的片段，放入 snippets 目录。
- `sakura-console.service`：控制台进程。
- `sakura-release-deploy`、`sakura-ci-import` 与 sudoers：CI 私人草稿导入入口。
- ACME 配置及定时任务：证书维护。

发布前备份实际配置并执行 `nginx -t`。官网、控制台与遥测分别发布，CI 导入草稿不改变公开清单。完整操作与回退见[在线服务指南](../../../docs/development/services.md)。
