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
- `release-distribution`：验证安装包、Portable、Updater 清单和发行镜像。
- `runtime-v2-shell`、`runtime-v2-window-surface`：桌面壳、角色表现、窗口几何与交互。
- `journey-mcp`、`journey-plugins`：MCP 和插件纵向产品链。
- `journey-visuals`：表现插件的资源、工坊、回复、播放、归档与授权。
- `journey-visuals-browser`：正式角色设置、工坊形态与草稿、数值/立绘播放、Spine 导入与播放，以及桌面 CSP 和多图保存回归；五个入口使用隔离数据。保存回归使用真实有界 Core 路由。需要 Playwright，Windows 默认使用已安装 Edge；原生窗口及重启事件由测试桥代替。
- `journey-tts`、`journey-character-studio`、`legacy-import`：TTS 播放链、角色工坊与 0.9.x 数据迁移安全边界。
- `journey-asr`：语音输入回填、独立识别插件与临时音频生命周期；真实麦克风和模型样本另行显式验证。
- `journey-observability`、`journey-agent-trace`：运行日志、跨层关联和私密 Trace。
- `runtime-v2-windows-interaction`：需要真实 Windows 桌面的透明点击穿透验收。

以 `python -m harness list` 的输出为当前 profile/case 真相源。

## 选择验证范围

从受影响能力的窄测试或 profile 开始，不要求每次都运行 `smoke`。`docs` 已调用 `tools/check_docs.py`，
不必再单独执行一次检查器。所选 profile 已包含的 case 也无需另跑，除非需要定位失败。

相关检查通过后，只有新改动、失败或尚未覆盖的风险才扩大验证。CI 负责完整平台矩阵；需要设备的体验验证
单独说明结果。旧 WP 中的 required profiles、人工 accepted 状态和文件白名单不决定当前开发的测试范围。

`Test` workflow 对所有目标为 main/dev 的 PR 运行，不使用路径白名单，以免必需检查因文件目录被过滤而一直等待。独立的 telemetry job 覆盖服务器回归、后台构建和 Chromium 测试。

PR 与主分支的 `Test` workflow 使用相同的检查步骤。定位偶发超时时，要同时检查测试等待、HTTP 请求、
夹具读写等各层期限；只延长外层等待，无法恢复已被内层超时丢弃的请求。普通异步测试的期限用于发现卡死，
不作为共享 CI runner 的性能指标。遥测测试统一留出 10 秒等待预算；取消、退出和队列溢出测试保持响应未完成，
通过请求到达与连接关闭确认时序。只有验证超时行为的用例才主动使用短 HTTP 期限。
本地 HTTP 夹具读取请求前显式恢复 accepted socket 的阻塞模式，避免 Windows 继承 listener 的非阻塞状态后
将暂时无数据误判成坏请求。Windows 单实例锁测试使用独立随机名称，避免与运行中的应用或其他测试进程争用。

CI 失败处理遵循 [项目约束](../AGENTS.md#ci-偶发失败必须闭环)：保留首次失败和每次诊断复跑的结果，
查清根因并验证修复后再关闭问题。不得用重跑变绿替代修复，也不得把已知失败留给合并后的主分支发现。
异步测试通过明确状态或同步信号建立先后关系；连续发送两条命令不保证接收线程会在同一状态下处理它们，
固定 sleep 也不能证明命令已处理完毕。启动中的重复 Retry 由 Supervisor 状态机测试验证；Runtime 缺失的
线程集成测试逐轮等待失败状态，验证后续手动重试和退出。

Rust 日志内容测试统一调用 `drain_and_shutdown_for_test()`：停止接收新记录，等待队列写完、文件刷新关闭和
写线程退出，10 秒仍未完成则失败。队列为空不代表最后一条记录已写完，不能用轮询队列后再调用生产退出接口
代替。生产 `shutdown()` 仍保留 500 毫秒上限和到期丢弃待写记录的行为，由暂停写线程的回归测试单独验证。

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
