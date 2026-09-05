---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-3S-01：供应商与模型设置纵向链

> 计划中的 [WP-4-07R](WP-4-07R-typed-timeline-adaptive-context.md) accepted 后，聊天模型槽会增加可选
> `context_window_tokens` 供自适应上下文预算使用。在此之前，下述 Provider/模型字段仍是当前 accepted
> 写入边界。

> 规范来源：`settings-incremental-migration.md` 第 6 节、ADR-0001/0002/0007/0035
> 当前状态只以 Work Package 总表为准

## 设置范围

Runtime v2 canonical 设置页完成 Provider 公开读取、
凭据动作、模型目录、聊天/视觉模型槽、原子保存、同 generation 热应用和有界网络探测的完整闭环。

生产写入仅允许 `user_root/config/api.yaml` 当前 schema 的以下字段：

- `api_profiles[].{id,alias,base_url,api_key,models[].name}`；
- `model_slots.chat` 与 `model_slots.vision_chat`；
- `llm.{base_url,api_key,model}` 当前聊天槽投影，以及
  `llm.{timeout_seconds,temperature,top_p,max_tokens}` 旧页面已支持的生成参数。

`memory_curation` 模型槽及 TTS/MCP/插件等非目标字段必须逐字节语义保留，不由本 WP 前端开放。写入前必须
确认 `system_config.yaml.config_version == 1`；任何其他版本、缺失、类型错误或损坏数据都明确拒绝。
读取不得触发迁移，响应只暴露 `configured`，不返回已保存密钥。

## 契约

- capability schema v1 以 section + feature 表达 `available/read_only/unavailable`；其他 schema 直接拒绝。
- Provider DTO 包含 `id/alias/baseUrl/configured/models`；credential action 仅为 `keep/replace/clear`。
- `save` 对整个 Provider/模型域先纯校验，再合并原 YAML，一次原子替换；任一错误不修改文件或运行态。
- ADR-0032 生效后保存成功返回 `applied`；同 generation 热更新 Session client 或只替换/退休 Assistant
  Session，设置页按相同 Core identity 回读。
- 冷启动和热应用必须将已保存的 `temperature`、`top_p`、`max_tokens` 传给聊天请求。
  `temperature` 支持 0–2，`top_p` 支持 0–1，数值 0 必须保留；缺省或清空后温度恢复为 0.8，
  `top_p` 和 `max_tokens` 不发送。配置读取器拒绝超范围、非数值和非有限数值，返回 `CONFIG_DATA_INVALID`。
- `list_models`/`test_connection` 使用瞬时新密钥或 Core 内已保存密钥，带 deadline 与取消。HTTP 失败保留稳定
  业务码，同时向设置页、GUI 运行日志和文件日志显示 HTTP 状态，以及供应商返回的
  `message/code/type/status`；非 JSON 响应只显示有界的脱敏摘要。不得回显 URL query、Authorization、
  credential、API Key 或未经筛选的完整响应 body。
- 关窗、退出、Core crash 或 generation 变化会取消/丢弃旧操作；每个请求只有一个终态。

## 数据与职责边界

Provider 设置只保存本域字段，保留其他配置与密钥；测试使用隔离数据，不改写真实用户配置。
业务写入由 Core 拥有，前端不恢复旧 Qt HostRpc，也不直接操作配置文件。

## 验收与回退

自动门覆盖 current/non-v1/corrupt schema、unknown-field/secret 保持、credential 三态、Provider 增删改、槽
引用、原子故障、网络终态、generation identity 保持和 secret scan。真实 Windows Tauri 验证中文 IME、模型
列表/测试、应用/保存、关窗与重新打开；公共代码以同一候选 SHA 通过三平台门。

回退先禁用 `providers.*`/`model.*`，取消并排水在途探测，再逆序回退代码；绝不删除、恢复或重写用户
现有 `api.yaml`。

## 历史验收记录（2026-07-29）

> 以下记录当时的实现、测试与任务安排，不作为当前流程要求。

生产实现和本地自动门已完成，工作包总表已进入 `stabilizing`。2026-07-30 验收回归修复后的本地证据为
Python unit 1182 passed/6 skipped、canonical frontend 99 passed、locked Rust 210 passed/23 ignored、Smoke Harness
2/2 cases（25 tests）和 Runtime v2 Shell Harness 7/7 cases（166 tests）；locked check、Rust fmt 与 diff
完整性检查通过。该段保留的是 ADR-0032 之前的历史验收事实；当时自动门还覆盖 Qt service -> v2 -> Qt
service 回读、unknown/non-target/secret 保持、稳定超时与保存错误码、重复保存串行化、探测关窗取消、真实
Core get/save 往返和 restart 后新 generation 重新绑定。当前规范改由同 generation 热应用测试取代该重启路径。

验收阻断回归“保存并应用后角色变成粉色默认背景，重启后窗口不可见”的根因是：新增但未完成模型列表的
非当前 Provider 被 Core 配置读取器错误提升为全局 `PROVIDER_SETUP_REQUIRED`，导致已经有效的聊天槽和
`current_character_id` 一起失去发布。读取器现允许未被槽引用的 Provider 保持空模型草稿，只以实际聊天槽
能否解析为启动条件；跨层回归固定了“有效聊天槽 + 未完成 Provider + N.A.V.I.”保存后仍为 `READY`。
同时设置端在新 Core generation 发布后通知主桌宠重新装载角色资源，冷启动无法取得角色 DTO 时也不再等待
无效占位图片才 reveal，确保错误态仍可见、可恢复。真实 `data/**` 只做了脱敏只读诊断，未被测试或修复改写。
首次修复复验仍会丢失的第二层原因是主桌宠 WebView reload 后前端布局 revision 从 1 重新计数，而原生
`WindowGeometrySession` 仍保留旧 revision，导致重载首个布局被当作 stale、页面停在隐藏的 loading 状态。
前端现先读取原生已应用 revision，再从下一值继续；真实 Windows 窗口已执行整页 reload，重载前后均确认
`N.A.V.I.` 名称、主题和立绘可见。

项目负责人于 2026-07-31 在当前开发会话中明确声明 WP-3S-01 已亲自验收通过，并授权开始后续 Harness
改造。Work Package 总表据此登记 accepted；本规范不补写负责人未提供的设备组合、CI run ID 或候选
SHA 细节。当时将 WP-H-01 排在 WP-3-04 之前；这项历史安排已不约束当前开发。
