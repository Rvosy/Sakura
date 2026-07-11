# 运行日志单一配置源收敛设计

## 1. 背景

Sakura 的设置界面和 `AppSettingsService` 只读取、保存 `data/config/system.yaml` 的 `debug` 节：控制台开关、正文开关、文件开关、日志档位和舞台调试项都来自这里。

`app/core/runtime_log.py` 却同时读取一个产品从未写入的 `logging` 节，并让它覆盖 `debug`：

- `logging.console_enabled` 覆盖 `debug.enabled`；
- `logging.file_enabled` 覆盖 `debug.file_enabled`；
- `logging.level` 覆盖 `debug.profile`；
- `logging.gui_enabled` 是仅存在于 runtime 的隐藏开关。

这形成两个配置真值源。若旧文件、手工配置或残留包包含 `logging` 节，用户在 UI 中保存 `debug` 后，实际控制台、文件或级别仍可能被隐藏值覆盖。

日志调用侧还保留 `app/core/debug_log.py` 兼容 shim。生产中只有内置 `sakura_mobile` 插件仍导入 `debug_log()`；其余导出仅被 shim 自测使用。`raw_tts_service_log_enabled()` 恒为 True 且零调用，`_close_file_logger_for_tests()` 是零效果测试兼容入口。

## 2. 目标与非目标

### 目标

- `system.yaml:debug` 成为运行日志唯一持久化配置源。
- UI 保存的控制台、文件和日志档位立即对应 runtime 行为，不再被隐藏节覆盖。
- GUI 日志继续保持当前默认开启行为。
- 内置移动端插件直接使用 `runtime_log.log_event()`。
- 删除 `debug_log.py` shim、恒真 TTS 日志开关和测试 no-op 清理入口。
- 保持日志格式、脱敏、轮转、过滤、GUI 窗口和插件可见行为不变。

### 非目标

- 不重命名 UI 中的“调试日志”设置或迁移 `debug` 节到新名字。
- 不改变日志等级定义、事件映射、verbosity 或文件 JSONL 格式。
- 不改变 TTS 子进程原始日志始终落盘的现有策略。
- 不处理 API 配置双路径；它使用独立规格和提交。

## 3. 方案比较

### 方案 A：保留 `debug` 单一源（采用）

删除 `_load_logging_values()` 及所有优先级分支，console/file/level 只读取 `debug`。GUI sink 继续无条件参与既有可见性过滤。

优点：与 UI 和设置服务的真实写入路径一致；无需迁移；删除量最大。缺点：手工写入且从未受 UI 支持的 `logging` 节不再生效。

### 方案 B：迁移到 `logging` 单一源

修改设置服务、UI、默认配置和迁移器，把日志配置全部改写到 `logging`。

优点：命名更通用。缺点：需要新增迁移和双读过渡，短期代码更多，并扩大用户配置变更面。

### 方案 C：保留双源优先级

优点：不改现状。缺点：继续允许隐藏配置覆盖 UI，是本轮需要消除的真实漏洞。

## 4. 单一配置语义

- `console_log_enabled()` 读取 `debug.enabled`，默认 False。
- `file_log_enabled()` 读取 `debug.file_enabled`，默认 True。
- `log_level()` 优先读取 `debug.level`，兼容当前正式字段 `debug.profile` 和旧档位别名。
- `log_body_enabled()` 继续要求控制台开启、trace 档位和 `debug.body_enabled=True`。
- GUI 日志继续执行 `_event_visible(record, sink="gui")`，不再读取不存在于设置模型的隐藏开关。

文件写入、控制台格式化、GUI buffer、事件映射和脱敏逻辑不变。

## 5. 兼容层删除

把 `plugins/sakura_mobile/server.py` 的 `debug_log()` 调用迁移为 `log_event()`，随后删除 `app/core/debug_log.py`。

删除 `runtime_log.raw_tts_service_log_enabled()`。TTS 原始 stdout/stderr 已在真实写入路径中无条件落盘，不需要恒真函数表达旧开关。

删除 `_close_file_logger_for_tests()` 及测试 fixture 中的调用。当前文件日志每次写入都立即关闭，该方法没有状态可清理。

外部插件应通过插件服务和公开日志能力工作，不把 `app.core.debug_log` 作为插件 SDK 合约；仓库文档没有承诺该模块。

## 6. 测试策略

严格 TDD：

1. 先写结构测试，要求 `_load_logging_values` 和隐藏 `gui_log_enabled` 消失；当前实现 RED。
2. 写行为测试证明 `debug` 单独控制 console/file/level，删除测试中对第二配置源的 monkeypatch。
3. 先写结构测试要求 `app/core/debug_log.py`、`raw_tts_service_log_enabled` 和 `_close_file_logger_for_tests` 消失；当前实现 RED。
4. 迁移移动端插件日志调用并删除兼容层。
5. 运行 debug log、移动端插件、TTS、GUI 日志窗口及全量回归。

## 7. 提交与验收

计划提交：

1. `refactor: use one runtime logging config source`
2. `refactor: remove legacy debug log shim`

验收标准：

1. runtime 日志只读取 `system.yaml:debug`。
2. GUI sink 保持默认开启并继续受事件可见性过滤。
3. 移动端插件日志仍进入统一 runtime log。
4. 文件 JSONL、控制台文本、GUI buffer、脱敏和轮转测试通过。
5. TTS 原始服务日志行为不变。
6. 全量测试通过，无新增原生退出警告。
7. 工作树只剩用户 bat，且 `app/`、`plugins/` 删除量大于新增量。
