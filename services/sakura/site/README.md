# Sakura 官网

`sakura.cialloo.cn` 的 Astro / Starlight 静态站点。页面在 `src/content/docs/`，导航在 `astro.config.mjs`，样式在 `src/styles/starlight.css`，图片在 `public/images/`。

本站提供介绍和快速入口，详细用法与开发接口统一维护在[仓库文档](../../../docs/README.md)。产品行为变化时同步摘要和链接，旧版本内容从 Releases 或 Git 标签查询。

## 本地开发

使用 Node.js 24 和 pnpm 11.19.0，在本目录执行：

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm preview --host 127.0.0.1
```

`dist/`、`.astro/` 和依赖目录不入库。服务器仅托管构建产物，无需运行 Node 进程。

## 发布

官网与服务端独立发布。使用 `deploy_site.py` 预览同步，再以 `--apply` 执行；该工具保留服务清单和根目录隐藏项。部署、备份和回退约定见[在线服务](../../../docs/development/services.md)。
