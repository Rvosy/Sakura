---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-10
---

# Runtime v2 运行日志开发指南

Runtime v2 的统一运行日志为 `data/logs/sakura-runtime.log`。Rust 日志服务是唯一文件 writer；Python
Core 和 WebView 不得自行打开该文件。行为与隐私强制契约见
[`WP-4L-01 Spec`](../specs/runtime-v2/WP-4L-01-runtime-observability.md)。

## Rust 事件

Rust 调用统一服务提交固定 `channel/event/message` 和批准的关联 ID/attributes。调用必须是非阻塞的；
调用方不能因日志返回失败改变 command、health 或 shutdown 结果。新增事件时同时增加注册表/字段测试，
不要把 `Debug` 格式、原始 `Error`、路径、请求/响应对象或环境变量塞进 attributes。

等级由 `SAKURA_RUNTIME_V2_LOG_LEVEL` 控制，默认 `info`。`debug`/`trace` 只增加事件密度，不允许记录正文
或凭据。warning/error 应描述稳定失败类别；具体异常正文只留在内存中的有界故障诊断，不持久化。

## Python Core 事件

Core Host 启动时安装 stderr bridge。现有 `app.core.runtime_log.log_event` 和 `app.*` 标准 logger 会被转换为：

```text
SAKURA_RUNTIME_LOG_V1\t{"severity":"info","channel":"core.chat",...}
```

只允许 bridge 模块生成该行；业务代码仍使用 `log_event` 或标准 logger。不要 `print` 到 stdout，因为 stdout
只承载 Core framed protocol。聊天 worker 必须进入 operation interaction context，终态后清理。

## WebView 事件

前端通过 runtime diagnostics 模块批量提交固定事件。invoke 包装器只记录 command 名、稳定 outcome/code、
耗时、operation ID 和 revision；它不检查或复制 args/result/error message。业务 Promise 的返回值和拒绝
对象必须原样传回，诊断 command 失败必须被吞掉。

## 本地验证

```powershell
runtime\python.exe -m harness run journey-observability
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked wp_4l_01 -- --test-threads=1
node --test desktop/frontend/tests/runtime-diagnostics.test.js
```

排查隐私回归时，用专门 sentinel 作为 API key、聊天正文、工具参数、绝对路径和 generation credential，
退出后扫描 `sakura-runtime.log*`。测试只能使用隔离 assistant root，不得用真实用户 `data/` 注入 sentinel。
