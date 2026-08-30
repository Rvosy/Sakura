---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0038-explicit-transactional-legacy-import (character and TTS failure semantics only)
updated: 2026-08-31
---

# ADR-0039：旧版迁移优先保全不可替代的聊天与记忆

## 背景

0.9.x 的聊天记录和长期记忆按角色 ID 保存，不由角色包目录拥有。角色包可以再次手动导入，TTS runtime、模型和
配置也可以重新下载或配置。把这些可恢复资源与 Timeline、Memory 放进同一个全有或全无的 staging 结果，会让一次
TTS 文件数差异或一个损坏角色包回滚已经成功转换的不可替代数据。

## 备选方案

1. 继续让所有域共同决定迁移成败。实现最简单，但可恢复资源故障会丢掉本次聊天和记忆迁移结果。
2. 提交聊天和记忆后再启动第二个独立资源迁移事务。边界最强，但需要第二套 UI 状态、journal 和恢复协议。
3. 在同一事务中先构建并校验聊天、记忆及当前 Core 必需数据，再以隔离的最佳努力步骤迁移角色包和 TTS。

## 决策

采用方案 3：

- Timeline、Memory 及其固定 revision 记忆模型优先迁移；这些域失败时仍整体失败并回滚。
- 聊天和 Memory curation 的身份来自旧数据中的角色 ID。角色 manifest 只用于不改变语义的大小写规范化，不是数据
  所有者，也不是迁移前置条件。
- 当前 Core 必需配置和其他被纳入迁移的数据仍在提交前校验；本 ADR 不把任意损坏数据都静默降级。
- 角色包和 TTS 是两个独立的最佳努力域。任一域复制、转换或校验失败时，清除该域的 staging 输出，并仅在确认该目录
  已不存在后向报告和统一 Runtime 日志写入稳定 warning，然后继续提交其余 payload；清理无法确认完成时必须在
  commit 前失败，不能把残缺目录作为原子树提交。
- 用户取消不是可选域失败，必须继续中止整个迁移。
- TTS 断链、目标重叠和未知布局从 inspect blocker 改为 warning，并跳过 TTS；必需空间估算不包含角色包和 TTS。
- 没有可用角色包时，Core 可以以 `setup_required` 完成导入，用户之后手动导入同 ID 角色包。TTS 缺失不影响聊天、
  Timeline 或 Memory 可用性。

## 后果

大型或本机相关资源不再成为历史数据迁移的单点失败源，客户可以先完成升级，再修复角色展示或重新下载 TTS。代价是
“迁移完成”可能带有可见 warning，验收必须同时覆盖完整成功、完成但跳过资源、核心数据失败回滚和用户取消四类结果。
仍保留单一 journal 和一次 Core 后置校验，不引入第二套迁移状态机。
