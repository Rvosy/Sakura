---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# ADR-0020：助手阶段工具直接执行

## 背景

WP-4-02 为有副作用工具建立了一次性 Action ID 和原生二次确认，但当前 Sakura 的产品角色仍是响应用户
明确请求的助手，不是会自主规划和执行外部动作的 Agent。实机中浏览器导航、记忆写入等正常助手操作被
确认框打断，模型还提前生成“正在确认”的内容；确认并未提高当前阶段的授权清晰度，反而制造错误状态和
额外 Provider 续传。

项目负责人明确要求：现阶段没有需要二次确认的工具；权限机制只在未来 Agent 插件阶段引入。

## 决策

- 当前助手 ToolRegistry 在完成工具存在性、参数 schema 和 generation/contribution identity 校验后直接
  执行所有内置、MCP 和插件工具。
- `requires_confirmation` descriptor 和既有 `confirmationPolicy` 配置不激活 `PendingToolAction`；设置页
  不展示确认策略，product manifest 将该 feature 标为 unavailable。
- Action ID coordinator、Rust 原生确认和兼容配置字段不删除，只作为延期基础设施保留，当前助手入口不得
  创建或发布确认租约。
- 系统 prompt 不得声称工具需要权限、授权或二次确认。
- 未来自主 Agent 插件在启用前必须另建 spec/ADR，冻结能力声明、权限范围、授权租约、撤销和审计模型；
  不能仅把当前 `requires_confirmation` 开关重新设为真。

## 后果

- 用户明确请求的浏览器、记忆、MCP 和插件操作不再被二次确认打断。
- 当前产品不宣称存在细粒度权限沙箱；可信插件仍以当前用户权限运行，插件安全声明不变。
- 底层确认代码仍需保持不可由当前 WebView 伪造参数，并由既有测试防止资源泄漏；这些测试证明基础设施
  安全，不代表产品已激活确认流程。
- 本决策替代 WP-4-02 与 WP-4-04 中“当前助手写入/高风险工具必须确认”的激活结论，不改写其历史实现和
  验收事实。
