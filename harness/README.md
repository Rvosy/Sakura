# Sakura Harness

这是 Sakura 的仓库级验证入口，面向开发者、Codex 和 CI。它不替代 `pytest`；它把已有检查组织成稳定的 profile，并为每次运行生成统一 JSON 报告。

## 为什么放在这里

`harness/` 与 `app/`、`tests/`、`scripts/` 同级：

- `app/` 只保留产品代码；
- `tests/` 继续保存测试实现；
- `scripts/` 保存构建、安装和运维脚本；
- `harness/` 只负责选择场景、执行检查和汇总证据。

这样后续可以加入 Tauri、桌宠 UI、真实 Core lifecycle 或离线对话评测，而不需要改变现有测试布局。

## 使用

项目运行环境：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run unit
runtime\python.exe -m harness run core-host
runtime\python.exe -m harness run legacy-qt-ui
runtime\python.exe -m harness run python-full
runtime\python.exe -m harness run runtime-v2-shell
```

也可以指定报告位置：

```powershell
runtime\python.exe -m harness run smoke --report temp\harness\smoke.json
```

默认报告写入 `temp/harness/`。进程退出码为 `0` 表示全部通过，`1` 表示至少一个 case 失败，`2` 表示调用或清单错误。

`runtime-v2-shell` 会运行 `desktop/frontend` 的完整 Node 测试，以及近期桌面壳改动涉及的角色外观、角色表现、产品窗口、窗口几何和原生交互 Rust 模块测试。该 profile 保持离线，并避开会与正在运行的 Sakura 实例争用共享锁的完整 Rust 生命周期测试。

Python profile 按用途分层：

- `unit`：完整 `tests/unit`，适合 Python 业务代码的常规回归；
- `core-host`：Core Host 单元与真实本地子进程集成测试，不访问公网或真实 Provider；
- `legacy-qt-ui`：完整 `tests/ui`，在 offscreen Qt 平台验证仍受支持的 legacy Qt 回退；
- `python-full`：依次运行 unit、integration 和 legacy Qt UI，适合合并前完整回归。

Harness 只注册可执行行为、协议或生命周期检查。仅依赖源码字符串、函数排列或历史实现 token 的检查不作为 profile 门禁；对应意图应由 Python 行为测试、Node 测试、Rust 测试或独立真实验收覆盖。

## 扩展

在 `suites.json` 的 `cases` 中增加命令，再把 case id 放进相应 `profiles` 即可。命令以 argv 数组执行，不经过 shell；`{python}` 会替换为当前 Python，`{repo}` 会替换为仓库绝对路径。

最小版本刻意保持离线、无第三方依赖，也不会读取 API Key。需要真实模型或网络的评测应建立独立 profile，并显式标注和隔离数据目录。
