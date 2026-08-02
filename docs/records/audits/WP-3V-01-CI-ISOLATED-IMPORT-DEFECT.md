---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 headless oracle 隔离导入缺陷记录

## 事件与影响

2026-08-03，候选 `d156dcf1bb3a65b72614c32dc20721df5a011d83` 的 Runtime v2 platform
foundation run [30759208781](https://github.com/Rvosy/Sakura/actions/runs/30759208781) 在 Windows x64、
macOS arm64 和 Linux x64 三个平台失败。Test workflow 中 Harness、文档、Python Unit 与 UI jobs 同时
通过；失败仅发生在三平台 `Run WP-3V-01 real Assistant vertical slice` 步骤。

三个 job 的终态完全一致：Tauri/Core 场景退出后，staged Runtime Python 启动
`headless_legacy_oracle.py`，首次导入 `app.core.instance` 时抛出：

```text
ModuleNotFoundError: No module named 'app'
```

Windows job 为 `91526622304`，macOS job 为 `91526622290`，Linux job 为 `91526622286`。这不是三个
平台 backend 或 RuntimeLocator 的独立生产缺陷，而是同一个验收器隔离启动缺陷。

## 根因

本地仓库 Runtime 的 Python 搜索路径包含仓库根，因此此前真实 Windows 验收没有暴露问题。CI staged
Runtime 不承诺相同的开发 `.pth` 搜索路径；独立 oracle 以自身脚本目录作为首个导入目录，也没有显式
固定仓库根，导致它依赖本机环境才能导入冻结的生产共享锁和历史 parser。

oracle 的 cwd 虽然是仓库根，但 Python 执行脚本时不以 cwd 作为稳定公共导入契约。通过 workflow 设置
宽泛 `PYTHONPATH` 会把环境偶然性继续留在验收链中，因此不作为修复方案。

## 负责人批准与修复边界

项目负责人在当前开发会话中明确批准：

> 批准实施 WP-3V-01 headless oracle 隔离导入修复并补充三平台 CI 缺陷记录。

修复只允许 oracle 从自身受版本控制的位置解析、验证并固定仓库根，随后导入既有
`app.core.instance` 与 `app.storage.chat_history`；同时增加 `python -I`、清除仓库根搜索项且 cwd 位于
仓库外的独立子进程回归。不得修改生产模块、Runtime 布局、共享锁协议、数据 parser、Harness 或三平台
门禁。

## 状态与后续证据

本记录只保存已发生的失败、批准和修复边界。当前 Work Package 状态仍只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。实现候选须重新通过定向隔离导入测试、
真实 Windows 组合验收、required profiles 和同一 SHA 三平台 workflow；远端结果通过前不标记 WP-3V-01
accepted，也不更新 CAP-004。

## 修复候选本地验证

修复实现提交为 `339a1caa`。oracle 现在从自身文件路径解析并验证仓库根，去除等价旧项后将该根固定为
首个 Python 导入路径；没有读取或信任外部 `PYTHONPATH`，Qt import blocker 和共享锁/历史 parser 边界
保持不变。

本地验证结果：

- 定向 integration：8 passed；新增用例以 `python -I` 启动独立解释器，显式清除仓库根 `sys.path`，
  将 cwd 置于仓库外，再执行 oracle 导入边界，结果为 `repository_imports=true`、`qt_free=true`。
- 真实 Windows 组合：`status=passed`、`provider_requests=4`、`core_kills=1`、`cancel_terminals=1`；
  headless oracle 重获锁并兼容回读，仅允许的 fixture history 变化，敏感证据和进程残留均为 0，临时根
  已删除。
- Harness verify 报告 `temp/harness/20260802T175857Z-WP-3V-01.json`：5/5 required profiles
  通过，24 项自动检查 passed、0 failed，3 项负责人验收 pending，状态为 `manual_pending`。
- docs profile、Harness check、`cargo fmt --check`、Python `py_compile` 和 `git diff --check`：通过。

上述结果关闭本地可复现的隔离导入缺陷，但不替代新提交同 SHA 的原生三平台 CI；该远端证据仍待推送
后取得。
