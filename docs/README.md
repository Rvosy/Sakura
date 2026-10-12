# Sakura 文档

文档按使用、模块规格、架构决策与开发接口组织。各模块保留足够的职责、流程、数据和协作说明；重复内容通过链接共用，历史版本见 [Releases](https://github.com/Rvosy/Sakura/releases) 与 Git。

## 了解系统与模块

| 入口 | 内容 |
|---|---|
| [架构与模块关系](development/architecture.md) | 整体结构、模块地图、对话链路及数据归属 |
| [模块规格 Spec](specs/README.md) | 运行时、插件、聊天、模型、历史、设置、角色、语音等模块的业务契约 |
| [架构决策 ADR](adr/README.md) | 当前技术选择的理由、边界和代价 |

修改某个模块时，从它的 Spec 进入，再查看关联模块、ADR 和公开接口。规格不跟随内部函数拆分而改变，业务要求变化时同步更新。

## 使用指南

| 文档 | 内容 |
|---|---|
| [安装与首次配置](guide/setup.md) | 各平台安装、源码启动、首次使用与更新 |
| [模型服务](guide/models.md) | 连接、模型选择、密钥与请求故障 |
| [聊天与语音](guide/chat.md) | 截图、主动搭话、历史、上下文和语音 |
| [角色与外观](guide/characters.md) | 导入、工坊、布局与桌面交互 |
| [插件与资源](guide/plugins.md) | 安装、更新、设置、资源与联网工具 |
| [数据管理](guide/data.md) | 数据目录、备份、迁移与清理 |
| [故障排查](guide/diagnostics.md) | 按操作步骤定位失败与读取日志 |
| [统计与隐私](guide/privacy.md) | 发送范围、开关与保存期限 |

## 开发接口与维护

| 文档 | 内容 |
|---|---|
| [贡献指南](../.github/CONTRIBUTING.md) | 开发环境、目录与提交 |
| [Plugin API v4](development/plugin-sdk.md) | 清单、通用接口、设置贡献与最小插件 |
| [插件能力接入](development/plugin-recipes.md) | 模型、Assistant、语音、MCP 等公共服务接口 |
| [角色与表现接口](development/characters.md) | 资源格式、渲染与工坊编辑器接入 |
| [发行操作](development/release.md) | 构建、更新签名、发布正文与镜像 |
| [在线服务运维](development/services.md) | 服务构建、部署模板、备份与诊断导出 |
| [诊断接入](development/diagnostics.md) | 日志入口和错误传递示例 |
| [测试入口](../harness/README.md) | 选择并执行相关验证 |

协作和文档维护规则见 [AGENTS.md](../AGENTS.md)。[当前发行说明](CHANGELOG.md) · [项目主页](../README.md)
