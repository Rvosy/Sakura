---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03D Windows HostBackdrop 输入栏液态折射 PoC 自动验证记录

## 插入基线

2026-08-13，项目负责人要求开始液态玻璃实现。以
`f7e970e4e9961c8ed1362ba2340050148e3d1171` 为固定基线插入 WP-3-03D，并暂停 WP-4-04；其代码、测试
和既有证据未回滚。本包使用五字段 task v2，不创建 activation。

实现参考公开 MIT 项目 [Liquid Glass Studio](https://github.com/iyinchao/liquid-glass-studio) 的边缘 SDF、
折射曲线和分步诊断思想，但不复制其 GLSL/WebGL 背景管线。实现提交、自动命令、报告路径、候选路径和
实际结果将在发生后追加。自动门通过最多支持 `manual_pending`，人工视觉结论只记录负责人真实声明。
