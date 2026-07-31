---
kind: index
status: current
audience: all
source_of_truth: self
updated: 2026-07-31
---

# Sakura 文档总览

文档按职责组织。先按读者选择入口，再按文档类型查找工程资料。

## 快速入口

- [安装与配置](userdocs/SETUP.md)
- [API 配置](userdocs/API_CONFIG.md)
- [macOS 指南](userdocs/MACOS_SETUP.md)
- [开发者文档](devdocs/README.md)
- [Runtime v2 当前规范](specs/runtime-v2/README.md)
- [架构决策记录（ADR）](adr/README.md)

## 文档类型

| 目录 | 用途 |
|---|---|
| [`userdocs/`](userdocs/) | 最终用户的安装、配置和使用说明 |
| [`devdocs/`](devdocs/) | 开发者、插件作者和维护者的说明 |
| [`specs/`](specs/) | 必须满足的产品、接口和技术契约 |
| [`adr/`](adr/) | 架构选择、备选方案和决策后果 |
| [`plans/`](plans/) | 当前实施计划、Work Package 和退出条件 |
| [`records/`](records/) | 基线、验收、审计、事故和发布证据 |
| [`archive/`](archive/) | 已完成、废弃或被替代的历史资料 |

## Runtime v2 阅读顺序

1. 先读 [产品功能等价规范](specs/runtime-v2/product-capability-parity.md)。
2. 再读相关 [ADR](adr/README.md)，理解架构边界和数据兼容约束。
3. 按 [Work Package 总计划](plans/runtime-v2/work-packages.md) 确认当前执行状态。
4. 需要验证细节时查看 [Runtime v2 验收记录](records/README.md)。

Runtime v2 的 Work Package 状态只有
[`plans/runtime-v2/work-packages.md`](plans/runtime-v2/work-packages.md) 是真相源；spec、ADR 和
record 只描述契约、决策或证据，不重复维护执行状态。

## 文档元数据

每份 `docs/` 下的 Markdown 文档都必须带有 YAML front matter：

```yaml
---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---
```

目录职责、元数据、链接和 Runtime v2 状态约束由 `tools/check_docs.py` 检查，并通过
`runtime\python.exe -m harness run docs` 执行。
