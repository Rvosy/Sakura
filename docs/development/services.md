# 在线服务开发与运维

服务职责、发布草稿、公开数据和诊断接收约定见[在线服务规范](../specs/services.md)。本页说明仓库工具和部署模板的用法；实际服务器路径、账号与进程管理方式以该环境配置为准。

## 本地构建与运行

服务使用 Python 3.12+，依赖安装到服务专用虚拟环境。仓库开发使用项目 Python；生产命令使用服务器的服务环境，不与桌面发行 Python 混用。

先构建控制台，再生成包含服务代码与静态界面的包：

```text
npm ci --prefix services/sakura/dashboard
npm run build --prefix services/sakura/dashboard
runtime\python.exe services/sakura/package_release.py --output artifacts/sakura-service.tar.gz
```

包内包含服务 Python、admin_static、依赖清单和部署模板，不包含官网。打包工具不会连接服务器或完成部署。

本地调试将 SAKURA_TELEMETRY_DB_PATH、SAKURA_TELEMETRY_EXPORT_ROOT、SAKURA_CONSOLE_DB、SAKURA_RELEASE_ROOT、SAKURA_RELEASE_LOCK、SAKURA_EXPORT_LOCK 指向独立测试目录。在服务工作目录使用对应环境运行：

```text
python -m uvicorn app:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
python -m uvicorn console_app:app --host 127.0.0.1 --port 8766 --workers 1 --no-access-log
```

控制台请求仍需配置允许的 Host，写请求带同源 Origin 和 JSON 类型。不要把生产数据库或公开发布目录用于测试；服务业务测试见[tests](../../services/sakura/tests/)。

## 仓库部署模板约定

以下值由[部署模板](../../services/sakura/deploy/)和服务默认配置约定，部署到目标环境时同步调整对应路径、账号与权限。

| 对象 | 模板或默认值 |
|---|---|
| 控制台代码 | /opt/sakura-service/current |
| 控制台进程 | sakura-console 用户，sakura-console.service，监听 127.0.0.1:8766 |
| 控制台 Python | /opt/sakura-console-venv/bin/python |
| 诊断接收上游 | 127.0.0.1:8765 |
| 诊断数据库 | /var/lib/sakura-telemetry/telemetry.db |
| 控制台数据库 | /var/lib/sakura-console/console.db |
| 控制台导出与锁 | /var/lib/sakura-console 下的 exports、publish.lock、export.lock |
| 公开 JSON | /www/wwwroot/Sakura/service/v1/ |
| 官网根目录 | /www/wwwroot/Sakura/ |

诊断接收端的代码目录、运行账号和进程管理由部署环境配置；接收进程需独立运行，并具有诊断数据库的写权限。

控制台模板使用 sakura-diagnostics 组读取诊断数据，其 systemd 可写目录仅包含控制台数据与公开 JSON。接收端端口和控制台端口都只监听回环地址，外部流量经 Nginx。

Nginx 模板分开管理 api.sakura.cialloo.cn 与 adm.sakura.cialloo.cn；官网配置引用兼容的 service/v1 片段。location 片段放入 snippets 后由站点引用，不作为独立 vhost 加载。旧域名转发由实际站点配置维护，不能仅部署新模板就假定兼容入口已经生效。

## CI 草稿导入权限

CI SSH key 限定到 /usr/local/sbin/sakura-release-deploy，该入口拒绝额外远程命令，再通过精确的无参数 sudo 入口调用 /usr/local/sbin/sakura-ci-import。

导入脚本使用 /opt/sakura-console-venv 和 /opt/sakura-service/current/ci_import.py，以 sakura-console 身份写私人草稿。与之配套的 sudoers 只授予这一个入口，不给 CI 公开静态目录写权限或通用 shell。

GitHub 配置 SAKURA_SERVICE_DEPLOY_KEY 与固定主机公钥 SAKURA_SERVICE_KNOWN_HOSTS。主机变更时由维护者更新已确认的公钥，不在部署过程中自动信任新主机。

## 部署与代码回退

部署包解压到独立版本目录，安装相应服务依赖并保留上一版代码。备份实际配置和数据库后切换 current，重启控制台，检查进程、受保护的管理接口及公开文件。

接收端另行部署并检查 /health 和实际写入路径。诊断契约变更先部署兼容服务端，再发布使用它的客户端。官网发布不重启控制台或接收端。

代码回退切换上一版代码，保留新产生的数据库记录、草稿、导出及公开发布资料。不能用旧数据库快照覆盖期间产生的数据；涉及数据格式变化时，应确认回退代码的兼容性。

SQLite 在线备份使用 backup API，不能只复制主数据库而遗漏 WAL。认证文件、密钥、在线数据库和公开版本 JSON 不提交仓库。

## 检查服务与证书

以下命令对应仓库控制台和证书模板：

```text
systemctl status sakura-console
journalctl -u sakura-console --since today
nginx -t
systemctl list-timers sakura-cert-renew.timer
/opt/sakura-acme-venv/bin/certbot renew --cert-name sakura-console --dry-run
```

修改 Nginx 后先通过配置检查，再重载。模板证书位于 /etc/letsencrypt/live/sakura-console/，ACME challenge 根为 /var/lib/sakura-acme，续期由 sakura-cert-renew.timer 管理。实际证书和站点管理方式不同的环境应使用对应配置。

故障按反向代理、认证、服务进程、数据库和公开文件逐步定位，保留出错步骤与原始原因。单个进程启动成功不能代替端到端接口结果。

## 分析包与映射

在服务目录使用服务 Python 导出分析包：

```text
python export_bundle.py --output /private/analysis.zip
python export_bundle.py --report <report-id> --output /private/report.zip
python verify_bundle.py /private/analysis.zip
```

可用 start/end/build/version/installation/run/generation/operation/group/component/reason/severity/platform 筛选，include-test 显式包含测试数据。分析包内容和完成条件见[在线服务规范](../specs/services.md#私人控制台与分析包)。

构建映射按 buildId 保存于 builds 下，由对应发行的 diagnostic-build 产生。缺少映射保留未知位置，不套用其他版本；原始错误、路径和栈仍可用于定位。

## 官网单独发布

[官网](../../services/sakura/site/)使用 Astro/Starlight，独立构建。目标站点目录需已存在，服务器需安装 rsync；使用拥有目标目录写权限的账号执行同步。

```text
python services/sakura/site/deploy_site.py <dist目录> <站点根目录>
```

命令默认预览差异，确认目标与范围后加 --apply。同步保留站点根 service/ 和隐藏文件，不覆盖更新资料、证书入口或服务数据。
