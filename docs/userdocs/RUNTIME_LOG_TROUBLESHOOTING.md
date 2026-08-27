---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-26
---

# 运行日志与故障排查

Sakura 在 `data/logs/` 下维护两份日志，各自用途不同。

## 运行日志

```text
data/logs/sakura-runtime.log
```

运行日志记录启动、窗口、聊天、Memory、工具、截图、TTS 和插件的状态。格式是一行一个事件：

```text
[15:56:45] [API] 模型请求失败 │ status=400 elapsed_ms=2789ms
```

文件约 10 MiB 时轮转，最多保留 `.1` 到 `.5`。默认 `info` 只写有诊断价值的事件；打开 `debug` 后会增加命令调用和状态轮询记录。

同一轮聊天使用短 `op` 标识。模型调用还会带 `trace` 和 `call`，便于在 Agent Trace 中找到对应请求。运行日志只保存数量、耗时、状态和稳定原因码，不写 API Key、对话正文、Prompt、工具参数或绝对路径。

## Agent Trace

```text
data/logs/sakura-agent-trace.log
```

Agent Trace 默认开启，可以在“设置 → 系统”中关闭。它保存最终发送给模型的 Prompt、动态上下文、Memory、工具调用和回复，适合排查模型行为。

这份文件包含私密正文。Sakura 会移除已知凭据、URL userinfo 和二进制数据，但不会替你隐藏普通聊天、记忆或工具结果。分享前必须自己检查并删去不愿公开的内容。

Trace 按完整 operation 写入。程序在写入途中退出时，下次启动会把残留请求标为 `interrupted`。活动文件接近 32 MiB 时轮转。

## 排查顺序

1. 正常退出 Sakura，再重新启动一次。
2. 在 `sakura-runtime.log` 中找到失败时间附近的事件。
3. 按 `op` 收集同一轮日志，先看稳定原因码。
4. 需要检查模型输入时，确认 Agent Trace 已开启并复现一次；平时不希望保存正文可以关闭它。

常见情况：

- `CORE_CONFIG_SETUP_REQUIRED`：角色或供应商配置尚未完成，打开设置完成首次配置。
- `Prompt 依赖未就绪`：Memory 或 MCP 没有及时就绪，本轮聊天会在缺少该能力的情况下继续。
- `UNKNOWN_REQUEST_ID`：请求所属的 Core 已失效，等待界面连接当前 Core 后重试。
- `MEMORY_NOT_READY`：本地 Memory 仍在初始化或资源不可用，普通聊天可以继续。
- `COMMAND_NOT_FOUND`：MCP 或外部服务命令无法由 bundled Runtime 启动。

单实例锁冲突时，新的进程会直接退出。请回到已经运行的 Sakura 窗口，不要删除锁文件或结束无关 Python 进程。

报告问题时附上版本、系统、复现步骤和经过检查的相关日志片段。不要上传整个 `data/`。
