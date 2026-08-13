---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-4-04 Python 插件能力等价自动验证记录

## 候选与环境

2026-08-12，在 macOS arm64、分支 `refactor/tauri-runtime-v2` 上验证产品候选
`73c501e808ab9c493d40264e3a916db20d2d0a66`。该分叉当时的固定 base 为
`80764fa55d9dbb69e44f4bd5f634093f44d79010`；整合 WP-4L-02、WP-4-01B 与 WP-3-03B 后，当前审计 base
前移为 `7884e6d41bf2c29ce1ce7472f6936fa3ed29763c`。Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只写入本机已经执行的自动证据，
不预填 Windows 实机操作、Linux x64、Windows x64 或同一 SHA 三平台 CI 结果，也不把 WP 标记为
`accepted`。

候选按照 [normative Spec](../../specs/runtime-v2/WP-4-04-python-plugin-capability-parity.md) 和
[ADR-0016](../../adr/0016-runtime-v2-generation-private-plugin-worker.md) 增加 generation 私有插件 worker。
Core 主解释器不导入插件实现；worker 通过有界 JSON RPC 提供 tool、prompt patch、context provider、
`app/message/tool` 摘要事件和声明式设置。Rust gateway 与设置 WebView 严格校验脱敏 DTO，插件启停保存
后受控重启 Core 并原位重绑 generation。

## 已执行自动结果

- `runtime/bin/python3.12 -m harness check WP-4-04`：当前任务、accepted 依赖、固定 base、allowlist、全局
  保护边界、测试删除、task 修订和 activation 关闭检查全部通过；越界、保护和 owner-review 文件均为
  0。
- required profiles 的逐项预跑全部通过：`docs` 2/2、`smoke` 3/3、`core-host` 4/4、
  `runtime-v2-shell` 6/6、`journey-tools` 3/3、`journey-plugins` 3/3。
- 最新插件 journey：Python 58 passed，覆盖健康/损坏插件、Qt-free 导入、worker 加载/关闭、工具、
  prompt/context/event、设置/action、超时终止和 Core 纵向链；Rust 2 passed，覆盖 capability 协商与严格
  设置 DTO；前端 4 passed，覆盖私有字段拒绝、保存重绑、失败草稿和 action 边界。
- 额外宽回归：插件相关 Python 86 passed；完整 Runtime v2 前端 149 passed；完整 Rust 串行测试
  282 passed、3 ignored。`cargo fmt`、Python `py_compile` 和 `git diff --check` 通过。
- Windows 验收脚本 `desktop/tests/windows_wp_4_04_plugin_acceptance.ps1` 已加入候选，覆盖隔离 root、健康/
  损坏插件、prompt/context/event、原生工具允许/拒绝、超时失效、设置 action、禁用/启用 generation
  重建、Core 强杀恢复、日志 sentinel、真实 `data/**`/`plugins/**` 零变化和退出零残留；本机没有
  PowerShell/Windows 环境，因此未把脚本存在或静态检查冒充实机执行。
- 首次干净候选 verify 报告 `temp/harness/20260812T125617.938764Z-WP-4-04.json` 在 Core Host 单测中
  暴露了测试同步竞态：100ms 的通用 callback deadline 可能先让并行 `app.start` 结束 worker，测试随后
  收到 `PLUGIN_WORKER_EOF` 而不是其硬编码的工具 timeout。产品仍按规范 fail closed；后续修订增加异步
  contribution 绑定完成同步点，并让故障测试在绑定完成后以 1 秒 deadline 单独触发 30 秒工具 hang。
  本记录保留该失败事实，最终结论只以后续干净候选 verify 报告为准。

## 未执行证据与人工边界

Windows x64 实机清单、Linux x64 自动门、Windows x64 自动门以及同一候选 SHA 的三平台 Runtime v2 CI
尚未在本记录中声称完成。Windows 实机需运行
`desktop/tests/windows_wp_4_04_plugin_acceptance.ps1` 并把操作结果交给项目负责人；脚本本身不会自验收
Work Package。

本记录提交后还必须对干净候选运行 `runtime/bin/python3.12 -m harness verify WP-4-04`。自动门全绿只表示
进入 `manual_pending`，仍需上述平台证据和项目负责人明确验收；Agent 不填写人工结果，也不得把
WP-4-04 标记为 `accepted`。

## 2026-08-14 恢复记录

项目负责人明确要求搁置 WP-3-03D 界面优化并继续推进主线。已整合的 WP-4-04 候选、测试和既有自动
证据未回滚；固定 `base_ref` 按负责人授权单向前移到提交
`e5b57f64591c9605fe74ec2fbb05c93db9289a5c`，WP-4-04 恢复为唯一 active。该任务契约修订须先提交并
通过 `harness check WP-4-04` 审计；本记录不构成人工验收或 `accepted`。

恢复提交 `7b485e9fdd12af9e02d27c511ab7f55dff0d049a` 后，本机 Windows x64 执行：

- `runtime\python.exe -m harness check WP-4-04`：通过；固定 base、accepted 依赖、allowlist、全局保护
  边界、测试删除和 task 修订全部通过，已提交 `base_ref` 修订被审计接受；
- `runtime\python.exe -m harness verify WP-4-04`：报告
  `temp/harness/20260813T181515.724018Z-WP-4-04.json`，21/21 自动 case 通过，0 failed、0 blocked，状态
  `manual_pending`；其中插件 Python journey 58 passed、Rust journey 2 passed、前端 journey 4 passed；
- required profiles `docs`、`smoke`、`core-host`、`runtime-v2-shell`、`journey-tools`、
  `journey-plugins` 全部通过。

该结果关闭本地自动门并支持进入 `stabilizing`，不替代 Spec 要求的 Windows 隔离 root 实机清单、三平台
证据或项目负责人明确验收。上述人工证据完成前不得标记 `accepted` 或开始 WP-4-05。

## 2026-08-14 实机验收缺陷

项目负责人提供的设置页截图显示 Sakura Mobile 的“刷新状态”动作返回
`SETTINGS_VALUES_INVALID|plugins.manage|插件设置动作失败`。同一轮提供的手机页面截图可打开网页壳，但
正文显示“移动端聊天服务尚未就绪”。源码与调用链复核确认是两项独立缺陷：

- 设置页把 `running`、`local_url`、`lan_urls`、`error` 等只读展示值与可编辑字段一起传给 save/action，
  worker 又按既有规则拒绝所有只读输入，因此真实插件形状没有被 fixture 覆盖；
- Runtime v2 私有 worker 创建了未注入任何后端的 `PluginMobileService`，使声明 `mobile_chat` 的插件看似
  `ready/运行中`，但所有聊天调用必然失败。WP-4-04 的既有 Spec 和 userdoc 已明确移动桥接延期到
  WP-5-05，当前正确行为应为 `unavailable/degraded`，而不是启动空壳服务。

该证据使此前 `manual_pending` 候选失效，WP-4-04 退回 `active`。纠正实现不得修改受保护的顶层
`plugins/**`；需在 WebView、Core worker 与宿主服务注入边界三层 fail closed，并补充真实插件同形态的
readonly/action 与 `mobile_chat` 回归。新自动门和负责人复验完成前不得恢复 `stabilizing`。

纠正提交 `3120477575bfafdc46e39bdd4708571127d241ce` 的本机 Windows x64 自动结果：

- `journey-plugins` 3/3 通过：Python 60 passed、Rust 2 passed、前端 5 passed；
- 完整 Runtime v2 前端 157 passed；额外设置页/插件边界 41 passed；插件 Python 定向 59 passed；
- `docs` 2/2、`harness check WP-4-04` 和 `git diff --check` 通过；
- 最终 `harness verify WP-4-04` 报告
  `temp/harness/20260813T183843.465618Z-WP-4-04.json`：21/21 自动 case 通过，0 failed、0 blocked，
  状态 `manual_pending`。

WP-4-04 据此恢复 `stabilizing`，等待项目负责人确认设置 action 不再报错，且 Sakura Mobile 在 Runtime v2
明确显示 `degraded/HOST_SERVICE_UNAVAILABLE`、不再启动不可用网页入口。本记录不填写人工结果。

## 2026-08-14 浏览器插件与确认边界缺陷

项目负责人要求检查 `sakura-runtime.log`、`sakura-agent-trace.log` 和 `N.A.V.I..jsonl`。真实链显示模型
正确调用 `playwright_navigate(https://www.bilibili.com)`，Core 却先创建二次确认；允许后插件在约 1.3 秒
失败，日志只保留 `PLUGIN_CALLBACK_FAILED`，随后同一 `tool_call_id` 的工具结果续传被 Gemini 3.1 端点以
HTTP 400 拒绝，聊天历史只留下提前生成的“正在确认系统资源”文本，没有真实失败后的最终回复。

同机随后完成两个只读诊断：直接调用 Playwright 可见 Edge 导航成功；通过真实 `PluginWorkerClient`
调用同一插件贡献也成功。由此不能把原故障归因于浏览器未安装、网络、URL 校验或 worker 基本链路，原始
异常又已被泛化码不可逆丢失。修复方向是：当前助手工具统一直执行；插件回调输出严格脱敏的稳定分类码；
Provider 原始工具调用扩展元数据保留到续传，避免丢失 Gemini thought signature。底层 Action ID/原生
确认代码保留但不由助手激活，未来 Agent 插件权限模型另行设计。

范围修订提交 `5db9d9eb` 后，`harness check WP-4-04` 通过并审计接受 `allowed_paths` 修订。实现中的首轮
定向结果为：Python 142 passed；`journey-tools` 3/3 通过，其中 Python 22 passed、Rust 7 passed、前端
4 passed。本段只记录已经发生的自动结果；最终 `verify` 结果在完成全部回归后追加。

同一实现工作树继续取得以下本机 Windows x64 结果：

- 真实私有 `PluginWorkerClient` 绑定仓库 Playwright 插件后，`playwright_navigate` descriptor 的确认标记
  被助手边界停用，直接导航 `https://www.bilibili.com` 成功，返回最终 URL 与标题且没有 Pending Action；
- 插件 journey 3/3 通过：Python 62 passed、Rust 2 passed、前端 5 passed；
- 完整 Runtime v2 前端 158 passed；Core Host 4/4 profile 通过，其中 Python 252 passed；Smoke 3/3、
  Runtime v2 Shell 6/6、文档 2/2 全部通过；
- 插件错误回归证明 worker 只返回 `PLUGIN_CALLBACK_IO_FAILED`，ToolResult 保留相同稳定 reason code，注入
  的私有路径和异常正文不进入公开结果；API 回归证明 Provider `extra_content.google.thought_signature`
  保留在助手工具调用续传消息中。

上述结果仍不构成项目负责人实机验收。最终 `harness verify WP-4-04` 报告在产品提交后追加。
