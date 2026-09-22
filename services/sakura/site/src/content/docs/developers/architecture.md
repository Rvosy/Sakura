---
title: 技术架构
description: Tauri Shell、Python Core Host 与插件进程的职责。
---

Sakura 由 Tauri Shell、Python Core Host 和逐插件进程组成。Shell 管理窗口、系统资源和 Core 生命周期；Core 负责对话受理、本地数据和插件路由；默认 Assistant、模型服务和语音等能力由插件提供。

源码入口、调用链、配置归属和验证方式见 [技术架构指南](https://github.com/Rvosy/Sakura/blob/main/docs/devdocs/TECHNICAL_README.md)。公共行为与兼容契约见 [Spec 索引](https://github.com/Rvosy/Sakura/blob/main/docs/specs/runtime-v2/README.md)，架构取舍见 [ADR](https://github.com/Rvosy/Sakura/blob/main/docs/adr/README.md)。

开发环境与测试命令统一维护在 [贡献指南](https://github.com/Rvosy/Sakura/blob/main/.github/CONTRIBUTING.md)和 [Harness 指南](https://github.com/Rvosy/Sakura/blob/main/harness/README.md)。
