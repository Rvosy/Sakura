---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-10
---

# Runtime v2 运行日志与故障排查

Runtime v2 会默认把 info 及以上的本地诊断追加到：

```text
data/logs/sakura-runtime.log
```

文件达到约 10 MiB 后会轮转，最多保留 `sakura-runtime.log.1` 到 `.5`。日志仅保存在本机，不会自动上传，
也不会记录 API Key、对话正文、Prompt、工具参数/结果或绝对路径。当前版本还没有日志查看器、导出按钮或
Repair 页面；这些功能会在后续迁移阶段单独提供。

升级前已经存在的 `memory-initialization.jsonl` 会原样保留作为历史记录，但 Runtime v2 不再追加它；新的
Memory 启动诊断请以统一日志为准。

## 遇到问题时

1. 记下问题发生的大致时间，并尽量正常退出 Sakura，让最后的 warning/error 完成刷新。
2. 复制 `data/logs/sakura-runtime.log` 和相邻备份到单独目录，再重新启动，避免后续轮转覆盖现场。
3. 提供日志前仍建议搜索自己的 API Key、聊天原文和本机用户名；如果意外命中，请不要上传，并向维护者
   报告隐私缺陷。
4. 不要删除 `data/`、Memory/Qdrant 文件、共享锁或日志来尝试修复。统一日志只用于诊断，不是修复入口。

日志写入失败不会阻止聊天、设置、Core 恢复或正常退出，所以文件缺失不一定表示应用没有运行。第二实例
因共享锁冲突退出时不会触碰日志；应在已经运行的 Sakura 实例中继续操作。

`SAKURA_RUNTIME_V2_LOG_LEVEL=error|warn|info|debug|trace` 可供开发者临时调整事件密度，默认是 `info`。
提高到 trace 仍不会开放正文和凭据记录。Runtime v2 暂不读取旧版 `debug.file_enabled`。
