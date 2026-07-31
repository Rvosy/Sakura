---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# ADR-0007：设置按 feature 随领域能力纵向迁移

> 状态：Accepted
> 决策日期：2026-07-28
> 适用范围：Runtime v2 设置 capability、配置读写、生效计划和后续能力 Work Package

## 背景

ADR-0006 和 WP-3U-01 已建立同一 Tauri App 内的设置窗口与 capability shell，WP-3U-02 又证明
角色外观可以跨 WebView、Rust 和兼容配置完成读取、预览、原子保存、失败恢复与 legacy Qt 回读。

原计划把设置集中到 Phase 5 一次性补齐。该方式会让尚无 Runtime v2 领域所有者的旧控件提前开放，
或迫使一个设置后端跨越 Provider、Memory、Tools、MCP、插件、TTS、截图和平台服务建立巨型保存
事务。旧页面已经存在也不能证明对应运行态、生效、撤销和退出语义已经迁移。

Provider 与模型配置又是 `setup_required -> ready` 和真实聊天的直接前置；把它们继续推迟到 Phase 5
会要求用户依赖 legacy Qt 或手工编辑配置。

## 候选方案

### 方案 A：按 feature 纵向迁移

以 `providers.credentials`、`model.chat_slot` 等稳定 feature key 为单位。每个领域 Work Package
同时交付公开读取、校验、保存、生效计划、失败回滚、能力门控和真实应用验收。

### 方案 B：Phase 5 集中恢复全部页面和保存逻辑

等所有主要能力迁移后，再一次性恢复旧设置页面和配置后端。该方案延迟真实消费者所需配置，并倾向于
重新聚合跨域保存与生命周期责任。

### 方案 C：先恢复页面或表单，后补领域实现

让旧控件先显示或返回占位成功，但暂时没有真实读取、保存、生效或撤销路径。该方案会把页面可见错误
表示为能力已经可用。

### 方案 D：整体迁移 legacy Qt / HostRpc 设置宿主

把 `app/ui/tauri_settings.py`、旧线程/进程宿主或“保存全部”事务整体搬入 Runtime v2。该方案继承
Qt 生命周期和跨域所有权，不符合 ADR-0006。

## 决策

采用方案 A：

- 设置迁移单位是稳定 feature key，不是页面、section 或旧设置返回对象。
- 每个 feature 必须由拥有对应运行态和数据的领域 Work Package 同时完成 `get`、`validate`、
  `save`、change plan、失败回滚、capability 和验收闭环。
- WebView 只拥有草稿和未提交预览；Rust 负责权限、identity、窗口和调用协调；领域真相继续位于
  Python Core 或已批准的原生平台服务。
- capability manifest 只开放已经完成闭环的 feature。未迁移或未知 feature 失败安全为
  `read_only` / `unavailable`，不能接成 no-op。
- 保存以领域为原子边界并保留未知字段；不建立跨多个尚无共同事务所有者的“保存全部”事务。
- 保存结果必须表达真实 change plan，例如立即生效、受控 Core restart、下次启动生效或不支持。
- 拥有用户可配置能力的后续 Work Package 必须声明其设置切片；没有设置切片时也要明确记录。
- Phase 5 只审计缺口、收口跨域一致性并编排已经 accepted 的结果，不重新建设巨型设置后端。

feature manifest、DTO、字段、错误、保存协议和验收条件继续由
[`设置增量迁移 spec`](../specs/runtime-v2/settings-incremental-migration.md) 及各领域 spec 约束。

## 后果

收益：

- 每个设置能力与真实消费者、运行态所有者和独立回退同时交付。
- 未迁移控件不会因为旧页面存在而被误报为可用。
- 配置失败被限制在明确领域边界，避免跨域半更新和伪热更新。
- Provider/模型等近期阻塞能力可以在 Phase 5 前安全交付。

代价：

- 迁移期间同一页面可以包含 `available`、`read_only` 和 `unavailable` 的不同 feature。
- 每个领域 Work Package 都需要承担设置 UI、数据兼容、真实生效和回退门禁。
- capability 和 canonical frontend 必须长期保持未知 feature 失败安全，增加契约维护成本。

## 状态与后续变更

WP-3U-02 已验证首个设置纵向切片；WP-3S-01 正按本决策迁移 Provider 与模型设置。该策略已经成为
后续 Phase 4–5 Work Package 的强制输入，因此本 ADR 为 `Accepted`。单个 WP 的当前执行状态只见
[`work-packages.md`](../plans/runtime-v2/work-packages.md)。

feature schema 的具体版本不是本 ADR 的不可变选择；它由 spec 兼容演进。若未来改回集中式跨域设置
事务或改变配置领域所有权，应创建新 ADR 并 `supersedes: ADR-0007`。
