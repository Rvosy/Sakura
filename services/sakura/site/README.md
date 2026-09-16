# Sakura 官网

`sakura.cialloo.cn` 的 Astro / Starlight 源码在此维护。页面内容位于 `src/content/docs/`，导航在
`astro.config.mjs`，样式在 `src/styles/starlight.css`，图片在 `public/images/`。新增文档页时同步检查导航和链接。

源码迁自 [Rvosy/sakura-site](https://github.com/Rvosy/sakura-site)，基线提交为
`f19ca7cb1712c1aec9850dc9a64cc33c3b6f7cda`。原仓库未删除或归档；本分支合入后，官网维护入口统一到这里。
2026-09-16 已核对线上首页及路由；服务器只保存构建产物，没有找到源码检出。
该提交是导入的源码基线，不能据此断言线上文件就是由该提交构建。

## 本地构建

使用 Node.js 24 和 pnpm 11.19.0，在本目录执行：

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm preview --host 127.0.0.1
```

`dist/`、`.astro/` 和依赖目录不入库。官网为静态站点，服务器不需要运行 Node 进程。
此次迁入保留现有页面内容；其中产品介绍与安装指南尚未按当前桌面版本重新审校。

## 发布与回退

官网文件和服务端 Python 包独立发布。构建后可用 `tar -czf site.tar.gz -C dist .` 打包，
通过 SSH 技能上传到站点目录外的候选目录并解压。不要直接覆盖整份 `/www/wwwroot/Sakura`：
其中 `service/v1/` 是控制台管理的在线更新清单，隐藏文件和 `.well-known/` 由服务器管理。

生产写入前备份当前官网、确认候选绝对路径，并先预览变更。以下命令在服务器上运行；候选目录以实际上传位置为准：

```sh
python3 deploy_site.py /path/to/candidate /www/wwwroot/Sakura
# 检查输出后，获准发布时执行：
python3 deploy_site.py /path/to/candidate /www/wwwroot/Sakura --apply
```

该工具默认只预览，显式 `--apply` 才同步。使用已有站点用户 `www` 执行，避免生成 root 所有的网页。
它会清理候选中已不存在的旧网页和资源，但保留 `service/` 及根目录隐藏项，候选也不能覆盖这些路径。
官网构建文件直接覆盖更新，不凭大小和修改时间跳过文件，也不计算额外内容摘要。
需要服务器具备 Python 3 和 rsync。回退时将官网备份作为 source，使用同一工具，保留实时更新清单。

发布后验证首页、安装指南、搜索、图片，以及官网和 api 域名的清单仍可读。
普通静态文件发布不需要重启控制台、遥测进程或 Nginx；证书与站点配置继续由宝塔维护。
