---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 第 4 阶段启动提示词（复制给新会话）

```
继续 Sakura issue #94 第 4 阶段——接话（Backchannel）模块资源化。仓库在
C:\Users\LBW\MyFile\sakura-project\Sakura，分支 refactor/resource-manager，
第 1+2+3 阶段已完成。

请先读 docs/archive/plans/resource-manager/RESOURCE_MANAGER_PHASE_4_5_PLAN.md 的「第 4 阶段」部分（已含待拍板决策、
RM 扩展与提交序列）；需要背景再读 docs/archive/plans/resource-manager/RUNTIME_RESOURCE_MANAGER_PLAN.md（设计/状态机/
线程域）、docs/archive/plans/resource-manager/RESOURCE_MANAGER_HANDOFF.md（总交接）、app/core/resource_manager.py、
app/backchannel/controller.py、app/ui/pet_window.py 的 close_external_tools。

先就 §4.2 的待拍板决策给结论（默认取「复用 PetWindow 的 ResourceManager」），确认后按
§4.4 提交序列从「提交 4.1：RM 扩展（ThreadGroupResource）」开始逐个提交落地，每个提交保持
测试绿（破坏某测试就在同一提交里改它）。测试用 ./runtime/python.exe -m pytest（别用系统
Python）。注意 tests/ui 退出阶段约 1/3 概率的 native access violation 是既存问题
（见 docs/records/incidents/TTS_SHUTDOWN_NATIVE_CRASH.md），重跑即可，以非崩溃运行是否全绿为准。用中文。
```
