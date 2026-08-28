---
kind: adr
status: superseded
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-13
superseded_by: 0037-replaceable-default-plugins-and-isolated-python-runtimes
---

# ADR-0016：Runtime v2 使用 generation 私有插件 worker

> 已由 ADR-0037 的逐插件进程与独立 dependency root 取代。本文件只保留 cutover 前的历史取舍。

## 背景

Legacy 插件管理器在桌面 Python 进程内发现并导入插件，允许插件注册工具、prompt patch、动态 context、
事件 hook 和声明式设置。插件不是安全沙箱；初始化、hook、设置 action 或工具 handler 可执行任意 Python，
占用 GIL、创建线程或子进程并访问插件私有数据。把这条调用链原样接入 Runtime v2 Core，会让非协作插件
阻塞 `system.health`、`system.shutdown`、聊天取消和 generation 回收。

ADR-0001 要求全部后代受唯一桌面生命周期根监管；ADR-0002 要求真实消费者对不可终止或可能长期占用
GIL 的工作选择可终止 worker process，或证明其他隔离足够。WP-4-04 是插件的首个 Runtime v2 真实
消费者，因此必须在实现前冻结该边界。

## 候选方案

1. 在 Core 主解释器内复用 `PluginManager`。改动最少，但线程和 asyncio 都不能可靠抢占持有 GIL 或阻塞
   原生调用的插件，无法满足 control/shutdown deadline。
2. 每个插件一个进程。隔离最强，但会立刻引入多进程调度、逐插件协议和大量生命周期 owner，超过当前
   两个内置插件和既有 API v2 的迁移需要。
3. 每个 Core generation 一个插件 worker，由 Core 通过私有、有界 RPC 使用。
4. 建立跨 generation 常驻插件服务。可减少重载，但会形成第二生命周期根，并使代码、配置和数据 owner
   跨代复用。

## 决策

采用方案 3。

- 每个 Core generation 最多创建一个私有插件 worker。它由 Assistant Adapter/插件 facade 持有，继承
  Rust Supervisor 的受控进程树，不可独立寻址、自行重启、breakaway 或跨 generation 复用。
- Core 主解释器不导入插件实现。worker 负责发现、manifest/API/permission 校验、导入、初始化、回调和
  逆序 shutdown；Core 只保存经严格校验的公开 descriptor 和 opaque contribution ID。
- 私有协议只开放 load/status、tool call、prompt/context、受控 event、settings get/save/action 和 close。
  请求、响应、队列、字符串、schema、context、事件和设置值均有明确数量、深度、编码大小及 deadline；
  禁止任意反射、模块调用、文件 RPC、pickle 或异常对象穿透。
- Worker 异常、超时或退出使插件域进入稳定 degraded 状态，当前调用失败并注销其 contributions；Core
  health、聊天、Memory、内置 Tools、MCP、设置关闭和应用退出继续可用。重建只能随受控 Core generation
  重建，不能由插件自行拉起。
- 插件工具继续注册到 WP-4-02/03 的同一 `ToolRegistry`，执行前重验 generation 和 contribution identity，
  有副作用工具复用 Action ID 原生确认。插件 prompt/context 保持不可信数据语义，不能越权注入 host
  指令或完整 prompt。
- 事件由 Core 明确发起，worker 不获得通用 IPC emitter。事件 handler 和设置 action 具有各自 deadline；
  迟到响应、重复 response 和旧 generation identity 一律丢弃。
- `plugins.yaml` 的启停/优先级和 `data/plugins/<id>/` 私有数据仍由 Python 插件域解释。Rust/WebView 只见
  脱敏 DTO；路径、entry、异常正文、token、插件私有设置和工具参数/结果不得进入 Snapshot 或日志。
- 正常关闭先停止接收新调用、使贡献失效、逆序调用插件 shutdown 并排水 worker；超时后只终止当前
  generation 明确拥有的 worker/后代。不得扫描或结束其他 Python/浏览器进程。
- 这不是安全沙箱。插件与 worker 仍拥有当前账户权限；用户必须只安装可信插件。进程边界用于可终止性、
  故障隔离和生命周期所有权，不宣称阻止恶意代码访问文件或网络。

## 范围收敛

WP-4-04 只迁移 tool、prompt patch、context provider、受控事件、插件启停和声明式设置/action。现有
renderer、Qt `chat_ui_widget`/`tools_tab` 不能序列化为 WebView UI，需由对应产品消费者另行设计；
Playwright 浏览器和 Sakura Mobile 的真实运行态分别由 WP-5-05 承担，TTS/截图/角色 Studio 也不由本
ADR 提前开放。不得为了演示 worker 修改顶层 `plugins/**` 或真实 `data/**`。

## 后果与回退

收益是插件阻塞或崩溃不再冻结 Core control 面，且全部插件后代随 generation 统一回收。代价是贡献必须
序列化为稳定 descriptor，调用多一次本机 IPC；不能穿过边界的 UI/renderer 对象不再假装已迁移。

回退时先关闭当前 generation 并确认 worker、线程、pipe、子进程和文件句柄归零，再逆序回退插件 facade、
私有协议和设置接线；不得删除、改写或迁移 `plugins.yaml`、插件安装目录或插件私有数据。若未来需要逐插件
OS sandbox、跨 generation 常驻服务或远程插件，应新增 ADR 替代本决策。
