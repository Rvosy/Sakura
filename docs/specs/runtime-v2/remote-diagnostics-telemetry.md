---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-12
---

# 远程诊断与运行统计

## 目的与边界

错误报告必须保留能用于修复 bug 的现场：原始异常消息、异常链、调用栈、失败位置和有关上下文。稳定错误码用于检索和统计，不替代底层原因。维护者从报告中应能提出可验证的排查方向；缺失的旧版证据不补造。

Rust 是唯一 HTTP 出站 owner。Core 和插件通过现有日志/遥测 bridge 提交诊断，WebView 通过现有诊断命令提交。服务继续使用 FastAPI、SQLite 和现有受保护管理端。架构取舍见 [ADR-0048](../../adr/0048-original-error-reports.md)。

## 开关与身份

`config/ui.json` schema 1 的 `settings.telemetry.enabled` 控制错误、运行事件和模型指标，默认开启。诊断 ID 为随机 UUID v4，不是账号、设备指纹或鉴权凭据。关闭立即停止发送、取消在途请求并清理待发错误；其他产品功能不受影响。重新生成诊断 ID 也清理旧 ID 的待发错误，不修改服务端历史数据。

不新增首次运行弹窗。设置行明确说明错误报告包含原始报错、调用栈和相关路径；帮助按钮打开 [用户说明](../../userdocs/REMOTE_DIAGNOSTICS_AND_TELEMETRY.md)。

## v3 错误报告

`POST /v3/errors` 接收单条 JSON，成功入库返回 `202`，body 上限 128 KiB。客户端在采集时保留诊断，再做具体凭据替换；服务器校验结构、类型和大小，不因文本含 URL、绝对路径、中文或原始异常而拒收。

契约以 `tools/telemetry_server/v3_models.py` 和 `desktop/src-tauri/src/telemetry.rs` 为准：

|字段|含义|
|---|---|
|`schema=3`、`reportId`|协议版本、报告 UUID；服务端按 reportId 幂等入库|
|`installationId/runId/operationId`|安装、运行和操作关联；缺失的 operation 不猜测|
|`app/system`|版本、渠道、系统/架构及可获取的 WebView 版本|
|`diagnostics`|buildId、environment、generation、occurredMs；occurredMs 是运行内时间点|
|`error`|component、event、code、exceptionType、fingerprint；未知错误不需要预先登记代码|
|`details`|严重程度、影响、阶段、系统退出/超时信息、恢复和修复结果等结构化事实|
|`evidence`|原始诊断及必要的标量上下文；字符串不是 SafeToken|
|`stack/breadcrumbs`|兼容的相对位置帧和错误前最多 40 条事件；完整异常栈在 evidence 中|

`evidence` 当前采集字段：

- `diagnostic`：底层错误原文；`error_type/cause_type`：真实类型。
- `exception_chain/exception_stack`：异常链和逐层栈，包括依赖帧、插件进程传回的诊断；`recovery_diagnostic` 单独保存恢复失败，不覆盖原始失败。
- `exception_site/source_file/source_line`：可获取的失败位置。
- `errno/winerror/exit_code/status/timeout_ms`：系统、子进程与请求事实。
- `plugin_id/plugin_name/provider/model/endpoint/url/path`：发生故障的实际组件、模型、请求目标和路径，不上传完整插件清单。
- `stderr/stage/command/request_id/window_label/provider_error_code/provider_error_type/repair_reason/repair_outcome`：有关现场字段。

Core 的进程边界复用 `exception_diagnostics` 的结果，不再为遥测另造一个只有类型和安全栈的摘要。Rust 在本地日志显示属性过滤之前取得诊断；本地日志等级不会阻断遥测。WebView 保留 message、原始 stack 和 cause 链；Rust panic 保留 panic 原文、位置和 backtrace。

诊断字段优先来自真正捕获异常的位置。发生 error/warning 且有诊断原文或异常栈时，即使类型或错误码此前未知，也可以形成报告。普通 stderr 行进入事件上下文，避免每行 traceback 单独生成报告；已知故障的既有分类继续保留。用户取消本身不生成故障报告。

## 凭据处理

保留实际文件路径、盘符、中文、空格、URL host/path、普通查询参数和模型名称。错误原因不得仅因含这些内容被整段替换。

只替换当前请求已知的凭据值，以及明确的 API key、Authorization、Cookie、password/token 参数、URL userinfo 等凭据。保留字段名和报错上下文。环境变量仅在名称表明是凭据时参与精确替换，不能把所有环境变量值都当成秘密，误删 HOME、PATH 等目录线索。

Python、插件 SDK、WebView、Rust 遵循相同的定向处理原则。服务端持久化、管理页面和导出器不追加全文正则清洗。页面按纯文本渲染诊断，不把报错当作 HTML。

不默认采集完整聊天、Prompt、Memory、工具输入输出、请求正文、局部变量或磁盘日志文件。Provider 错误保留服务端 error.message/code/type；非 JSON 错误和解析失败保留有长度上限的原始错误。原始异常本身可能包含失败片段和本地路径，不能再承诺“错误文本绝不含用户内容”。

## 大小、分组和发送

异常链/栈按字段有界，超限文字带 `[truncated: ...]` 标记；整个报告还按 UTF-8 序列化字节数控制。超限时缩减具体大字段，不把整份错误变回一个代码。Breadcrumb 优先保留失败、阶段变化和诊断，普通成功 IPC 往返不占用现场。

客户端在同 generation 内比较组件、事件、代码、原因、阶段、原始 message/chain/stack 和旧位置帧。不同底层原因保留独立样本；完全重复的样本保留首份报告并累计重复次数。`fingerprintVersion=3` 中 fingerprint 是随机样本组 ID，沿用旧字段名供累计次数关联，不计算自制内容摘要。服务端用实际分组字段比较跨 run 的报告，旧 v1/v2 fingerprint 保持可读。

Rust 使用一个后台发送任务和有界队列。错误发送前写入 UI 配置同级的 `telemetry-pending-errors/`，最多保留 64 份、每份不超过 128 KiB、最长 7 天；容量满时淘汰最旧记录。收到 202 后删除对应待发文件。失败保留，下次启动及运行中空闲时每分钟重试。重发保留原 reportId、run 和 generation，不能变成本次运行的新错误。关闭/重置 ID 清理待发目录，队列使用 epoch 隔离旧设置。

运行事件、模型指标仍为内存发送；不为它们增加落盘平台。磁盘写入或发送失败不得使产品操作失败，已有发送诊断计数保持可查询。断网退出后补发依赖曾成功写入待发文件；硬终止发生在捕获之前的 native crash 不在当前保证内。

## v2 运行事件和模型指标

`POST /v2/events` 仍为最多 10 条、8 KiB 的批量事件；`POST /v2/model-calls` 仍为最多 10 条、16 KiB 的批量模型指标。字段定义见 `v2_models.py` 和 Rust `telemetry/contract.rs`，不把完整错误塞入它们的固定 details。

聊天在 `RealChatBoundary` 确定唯一终态后记录 `chat.finished`，分别报告 success/failed/cancelled，并使用操作实际耗时。诊断用的 `chat.request.failed` 不再承担聊天终态计数，避免重复和遗漏成功/取消。普通 info 事件不默认标为 error/unavailable；`durationMs` 取 elapsedMs，没有就省略，不能填 occurredMs。

TTS、迁移、修复继续记录已有业务结果，source/repair/recovery 等既有字段不得被日志别名过滤吞掉。`migration.recovery` 只用于真正的恢复事件，不能把所有导入失败都视作恢复失败。

模型 usage 与 Context 估计分开；失败保留 HTTP 状态、faultDomain、reasonCode、stage、attemptCount 和 compatibilityFallback。模型指标继续归类 modelFamily；错误报告可以包含实际 model 和 endpoint。

## 存储、查看和导出

数据库在原三张表上增量添加列，保留历史 received_at。v3 在 `error_events.report_json` 保存完整报告，`group_key` 保存实际分组字段，不重算历史记录。管理端详情先显示原始报错、栈、异常链、恢复错误，再显示环境和统计；可复制完整 JSON。旧记录没有原文时明确显示未采集。

`/admin/api/v2/records/errors` 的 q 支持最多 512 字符的诊断文本搜索。分组查询按实际错误合并跨 run 的相同样本，组内不同原因不混并。重复摘要为累计值：按 installation/run/generation/fingerprint 取最大值，再聚合，不能直接求和。

`export_bundle.py` 与管理端 ZIP 下载使用同一快照导出。JSONL 保留已入库 evidence 和完整 report，不再遇到 URL/路径就替换整段字符串。导出仍包含 schema、时间线、构建映射、说明和完整性记录；只读事务、权限和短期下载清理机制不变。历史缺失字段不补零、不恢复不存在的原文。

buildId 映射由维护者的发布产物提供；源码定位使用对应 commit。没有 sourcemap/符号文件时保留原始编译位置并注明未知，不能假装已经符号化。当前不采集 native minidump，也不部署新的第三方诊断平台。

## 部署与兼容

生产服务先支持 v3，再发布客户端。Nginx 为 `/v3/errors` 增加精确路由和 128 KiB 上限，沿用 Origin Secret、Host 隔离和管理端 Basic Auth；不开放任意 `/v3/*`。v1/v2 原接口、上限和校验保持兼容。生产部署不由代码合入自动代表完成。

SQLite 原始记录保留 90 天，接收时间使用北京时间。Admin/API/导出仅在受保护的管理入口开放，原始诊断不进入公开静态目录。应用不将客户端 IP、User-Agent 写入诊断数据库；Nginx/Uvicorn 不记录请求 body 或凭据。

## 验收

重点验证真实错误路径：Python 异常 → bridge → Rust HTTP 发送 → FastAPI → SQLite → 详情/ZIP，并比较原文除具体凭据外是否一致。覆盖相同上层代码下的不同 SQLite 原因、插件失败路径、Provider 错误、WebView 原始 stack/cause、未知异常、大文本、断网重启补发及关闭后清理。用实际聊天边界验证三个终态各记录一次。

工具入口与部署准备见 [服务端 README](../../../tools/telemetry_server/README.md)。测试全部使用隔离根和测试凭据，不向生产写入验收记录。
