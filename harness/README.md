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
```

也可以指定报告位置：

```powershell
runtime\python.exe -m harness run smoke --report temp\harness\smoke.json
```

默认报告写入 `temp/harness/`。进程退出码为 `0` 表示全部通过，`1` 表示至少一个 case 失败，`2` 表示调用或清单错误。

## 扩展

在 `suites.json` 的 `cases` 中增加命令，再把 case id 放进相应 `profiles` 即可。命令以 argv 数组执行，不经过 shell；`{python}` 会替换为当前 Python，`{repo}` 会替换为仓库绝对路径。

最小版本刻意保持离线、无第三方依赖，也不会读取 API Key。需要真实模型或网络的评测应建立独立 profile，并显式标注和隔离数据目录。
