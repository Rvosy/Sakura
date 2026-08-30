---
kind: spec
status: archived
audience: maintainer
source_of_truth: self
status_source: self
updated: 2026-08-23
---

# WP-3-05：Core 崩溃恢复与 UI 重新水合

## 目标与依赖

本 Work Package 在 WP-3-04 已验收的真实桌宠聊天纵向链上关闭 Core 意外退出后的产品恢复缺口：Python
Core 崩溃、连接丢失或可自动重试的启动失败时，Tauri 桌宠窗口和当前 WebView 必须继续存在；旧
generation 立即失效；Supervisor 完成旧进程树回收并建立新 generation 后，主窗口使用新的完整 Snapshot
与角色表现重新水合。当前执行状态只见
[`work-packages.md`](../../../plans/runtime-v2/pre-simplification-2026-08-23/work-packages-history.md)。

依赖为 WP-3-04 accepted。进程树、串行 Supervisor、generation、Router、聊天唯一终态、最小 Snapshot、
角色资源绑定和真实聊天 UI 均复用已验收契约。本 WP 不改变 Python Assistant、Provider、history、
Envelope、窗口几何或设置 schema。

## 恢复状态机与所有权

```text
ready generation A
-> unexpected_exit | connection_lost | retryable startup failure
-> invalidate A requests/events/handles/resources
-> stop and verify A process tree
-> bounded restart backoff
-> spawn generation B
-> hello + initialize + complete Snapshot B
-> rebind character presentation B
-> rehydrate the same main WebView
-> ready | degraded | failed
```

所有权冻结如下：

| 状态 | 崩溃后的所有者与结果 |
| --- | --- |
| 桌宠窗口、WebView、固定几何 | Tauri 保持原实例；不得 close/recreate/reload，也不得再次播放启动 reveal 或问候 |
| 已完成回复、当前可见字幕、当前会话回复导航 | WebView 已接收的公开内容继续保留；不重新请求、不重复写 history、不重复播放 |
| 正在生成或逐字显示的回复 | 立即成为“本次回复因连接中断而停止”的本地终态；不得跨 generation 恢复模型请求、operation 或 typewriter |
| 输入框未发送草稿与 IME 文本 | 同一 WebView 本地保留；恢复后仍由用户决定是否发送，不提升为 Python 或磁盘真相源 |
| active operation、cancel handle、pending send/response/event | 归旧 generation；崩溃时全部失效，晚到消息不得改变 UI |
| Snapshot、角色资源 URL、设置 transport | 归旧 generation；清空后只接受新 generation 的完整 Snapshot/资源/transport |

WebView 重载、应用退出后恢复草稿或会话回复不属于本 WP；本地保留只覆盖桌宠窗口仍存在的同一运行实例。

## Tauri 生命周期契约

- `unexpected_exit`、`connection_lost` 及既有可自动重试启动失败继续进入同一个串行 `CoreSupervisor`；不得
  从 WebView、Python 或第二个线程创建平行 Core 所有者。
- 观察到 generation A 失败后，Gateway、ChatBridge、settings transport、pending waiter、Snapshot 与
  generation-scoped 角色资源必须在发布新代可用状态前失效。停止与验证 A 的完整进程树完成前不得生成 B。
- 自动恢复沿用既有三次有界 restart budget 与 backoff；成功进入稳定 ready/degraded 后预算按既有语义
  复位。预算耗尽后保持桌宠窗口，显示确定性 failed/retry 状态；手动重试仍走同一 Supervisor。
- `runtime_lifecycle_snapshot` 必须提供单调的 generation number、当前 generation identity、Supervisor
  状态与匹配该 generation 的完整 Snapshot 可用性。不得把旧 Snapshot 与新 Supervisor identity 拼接发布。
- generation B 只有在 hello、initialize 和完整 Snapshot identity/revision 校验通过后才可进入可发送状态。
  角色表现重新绑定失败时保留上一张安全画面并显示可恢复错误，不允许旧资源 URL 获得新代访问权。
- 崩溃、自动恢复、手动重试和 app shutdown 可以竞态；app shutdown 一旦胜出必须取消 backoff、禁止新
  generation，并在既有 deadline 内回收 worker、管道、timer、event projector 与完整进程树。
- 公共 DTO 只增加直接支持恢复状态机所需的稳定字段；不得暴露 PID、credential、绝对路径、Provider
  配置、原始异常、history 或任意进程句柄。

## 主窗口重新水合契约

- lifecycle 从 ready generation A 进入 crashed/restarting 时，主窗口保持 DOM、输入焦点能力、草稿、
  已完成回复、回复导航和当前立绘。活动 thinking/typing 不得伪装为 completed/cancelled，也不得继续动画；
  UI 显示稳定的“连接中断，本次回复已停止”状态并清除取消能力。
- generation 变化必须在前端原子执行：先封闭旧 operation、timer、portrait load 和 async callback，再登记
  新 identity；旧 generation 的 chat terminal、send response、cancel response、Snapshot、appearance
  event、图片 decode 或布局 callback 一律忽略。
- 新 Snapshot ready/degraded 且角色表现 B 完成绑定后，恢复输入发送能力、当前已完成内容和草稿；不得清空
  草稿、覆盖为 greeting、重复插入回复历史或自动重发中断消息。
- 新代仍为 setup_required/failed 时，保留草稿和已完成内容，但发送保持禁止，并展示既有设置/重试入口。
- 用户在恢复期间可以编辑草稿；重新水合提交必须以提交时的最新 textarea 值为准，不得用崩溃瞬间的旧
  副本覆盖用户继续输入的内容。中文/日文 IME composition 不得被 lifecycle poll 强制提交或清空。
- 恢复不得改变 WP-3-03/3-04 冻结的窗口包络、气泡/输入栏自适应、菜单、字幕语言、回复导航和角色视觉
  语义；不得因为连接状态创建第二套全屏错误页或重建窗口。

## 数据、安全与非目标

- 本 WP 不新增正常业务写入。不得删除、截断、修复、重放或追加旧 generation 的 history；不得修改
  `api.yaml`、`ui.json` 或角色包。自动/故障测试只使用隔离 app root 和受控 fixture。
- 草稿与已完成的 UI 表现只保留在当前 WebView 内存，不写入 Python Snapshot、history、设置或新增缓存。
- 不恢复跨 generation 的模型请求、Tool/Operation、TTS、MCP、Memory、截图、主动互动、提醒或未来任务；
  不引入通用 component/patch Snapshot、event replay、持久 session 或 WebView crash recovery。
- 不新增依赖、协议 minor、command 权限、CSP、文件/网络/shell 权限或进程诊断能力。

## 实施白名单与禁止范围

精确机器可读范围见 `harness/tasks/WP-3-05.json`。生产修改限于：

- `desktop/src-tauri/src/` 中明确列名的 Supervisor、Shell lifecycle、Gateway/chat generation barrier、
  composition root 和 WP-3-05 真实恢复验收模块；既有 restart 次数、backoff 和单根所有权不变。
- `desktop/frontend/app.js`、`lifecycle.js`、`chat/**` 及相关测试中的同 WebView 恢复协调、旧代失效、草稿/
  已完成内容保留和中断表现；不得重新设计 DOM 或视觉。
- 隔离故障 fixture、桌面 acceptance、三平台 workflow、规范、记录、userdoc 和 changelog。

明确禁止修改 Python Assistant/Core/Provider/history、legacy Qt、插件、TTS、Tools、Memory、MCP、角色包、
真实 `data/**`、bundled `runtime/**`、第三方代码和依赖 manifest/lockfile。

## 自动验收矩阵

| 门类 | 必测情形 | 核心断言 |
| --- | --- | --- |
| idle crash | ready 后强杀 Core、stdout/pipe 丢失 | 同一窗口/WebView 存活；旧代清理后新代 ready |
| active crash | pending send、thinking、typing、cancel 竞态 | 唯一本地 interrupted 终态；旧 response/event/handle 全部失效 |
| UI ownership | 已完成回复、回复回看、未发送草稿、恢复期间继续输入、IME | 内容不丢、不重复、不自动发送；最新草稿胜出 |
| rehydration | 新 Snapshot 延迟、错代/旧 revision、角色资源冷热缓存/失败 | 只接受新代完整 Snapshot；安全保留画面；ready 后一次性恢复 |
| restart budget | 连续 1 至 4 次失败、backoff 中 retry/shutdown | 预算有界；重复意图合并；耗尽后可见 failed；shutdown 胜出 |
| resource cleanup | Core 根/后代、pipe、waiter、event queue、timer、图片 callback | 旧代归零；无进程、句柄、线程、临时目录或订阅残留 |
| frozen boundary | DPI、固定几何、菜单、字幕、回复导航、设置窗口 | 不重建窗口、不重播 greeting、不改变既有产品语义 |
| security/data | stale credential/path/secret event、真实数据摘要 | fail closed；WebView 无私密字段；受保护数据零变化 |

任务级 required profiles 固定为 `docs`、`smoke`、`core-host`、`runtime-v2-shell`、`python-full`。实现还须
执行 frontend 全量、locked Rust 全量、fmt/diff check，以及同一候选 SHA 的 Windows/macOS/Linux 公共
workflow。自动故障只使用隔离 Runtime/app root，不强杀用户现有 Sakura 或修改真实配置。

## 人工验收与退出条件

Windows 真实 Tauri/WebView2 使用已有开发配置，分别在 idle、真实回复生成中和已完成回复后强制结束
Python Core：桌宠窗口不得消失、闪烁重建或重播问候；活动回复明确中断；已完成回复、当前回复导航和未
发送草稿保留；新 generation ready 后可成功完成下一轮真实聊天。恢复期间继续输入与中文 IME 不得丢失。

连续触发崩溃必须观察到有界 backoff、预算耗尽后的可见失败和同一路径手动重试；退出应用后 Core 根、
后代与 Sakura Runtime v2 相关进程不得残留。公共候选须取得同一 SHA 三平台门，无 P0/P1、旧代回流、
重复终态、凭据泄漏或资源残留。

自动门通过只允许进入 `stabilizing` 并等待负责人验收；Agent 不代填人工结果，也不自行标记
`accepted`。

## 回退

回退前停止新 send，取消或排水当前 operation，退出 Runtime v2 并确认 Core 完整进程树已结束。随后按
独立提交逆序禁用 UI rehydration 协调和新增恢复 publication，恢复 WP-3-04 的既有行为：Core 崩溃仍由
Supervisor 有界重启，但 UI 只显示连接状态并要求用户显式重新开始交互。

回退不得削弱既有 generation/Gateway/进程树隔离，不得恢复旧 operation、删除 history、清空草稿配置或
回退 WP-3-04 已验收的真实聊天链。
