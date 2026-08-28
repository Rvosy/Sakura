---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-26
---

# Runtime v2 单一运行时边界

Sakura 当前只允许一套产品运行链：

```text
Tauri Shell -> bundled Python Core -> PluginRuntimeManager -> per-plugin API v4 processes
```

- 不保留可运行的历史桌面入口、Qt UI、Qt worker、v3 共享 Worker/Kernel、旧插件宿主或旧音频播放链。
- 行为迁移参考来自 Git 历史，不通过当前分支维护第二套应用。
- 不保留旧安装的数据 parser、schema migration 或兼容 fixture。
- 新代码不得为历史入口增加 DTO、shim、依赖、测试 profile 或发布分支。
- 旧功能是否重新实现只依据当前消费者和产品需求，不以历史源码存在为理由保活。

验证至少覆盖：仓库不存在历史入口和 PySide6 运行依赖；Core Host 初始化不加载 Qt 或 v3 Worker；发布
白名单只包含 Runtime v2 与 API v4 插件；当前数据读取器只接受 v1 契约。
