# 测试入口

Harness 将现有 Python、前端、Rust 和平台测试组合为按功能选择的 profile。行为断言留在各测试目录，当前可用清单由 `suites.json` 维护。

## 使用

在仓库根目录运行，macOS / Linux 将 Python 路径换成 `runtime/bin/python`：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run <profile>
runtime\python.exe -m harness run docs
runtime\python.exe -m harness run journey-plugins --report temp/harness/plugins.json
```

先选择受影响业务的窄测试或 profile；具体文件也可以直接用 pytest、Node 或 Cargo 执行。相关检查通过后，仅在新改动、失败或尚未覆盖的问题出现时扩大范围。`docs` 只检查文档中的本地链接。

需要浏览器的旅程依赖 Playwright，Windows 默认使用已安装的 Edge。真实窗口、音频设备和平台体验需在对应环境验证，模拟桥接通过不代表设备验证通过。

## 结果与隔离

退出码 `0` 表示通过，`1` 表示检查失败，`2` 表示调用或清单错误。报告保存实际命令、退出码、耗时与输出。失败应定位原始原因，不能用反复重跑替代修复。

每次执行使用独立临时目录。清单中的超时用于终止卡死的检查，超时会记录失败并结束子进程，不自动重试。测试使用隔离数据，用户目录和真实模型不作为默认夹具。

## 维护

新增测试先说明能发现什么业务错误。确需注册到 Harness 时，将窄命令加入 `cases`，再引用到相应 profile；文件删除时同步移除入口。测试标准统一见 [AGENTS.md](../AGENTS.md)。
