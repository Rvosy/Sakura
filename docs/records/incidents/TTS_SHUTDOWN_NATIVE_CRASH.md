---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# TTS 退出竞态与 pytest Qt 清理 native crash（已修复）

> 修复日期：2026-06-20。
> 本记录保留原始现象、归因修正和最终验证证据，供后续排查类似 PySide6 退出竞态。

## 原始现象

运行 `tests/ui`（或 `tests/unit tests/ui` 组合）时，Windows bundled runtime 曾随机崩溃：

```text
Windows fatal exception: access violation
```

系统错误码为 `0xC0000005`（进程退出码 `-1073741819`）。崩溃可能发生在测试中主动调用 `processEvents()`、pytest teardown 或解释器退出阶段，不一定伴随断言失败。

正确的本地复现命令应使用 `--ignore` 排除缺少 `qtbot` 的 history 分片：

```powershell
./runtime/python.exe -m pytest tests/ui -q -p no:warnings `
  --ignore=tests/ui/test_history_window.py
```

原记录使用的 `--deselect tests/ui/test_history_window.py` 在当前 pytest 中不会排除整份文件。

## 历史证据

将工作树回到播放端点拆分前的 `f6c70d4` 附近后，4 次 UI 测试中有 2 次发生相同 access violation，说明问题早于 issue #94 第 3 阶段存在，并非播放端点拆分本身引入。

第 3 阶段另有一个独立回归：`TTSSynthesisSink` 要求 `fail_audio_request` / `skip_audio_request`，但 `TTSPlaybackEndpoint` 仅保留了下划线版本，后台失败和跳过路径会抛出 `AttributeError`。本次一并修复。

## 最终归因

原记录把崩溃归因于 TTS daemon 线程向已析构 QObject emit。代码检查确认这条链路确实不安全，但完成 TTS 防护后，10 轮 UI 压测仍有 2 轮在普通窗口删除阶段崩溃，因此它不是唯一根因。

最终确认有两层问题：

1. `TTSSynthesisQueue` 先启动线程、再登记 `ThreadResource`，关闭并发时存在漏管窗口。
2. Provider 先清理 Qt 播放端点、再 `stop_all()`，在飞合成线程仍可能向端点投递结果。
3. 合成结果入口没有检查 closed 和 `shiboken6.isValid(endpoint)`；失败清理还会从 daemon 线程直接调用 `QTimer.singleShot`。
4. 测试创建的真实 Provider 没有统一 teardown，后台线程与 QApplication 清理顺序不确定。
5. pytest 全局 Qt 清理夹具对每个测试执行 `deleteLater()` 后，多轮调用 `processEvents()` / `sendPostedEvents()`。最终 native 栈直接落在 `_drain_qt_events -> app.processEvents()`；这会执行跨测试残留的普通 queued event，并与对象延迟删除形成竞态。

## 修复措施

### TTS 产品链路

- 合成线程先登记到 `ThreadResource`，再调用 `start()`，消除漏管窗口。
- Provider 关闭改为幂等两阶段流程：设置 closed 并封闭结果入口，清空待处理请求，调用 `stop_all()` 收敛线程/进程，最后清理播放端点。
- `deliver_audio`、`deliver_prepared`、`fail_audio_request`、`skip_audio_request` 和 `schedule_cleanup` 统一检查 closed、端点接收状态及 `shiboken6.isValid()`。
- 合成失败清理通过 queued signal 返回 UI 线程；关闭后迟到音频同步尽力删除，预生成句柄标记失败。
- 关闭后可能到达的 slot 不再执行 UI 回调或向外发送错误信号。

### pytest 清理链路

- 自动夹具强引用每个测试创建的真实 TTS Provider，并保证 Provider teardown 先于 Qt 对象清理。
- 顶层窗口仍会先停止可发现的 QThread，再执行容错 `close()` 和 `deleteLater()`。
- 全局夹具不再调用 `processEvents()`；只调用一次 `sendPostedEvents(..., DeferredDelete)` 处理延迟删除，避免顺带执行普通 queued event。
- 精简测试窗口的 `closeEvent` 依赖未初始化字段时，夹具容错该异常并继续安排删除。
- 保留 CI 现有的通用 Qt teardown 兜底；本修复不假设所有未来 Qt native 崩溃都属于 TTS。

## 验证结果

最终代码在 Windows bundled runtime 中完成：

- TTS + ResourceManager 针对性测试：`89 passed`。
- 完整单元测试曾完成 `711 passed, 3 skipped`。
- unit、integration、除 history 外的 UI 组合：`1132 passed, 3 skipped, 1 deselected`，退出码 0；被排除的 100ms 时限测试单独运行 `1 passed`。
- 原 UI 概率复现集合连续运行 10 个独立进程：每轮 `283 passed`，10/10 退出码均为 0，未出现 `0xC0000005`。
- 高风险 `backchannel + bubble + pet_window` 组合连续 5 轮：每轮 `250 passed`，退出码均为 0。
- 新增回归测试覆盖线程登记顺序、关闭顺序与幂等性、公开失败/跳过协议、关闭后迟到结果，以及底层 QObject 经 `shiboken6.delete()` 失效后的投递。

组合套件中的 `test_thread_group_stop_uses_one_deadline_and_lingers` 在大量压力日志后曾以 `0.265s` / `0.297s` 超出 `<0.25s` 阈值，单独重跑通过；这是 Windows 调度与日志 I/O 相关的计时抖动，不涉及本次行为修改。

本地 runtime 未安装 `pytest-qt`，因此 `tests/ui/test_history_window.py` 的 5 个 `qtbot` 用例未运行；GitHub CI 会安装开发依赖并覆盖该分片。

测试进程仍可能在 pytest 自身清理 `%TEMP%/pytest-of-LBW/pytest-current` 时输出既有 `WinError 5`，但测试和进程退出码为 0；该权限提示与 Qt native access violation 无关。
