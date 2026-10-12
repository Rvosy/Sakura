# 当前架构决策

ADR 说明项目当前采用的技术选择、要解决的问题，以及随之接受的影响和代价。具体行为在 [Spec](../specs/README.md) 中维护，接口与命令在开发指南中维护。

这些文档按决策主题组织。一个决定改变时直接改写对应主题及关联规格，移除不再有效的内容；过往方案与修改过程留在 Git，不维护另一套决策状态表。

| 决策 | 解决的问题 | 主要规格 |
|---|---|---|
| [Core 与插件独立进程](process-plugin-isolation.md) | 依赖冲突、原生库故障、可替换能力 | [运行时](../specs/desktop-runtime.md)、[插件](../specs/plugin-runtime.md) |
| [唯一聊天受理与普通 Assistant](single-chat-boundary.md) | 多入口取消、历史写入和生成策略耦合 | [聊天](../specs/conversation.md)、[模型](../specs/model-services.md) |
| [局部应用与明确重试](explicit-lifecycle.md) | 整体重启、迟到结果、重复副作用 | [运行时](../specs/desktop-runtime.md)、[设置](../specs/settings.md) |
| [配置归属与页面分开](settings-ownership.md) | 插件化造成重复编辑和保存时机变化 | [设置](../specs/settings.md) |
| [角色资源由插件解释](composable-character-resources.md) | 表现格式耦合、分享数据与本机偏好混用 | [角色](../specs/characters.md)、[表现](../specs/window-presentation.md) |
| [音频与临时资源归属](media-resource-ownership.md) | 合成、播放、历史与传输相互误删资源 | [语音](../specs/voice.md)、[屏幕感知](../specs/screen-awareness.md) |
| [稳定锚点与原生命中](native-window-surface.md) | 多屏漂移、透明区域和系统交互 | [窗口与表现](../specs/window-presentation.md) |
| [程序与用户数据分离](data-preserving-updates.md) | 升级和迁移破坏已有内容 | [发行与存储](../specs/release-storage.md) |
| [统一步骤日志与原始诊断](original-diagnostics.md) | 首因被遮盖、日志重复、Trace 用途混淆 | [诊断](../specs/diagnostics.md) |
| [公开资料与私人管理分开](public-private-services.md) | 管理故障与权限扩散影响公开交付 | [在线服务](../specs/services.md) |
| [拥有者负责校验](validation-ownership.md) | 重复校验和任意限制妨碍正常使用 | [插件](../specs/plugin-runtime.md)、[角色](../specs/characters.md)、[设置](../specs/settings.md) |

新增独立 ADR 应对应真实的跨模块选择或重要取舍。普通修复更新所属 Spec 或接口说明即可，不为每次提交另建决策文档。整体关系见[架构总览](../development/architecture.md)。
