# 测试入口

Harness 将现有 Python、前端、Rust 和平台测试组合为按功能选择的 profile。行为断言留在各测试目录，当前可用清单由 `suites.json` 维护。

## 使用

在仓库根目录运行，macOS / Linux 将 Python 路径换成 `runtime/bin/python`：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run <profile>
runtime\python.exe -m harness run docs
runtime\python.exe -m harness run journey-plugins
```

先选择受影响业务的窄测试或 profile；具体文件也可以直接用 pytest、Node 或 Cargo 执行。相关检查通过后，仅在新改动、失败或尚未覆盖的问题出现时扩大范围。`docs` 只检查文档中的本地链接。

Python 测试依赖见 [requirements-dev.txt](../tools/requirements-dev.txt)。完整 Python 套件还会启动 MCP 私有进程，需要在 `plugins/dependencies/sakura.mcp` 准备依赖及就绪标记；命令见 [Test 工作流](../.github/workflows/test.yml) 的 `Prepare private MCP test dependencies` 步骤。新工作树需单独准备该目录。

需要浏览器的旅程依赖 Playwright，Windows 默认使用已安装的 Edge。真实窗口、音频设备和平台体验需在对应环境验证，模拟桥接通过不代表设备验证通过。

## 结果与隔离

退出码 `0` 表示通过，`1` 表示检查失败，`2` 表示调用或清单错误。报告保存实际命令、退出码、耗时与输出。失败应定位原始原因，不能用反复重跑替代修复。

每次执行使用独立临时目录。Harness 报告与运行临时目录默认放在系统临时目录的 `sakura-harness` 下；直接运行 pytest 时，夹具目录放在系统临时目录的 `sakura-tests` 下。Harness 的 `--report` 和 pytest 的 `--basetemp` 可显式指定位置。避免把临时目录设在仓库内，以免 Codex 创建工作树时反复扫描测试产物。

清单中的超时用于终止卡死的检查，超时会记录失败并结束子进程，不自动重试。测试使用隔离数据，用户目录和真实模型不作为默认夹具。

Windows 本地构建可在仓库的 `.cargo/config.toml` 中设置 `[build]` 的 `target-dir`，例如 `"../sakura-build"`，将缓存放到仓库旁边。启动、打包和原生界面测试使用 Cargo 的实际输出目录。本机路径配置无需提交；新工作树通过本地环境初始化脚本复制该配置，各工作树使用自己的相邻缓存目录。

## 维护

新增测试先说明能发现什么业务错误。确需注册到 Harness 时，将窄命令加入 `cases`，再引用到相应 profile；文件删除时同步移除入口。测试标准统一见 [AGENTS.md](../AGENTS.md)。
