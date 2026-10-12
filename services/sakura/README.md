# Sakura 在线服务

本目录维护遥测接收、私人控制台、版本清单与分析包导出。运行、配置、部署和回退统一见[在线服务指南](../../docs/development/services.md)。

- Python 服务入口：`app:app` 接收遥测，`console_app:app` 提供私人控制台。
- `dashboard/`：控制台前端；执行 `npm ci`、`npm run build` 构建。
- [site/](site/README.md)：官网，独立构建与发布。
- [deploy/](deploy/README.md)：Nginx、systemd 和 CI 导入配置。
- `tests/`：隔离数据库及协议测试。

本地运行与测试使用隔离目录，生产数据库和导出内容不进入仓库。
