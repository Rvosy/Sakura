---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0003-legacy-runtime-parts
updated: 2026-08-26
---

# ADR-0034：退役 Legacy Qt 运行时

## 背景

Runtime v2 已成为唯一产品入口。继续保留可运行的 Qt 桌宠，最初是为了迁移时对照行为和验证旧数据，
但它也持续保留第二套窗口生命周期、后台 worker、插件宿主、TTS 播放、测试矩阵和 PySide6/pyobjc 依赖。
这些代码不进入发布包，也不再是用户回退方案；需要查看旧交互时可以直接使用 Git 历史。

## 决策

- 仓库只保留 Tauri Runtime v2、bundled Python Core 和 Plugin API v3。
- 删除 Legacy Qt 启动入口、Qt UI、专属 worker/控制器、旧插件宿主、旧播放端点及其测试和依赖。
- 历史行为只从 Git 提交中查看，不维持可运行参考应用。
- 升级旧安装所必需的数据 parser、迁移步骤和冻结 fixture 继续属于 Runtime v2；它们不得依赖 Qt 或第二个
  应用生命周期根。
- 数据兼容测试直接运行 Runtime v2 的 parser、migration 和 Core 链，不再启动旧应用做往返 oracle。
- 尚未进入 Runtime v2 的旧功能不因保留旧源码而继续存在；是否重新实现只由当前产品需求决定。

## 后果

Sakura 只有一套桌面生命周期和发布依赖，开发、测试与故障定位不再需要区分两个入口。Git 历史仍能恢复
任意旧实现。代价是不能在当前检出中启动旧 Qt 应用验证视觉行为；数据兼容必须依靠稳定格式、冻结样本和
Runtime v2 直接测试。
