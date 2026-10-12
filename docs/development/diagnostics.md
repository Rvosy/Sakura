# 诊断接入

记录范围、步骤日志、原始错误、Agent Trace、远程报告及身份关联见[诊断规范](../specs/diagnostics.md)。本页只说明接入入口与代码示例。

## 复用统一入口

| 来源 | 入口 |
|---|---|
| Rust | desktop/src-tauri/src/runtime_log.rs 中的 RuntimeLogService |
| Python Core | app.core.runtime_log，经现有 stderr bridge 交给 Shell |
| WebView | 既有 runtime diagnostics 入口，将原异常交给诊断边界 |
| 插件 | context.get("sakura.host.logging") |

插件在 Manifest 的 requires 声明 sakura.host.logging。接口为 debug/info/warning/error(message, *, fields=None)，不支持标准 Python logger 的位置格式化、exc_info 或 exception()。

```python
logger = context.get("sakura.host.logging")
logger.info("开始读取模型", fields={"stage": "load", "model": model_name})
try:
    model = load_model()
except Exception:
    logger.error("模型读取失败", fields={"stage": "load"})
    raise
logger.info("模型已就绪", fields={"stage": "load"})
```

示例中的 load_model 和 model_name 由插件提供。warning/error 在异常处理块内自动附加当前异常的原文、原因链和栈，因此应在实际捕获点调用。

## 保留跨边界的异常

后台任务需要把异常转成结果时，使用 context.exception_diagnostics(error, secrets=...) 保留诊断。语音 Provider 可复用 sakura_provider_errors.provider_failure，将错误码、原始原因和诊断一起交给 Hub。

前端将原始 Error 或已有诊断载体传到统一错误入口，不先转成一条通用字符串。界面摘要、可复制详情和运行日志复用同一份原因；恢复错误作为附加信息，不能替换原错误。

同一操作使用已有 operationId，模型调用沿用 trace 与调用序号。添加诊断字段前确认它确实帮助定位步骤，不另建身份、日志文件或上传路径。

## 验证真实失败链

新增诊断沿异常产生、跨进程传递、界面详情、文件及需要的远程存储验证同一原因仍可定位，同时确认具体凭据已清洗。

使用隔离数据与测试接收端。业务成功、失败或取消由原操作决定；日志接收、写入和上传失败不得触发原业务重试。服务端模型与导出操作见[服务运维](services.md)。
