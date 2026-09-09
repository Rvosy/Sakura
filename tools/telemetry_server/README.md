# Sakura Telemetry v2 服务器与分析包

本目录包含遥测服务器、后台与分析包导出源码。基础文件取自核对后的线上 BaoTa 服务；旧 Phase 2 目录不再作为部署来源。
客户端、服务端与后台必须按协议顺序上线。后端继续使用 FastAPI、Pydantic、SQLite；React 页面保留在 `dashboard/`，没有新增账号系统或消息队列。

## 本地隔离运行

使用 Python 3.12 或以上，在独立虚拟环境安装 `requirements.txt`。测试另装 pytest、httpx。以下命令从本目录运行；所有路径使用测试目录，不能指向生产数据库。

```sh
export SAKURA_TELEMETRY_DB_PATH=/tmp/sakura-telemetry-test/telemetry.db
export SAKURA_TELEMETRY_EXPORT_ROOT=/tmp/sakura-telemetry-test/exports
python -m uvicorn app:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
```

`/health` 检查数据库。Admin API 和静态页要求 `Host: admin.cialloo.cn`；本地 Host 隔离不提供密码认证。生产 Basic Auth 必须继续由 Nginx 执行，8765 不得对公网开放。
数据库初始化采用增量列和索引，不修改既有 received_at。v1/v2 均可入库；未知诊断字段不补零、不推测。

后台构建：在 `dashboard/` 执行 `npm ci && npm run build`，将 dist 内容放入服务根的 `admin_static/`，保留 `assets/` 子目录。HTML 位于 `admin_static/index.html`，脚本、样式和字体位于 `admin_static/assets/`；后端通过固定路由提供这些文件。原外部后台目录没有被覆盖。

## 下载与离线分析

后台“诊断与分析包”提供 SQL 筛选、问题组、错误报告、按安装及运行限定的时间线和数据质量。列表默认排除 development/acceptance；没有指定时间时覆盖整个保留期。
生成分析包后等待状态变为 ready，再下载。只含 operation ID 的旧链接必须先指定安装与运行。模型调用失败率不是聊天失败率，聊天结果查看 chat.finished。

命令行与后台共用 `export_bundle.py`，包括跨进程单导出锁、同一只读事务和字节/时间上限：

```sh
python export_bundle.py --output /private/analysis.zip
python export_bundle.py --start 2026-09-01T00:00:00+08:00 --end 2026-09-10T00:00:00+08:00 --build BUILD_ID --output /private/build.zip
python export_bundle.py --report REPORT_UUID --output /private/report.zip
python verify_bundle.py /private/report.zip
```

其他筛选参数：version、installation、run、generation、operation、group、component、reason、severity、platform。`--include-test` 明确纳入开发及验收样本。
CLI 输出由调用者保管；后台临时文件保留一小时。超出 5 分钟或 1 GiB 未压缩内容会失败并清理，不能拿到静默截断的包。

每个包包含 README、protocol.json、schema.json、manifest 和标准库校验器。先核对必需文件、大小和行数，再查看 groups.json；同一 run 按 occurred_ms 排序。
发生次数是累计最大值，不能把重复摘要求和。received_at_iso 明确为 +08:00；旧 received_at 没有被移动。没有终态、没有原因和没有位置都保留未知。

将发行产物中的 `diagnostic-build-<target>.json` 核对后按其中 buildId 放入服务器 `builds/<buildId>.json`。缺少映射会在 builds.json 明确显示 mapping=null。
正式打包时 Rust 会核对构建 ID、环境和映射文件是否存在；每次准备发行资源生成独立的构建 ID。工作区修改的包标为 development，避免污染正式版本统计。

## 验收入口

```sh
python -m pytest -q -s -c /dev/null -p no:cacheprovider tests/test_diagnostics.py
```

这组测试覆盖 v1/v2、重复累计、205 条分页、跨 run 关联、私有导出、全量/超限、时区、90 天保留、索引与并发入库。
旧 ZIP 兼容：`python tests/verify_legacy_zip.py OLD_ZIP`，只在临时数据库操作，不改原包。

真实 Core/Rust 请求体到 ZIP 的契约验收，从仓库根执行（最后一步使用服务器虚拟环境）：

```sh
runtime/bin/python tools/telemetry_server/tests/capture_core_wire.py /tmp/core-wire.jsonl
SAKURA_ACCEPTANCE_CORE_WIRE=/tmp/core-wire.jsonl SAKURA_ACCEPTANCE_WIRE_OUTPUT=/tmp/http-wire.json cargo test --manifest-path desktop/src-tauri/Cargo.toml telemetry::tests::acceptance_wire_capture -- --test-threads=1
python tools/telemetry_server/tests/verify_captured_wire.py /tmp/http-wire.json
```

该入口注入真实 ResponseWriter 失败并经过 Core bridge、Rust 校验/HTTP 发送、服务器入库和 ZIP 校验；中间保存 loopback 请求体后重放到 FastAPI。
它证明协议与离线定位，不代替 Tauri WebView 的 Windows 发布包验收或生产公网验证。WebView、TTS、回复修复、迁移分别有客户端回归测试；完整发布包故障矩阵仍是发布门槛。
后台验证：`npm test -- diagnostics.spec.ts`。测试使用 mock API，不能当成公网认证验收。

## 生产部署与回退

生产部署和客户端发布分别需要授权，服务端上线不代表客户端已完成发布包验收。每次部署保存版本、文件清单、备份位置和公网检查结果。`package_release.py` 生成的 `release-manifest.json` 使用 format 3，文件条目只记录字节数。

1. 用 SSH skill 的 `macmini` 别名重新核实平台、BaoTa 项目、Python 版本、部署版本和 Nginx 路由。对比线上代码与上一次部署备份，核对 `release-manifest.json` 中的文件清单和大小，确认本次待替换内容。
2. 在站点目录外、仅维护者可读的目录备份代码。用 SQLite backup API 备份一致性数据库；不要复制活跃 WAL 数据库单文件，不把含用户数据的备份提交仓库。
3. 在隔离复制数据库中安装依赖并启动新后端，验证增量迁移、v1/v2、后台查询与 ZIP。核对实际 BaoTa Python 兼容性，再通过现有项目发布。
4. Nginx 保留 Basic Auth、Origin Secret 和 Host 隔离。遥测域名只允许 `/health` 与 `/v[12]/errors|events|model-calls`，不得转发 `/admin`；Admin vhost 的鉴权覆盖全部 `/admin/api/v2/exports` 与下载路径。后台 ZIP 不能用 alias 暴露。
5. 使用一个 Uvicorn worker；代码路径仍为 `/opt/sakura-telemetry`，DB 为 `/var/lib/sakura-telemetry/telemetry.db`，export 根为 `/var/lib/sakura-telemetry/exports`。后者归服务用户所有，目录 0700，文件 0600。保留原来的 90 天清理任务。
6. 发布后验证真实公网：未认证后台返回 401；已认证 Admin 页面/API/下载成功；遥测域名访问 Admin 返回 404；v1/v2 合法请求入库、非法请求拒绝；下载包离线验真。使用 acceptance 环境，不能混入生产统计。检查 Nginx 及应用日志没有请求正文或凭据。
7. 服务器验证通过后再安排 Windows/macOS 客户端隔离验收，最后发布下一版。

旧宝塔后台配置的 `/admin/` 仅允许 GET。增加导出时，应为 `/admin/api/v2/exports` 添加精确 location，沿用现有 `auth_basic` 和密码文件，只允许 POST，并设置 `client_max_body_size 8k`。状态和下载仍由原受保护的 GET 路由处理；不要给整个后台开放写请求。源站则只代理 `/health` 与 `/v[12]/(errors|events|model-calls)`，其他路径返回 404，保留 Origin Secret 校验。

如果宝塔自带 Python 的 HTTPS 检查提示缺少证书信任链，可指定服务器系统 CA 文件（例如 `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`）。不能通过关闭证书校验代替公网 TLS 验收。

回退先暂停新版客户端发布，保留新增列和 v2 接收模块；可以回退页面或关闭有问题的查询入口。不得用旧数据库覆盖新上报，也不能把 v1-only 旧服务替回已有 v2 客户端使用的服务器。
生产备份由维护者在服务器私有目录管理。
