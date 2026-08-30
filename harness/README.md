# Sakura Product Harness

Sakura 的产品能力验证入口。Harness 不替代 pytest、Node 或 Rust 测试；行为断言仍位于各测试目录，
`suites.json` 只把真实检查组织为稳定 profile，Runner 负责执行并生成机器可读 JSON 报告。

长期行为契约见 [`docs/specs/product-harness.md`](../docs/specs/product-harness.md)，设计取舍见
[`ADR-0021`](../docs/adr/0021-product-harness-outcome-verification.md)。

## 使用

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run journey-plugins
runtime\python.exe -m harness run runtime-v2-shell --report temp\harness\shell.json
```

macOS/Linux 将解释器路径替换为 `runtime/bin/python`。

命令退出码：`0` 全部 case 通过，`1` 至少一个 case 失败，`2` 调用或 manifest 错误。自动报告只表达
`passed` 或 `failed`；需要真实设备或人工观察的验收独立记录，不改变已执行自动 case 的结果。

## Profiles

- `harness`：验证 suite manifest、Runner、timeout、输出捕获和报告。
- `smoke`：快速验证 Product Harness 与 Core Host 协议。
- `docs`：验证文档目录、元数据、索引和本地链接。
- `unit`、`core-host`、`python-full`：Python 单元、Core 和完整离线回归。
- `runtime-v2-shell`、`runtime-v2-window-surface`：桌面壳、角色表现、窗口几何与交互。
- `journey-mcp`、`journey-plugins`：MCP 和插件纵向产品链。
- `journey-observability`、`journey-agent-trace`：运行日志、跨层关联和私密 Trace。
- `runtime-v2-windows-interaction`：需要真实 Windows 桌面的透明点击穿透验收。

以 `python -m harness list` 的输出为当前 profile/case 真相源。

## 执行与报告

Runner 按 profile 中声明的顺序执行全部 case。每个 case 使用 argv 数组启动，不经过 shell；`{python}`
替换为当前解释器，`{repo}` 替换为仓库绝对路径。

每次运行创建唯一的 `temp/harness/runtime-tmp/<run-id>`，默认注入 `TMPDIR`、`TMP`、`TEMP`。case 的
显式环境变量可以覆盖默认值。报告使用 UTF-8、UTC 时间和同目录原子替换，并保存 argv、退出码、timeout、
耗时以及 stdout/stderr；不会枚举环境变量或读取凭据。

`timeout_seconds` 是硬 deadline。超时后 case 失败并终止子进程，报告只保留实际捕获到的输出，不重试、
不增加隐藏宽限期，也不推断尚未产生的文本。

## 扩展

1. 将行为断言放入所属的 Python、Rust、frontend 或平台测试目录。
2. 在 `suites.json` 的 `cases` 中注册窄命令。
3. 将 case ID 加入最能代表该产品能力的 profile 或 journey。
4. 在对应 Spec 的 Verification 段引用测试或 profile。

不要把开发任务、文件范围、Git 起点、审批状态或执行过程写进 Harness manifest。
