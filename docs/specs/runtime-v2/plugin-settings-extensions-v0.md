---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
updated: 2026-08-21
---

# Experimental Plugin Settings Extensions v0

`sakura.host.settings.collection-v0` 和 `sakura.host.settings.surface-v0` 是显式 experimental Host 扩展。
Collection 提供当前 Mem0 所需的有界分页、搜索、筛选、列/表单声明和 CRUD callback；surface 只提供当前
Voice/Memory 页面复用 section 的展示提示。官方 Mem0、GPT-SoVITS 与 Genie 可以依赖它们。

这两个扩展不属于 Settings Basic 或 Kernel Core 的 normative 门，版本可在真实消费者反馈后变更。它们不
加载插件 HTML/JavaScript/CSS，不创建任意前端挂载点，也不改变 Python Host 作为 descriptor 语义唯一校验
层的边界。
