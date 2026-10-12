---
title: 技术架构
description: Tauri Shell、Python Core Host 与插件进程的职责。
---

Sakura 由 Tauri Shell、Python Core Host 和逐插件进程组成。Shell 管理窗口、系统资源和 Core 生命周期；Core 负责对话受理、本地数据和插件路由；默认 Assistant、模型服务和语音等能力由插件提供。

源码入口、模块关系和对话链路见[架构总览](https://github.com/Rvosy/Sakura/blob/main/docs/development/architecture.md)。各模块的职责、流程、数据归属和失败行为见[模块规格 Spec](https://github.com/Rvosy/Sakura/blob/main/docs/specs/README.md)，当前架构选择及理由见[架构决策 ADR](https://github.com/Rvosy/Sakura/blob/main/docs/adr/README.md)。

使用指南和公开接口从[文档总览](https://github.com/Rvosy/Sakura/blob/main/docs/README.md)进入。

开发环境与测试命令统一维护在 [贡献指南](https://github.com/Rvosy/Sakura/blob/main/.github/CONTRIBUTING.md)和 [Harness 指南](https://github.com/Rvosy/Sakura/blob/main/harness/README.md)。
