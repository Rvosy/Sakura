---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
---

# WP-3-06：legacy Qt 与 Tauri v2 双向数据兼容门禁

## 目标与依赖

本 Work Package 在 WP-3-05 验收后关闭 Phase 3 的数据回退风险：使用 WP-0-02 的脱敏数据集，通过真实
legacy Qt 入口、真实 Tauri Shell、bundled Python Core 和同一共享应用锁完成
`legacy Qt → Tauri v2 → legacy Qt` 往返。Tauri dogfooding 后，legacy Qt 必须仍能启动并读取角色、
配置与聊天历史；未迁移的 Memory、插件、任务、提醒和用户资源必须保持原样。当前执行状态只见
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。

依赖为 WP-3-05 accepted，并复用 WP-0-02 数据矩阵、ADR-0003、WP-1P-03 共享锁、WP-3S-01 设置仓库、
WP-3-04 真实聊天和 WP-3-05 完整退出/恢复契约。本 WP 不扩大产品能力，不把 Memory、Tools、MCP、TTS、
插件、历史分页或角色切换提前接入 Runtime v2。

## 数据权限与版本门

测试数据只能从 `tests/fixtures/runtime_v2/wp_0_02/` 复制到系统临时目录下专属于 WP-3-06 的隔离应用根；
不得让真实 Qt、Core 或 Tauri 指向仓库的 `data/**`、`characters/**`。夹具源本身只读，运行产物写入已忽略
的临时目录。

全局数据版本锚点仍是 `data/config/system_config.yaml::config_version=4`：

| 数据状态 | Runtime v2 行为 |
| --- | --- |
| 当前版本且目标结构有效 | 允许读取共享数据，并只执行下表批准的 Phase 1–3 写入 |
| 旧版本、缺少版本 | `diagnostics_read_only`；提示先由同版本 legacy Qt 迁移，不运行 v2 共享写 |
| 未来版本 | `diagnostics_read_only`；不降级、不覆盖、不生成默认共享配置 |
| YAML/JSON/JSONL 损坏或必要资源缺失 | `diagnostics_read_only`；保留原 bytes，不用默认值修复 |
| v2 私有文件未来 schema | 仅禁用/只读对应 v2 域，不回写任何 legacy 共享文件 |

只要数据集未通过版本与结构门，Core 就不得建立可写聊天会话。WebView 只能获得稳定错误码和用户可操作的
诊断/退出路径，不得收到绝对路径、原始异常、凭据或文件内容。

## Phase 1–3 写入白名单

| 域 | 允许结果 | 强制约束 |
| --- | --- | --- |
| 聊天历史 | Python `ChatHistoryStore` 向当前角色 JSONL 追加本次真实 user/assistant turn | 沿用 legacy 文件名和字段；Qt parser 可读；不修复、截断、迁移、轮转或重放旧工件 |
| Provider/模型配置 | WP-3S-01 Python repository 更新已批准字段 | 原子保存；未知字段、非目标域和未修改 secret bytes 保留；失败保持旧文件 |
| Runtime v2 外观/聊天表现配置 | 只写 `data/runtime_v2/config/ui.json` 的已批准 feature | `schema_version=1`；同目录原子替换；legacy Qt 忽略该命名空间 |

除上述目标外，兼容运行前后必须保持文件集合与 SHA-256 不变，尤其包括：角色包、`characters.yaml`、
`system_config.yaml`、MCP/插件配置、Memory/Qdrant/SQLite、整理状态、任务、提醒、notes、visual/runtime event、
Studio、TTS、migration backup、`.bak`、旧单文件历史、archive/corrupt 工件和历史锁文件。日志、验收 marker
及临时文件只允许位于隔离输出目录，不纳入共享数据集。

## 真实双入口序列

Windows 自动/人工验收必须以真实进程执行以下单一序列：

```text
copy sanitized WP-0-02 fixture to isolated app root
-> launch legacy_qt_main.py against that root
-> acquire the production shared desktop lock
-> legacy Qt domain code reads the dataset and appends a tagged fixture turn
-> drain writers and exit, then release the lock
-> launch the real debug Tauri executable against the same root
-> acquire the same production lock before Core or any data access
-> bundled Python Core initializes and completes one deterministic local chat turn
-> optionally save one approved provider/model and one v2 UI setting change
-> stop new writes, shut down Core/process tree, exit Tauri, release the lock
-> launch legacy_qt_main.py again against the same root
-> reacquire the same lock and parse the complete compatible result
-> compare the final manifest with the declared write allowlist
```

验收模式必须同时满足：显式环境开关、debug/test 构建或测试入口、绝对且 canonical 的系统临时目录、固定
WP-3-06 目录名和脱敏 fixture marker。任一条件缺失时 fail closed。release 正常入口不得接受任意 app-root
覆盖；验收参数、绝对路径和 fixture 内容不得投影到 WebView 或普通日志。

这不是静态 parser 测试：两个 Python 阶段必须经过真实 `legacy_qt_main.py` 进程及生产锁；中间阶段必须
经过真实 Tauri 可执行文件、生产锁、Shell lifecycle 和 bundled Core。允许验收模式自动关闭窗口和使用
本机确定性 loopback Provider，但不得直接调用 Core 函数冒充 Tauri 进程。

## 锁、退出与故障契约

- Qt 与 Tauri 竞争既有稳定 identity；任一入口持锁时，另一入口必须在 Core、migration、配置默认值、
  日志和共享写之前返回 `already_running`。不提供强制接管，不删除 `data/sakura.lock` 或 Qdrant `.lock`。
- 正常退出只在窗口、Core、写入任务、管道、reader/writer、线程和进程树归零后释放共享锁；退出后另一
  入口必须立即可重获。
- 强杀由操作系统释放生产锁；重启不得根据 marker、PID 或旧 lock 文件猜测持有者。
- 备份创建、临时写入、flush/fsync、校验和原子替换失败时保留原文件；孤立 temp 不得被当成新真相源。
- 聊天 JSONL 的进程级异常中断只允许留下完整旧行或一条完整新行，不接受半行成为可读记录；不在本 WP
  自动截断或修复已有损坏尾行。
- 未来/损坏 schema、目标路径符号链接/越界、夹具 marker 缺失、真实仓库路径或非隔离临时根必须拒绝
  共享写，并证明全清单零变化。

## 实施边界

精确机器可读范围见 `harness/tasks/WP-3-06.json`。允许的产品修改仅限：

- Python Core 的全局版本/结构写入门、现有聊天历史兼容追加，以及现有 Provider/模型仓库的失败安全；
- Rust/Tauri 对隔离 app root 的验收专用定位、共享锁和现有生命周期编排；
- `legacy_qt_main.py` 中同样 fail-closed 的验收入口，不改变正常 legacy Qt 启动、迁移或业务语义；
- 专用 Windows 双入口验收脚本、跨平台锁/数据单元测试、隔离 fixture 扩展和治理文档。

明确禁止修改或运行真实 `data/**`、`characters/**`、`third_party/**`，禁止新增依赖、破坏性 schema migration、
历史修复器、Rust 直接写共享 history、Memory/Qdrant/SQLite 访问、插件/TTS/MCP/Tools 能力、默认配置 backfill、
安装/更新逻辑和通用测试 app-root 后门。

## 自动验收矩阵

| 门类 | 必测场景 | 核心断言 |
| --- | --- | --- |
| 双向往返 | Qt 写入 → Tauri 真实聊天/设置 → Qt 回读 | 同一锁、真实进程、Qt parser 可读、只出现声明的写入 |
| 互斥 | Qt 持锁启动 Tauri；Tauri 持锁启动 Qt | 失败入口不启动 Core、不 migration、不创建日志/默认配置、不写数据 |
| 版本/结构 | current、old、missing、future、坏 YAML/JSON/JSONL、缺角色资源 | 仅 current+valid 可写；其余稳定只读诊断且 bytes 不变 |
| 保存故障 | backup/temp/flush/replace/中断 | 原文件可读且 hash 不变；temp 不晋升；重试不形成半提交 |
| 私有命名空间 | v2 UI 正常/未来 schema、未知字段 | Qt 行为不变；未来域局部禁用；共享 legacy 文件不回写 |
| 清单边界 | 角色、配置、历史、Memory、插件和外部资源前后 manifest | 只有批准 history/API/UI 路径变化；凭据与私密内容不进入报告 |
| 退出/强杀 | 正常退出、活动写入退出、Core/Tauri/Qt 强杀 | 完整进程与句柄归零；锁可立即重获；不误杀无关进程 |
| 平台门 | Windows/macOS/Linux 同一候选的公共锁和数据语义 | 平台 backend 通过；真实 Qt/Tauri 往返至少在 Windows 完成 |

required profiles 固定为 `docs`、`smoke`、`core-host`、`runtime-v2-shell`、`python-full`。还须执行专用
Python integration、locked Rust 全量、格式/差异检查和同一候选 SHA 三平台 workflow。报告只记录脱敏
fixture 的相对路径、允许变化分类、计数与摘要 hash，不记录 secret bytes 或真实用户数据清单。

## 人工验收与退出条件

项目负责人在 Windows 使用隔离脱敏数据执行真实 Qt → Tauri → Qt 序列，确认两种 UI 均实际出现并有界
退出；Tauri 完成一轮基础聊天后，legacy Qt 能读取两端新增历史，当前角色、现有配置和 v2 专属配置的
忽略行为正确。双向同时启动只允许持锁方继续。

负责人还须确认未来/损坏 schema 的只读诊断、保存故障的旧文件保留、正常/强杀后的锁重获和零相关进程
残留，并审查同一候选 SHA 的三平台证据、最终脱敏 manifest 与独立回退边界。自动门通过只允许进入
`stabilizing`；Agent 不代填人工结果，不自行标记 `accepted`。

## 回退

回退前停止新聊天和设置保存，退出 Runtime v2 并确认 Core/后代/写入任务归零。随后禁用 Tauri 对共享
业务数据的写入并退回只读使用，逆序回退 WP-3-06 的版本门补强与验收接线；保留 WP-3-05 及以前已验收
的 UI、Supervisor、共享锁、Provider/模型设置和 Python `ChatHistoryStore`。

回退不得删除、恢复、重命名或“修复”任何用户文件，不得回退 legacy Qt 入口或共享锁，也不得用旧
generation 重放聊天。若已产生兼容 JSONL 追加，它属于用户历史，不随代码回退删除。
