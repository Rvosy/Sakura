# Sakura 官网

`sakura.cialloo.cn` 的 Astro / Starlight 源码在此维护。页面内容位于 `src/content/docs/`，导航在
`astro.config.mjs`，样式在 `src/styles/starlight.css`，图片在 `public/images/`。新增文档页时同步检查导航和链接。

产品用法和开发接口的详细来源在仓库 `docs/`。本站保留介绍、快速入口和指向对应指南的链接；修改产品行为时，
先更新所属指南，再检查本站摘要与链接。历史版本用法从对应 Release 或 Git 标签查询。

## 本地构建

使用 Node.js 24 和 pnpm 11.19.0，在本目录执行：

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm preview --host 127.0.0.1
```

`dist/`、`.astro/` 和依赖目录不入库。官网为静态站点，服务器不需要运行 Node 进程。

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
