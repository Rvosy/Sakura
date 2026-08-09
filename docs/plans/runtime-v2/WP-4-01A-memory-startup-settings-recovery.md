---
kind: plan
status: accepted
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-09
---

# WP-4-01A Memory 启动预热与设置窗口恢复纠正计划

## 范围

本纠正包只恢复 WP-4-01 已冻结但真实产品未满足的两项生命周期语义：当前 Core generation 创建
Memory owner 后立即非阻塞预热已安装的固定 embedding 模型；设置窗口在 Memory 首读加载、超时或关闭
期间始终可以有界关闭并重新创建；真实复现另写一份脱敏、定长的 Memory 初始化诊断时间线。负责人在
诊断确认 PyTorch 冷导入与体积成本后，将本纠正包扩展为只迁移长期记忆的推理后端：公开模型名与 384
维契约不变，底层改用固定 ONNX 工件、FastEmbed 和 ONNX Runtime。快速接话不接入 Runtime v2，也不在
本包迁移其 BGE 模型或分类头。协议 DTO 和配置 schema 不变，诊断日志不得包含记忆正文、query、路径、
密钥或底层异常原文。若 mem0 冷导入本身阻塞 Core，则按
[ADR-0011](../../adr/0011-runtime-v2-memory-process-isolation.md) 把完整本地 Memory 运行时隔离到当前
generation 私有子进程，Core 保留业务策略、公开状态和生命周期所有权。

## 实施顺序

1. 用 Python 单元测试先证明 Memory owner 构造后、任何 `status/search/settings.get` 之前已经调用一次
   `preload(wait=False)`；模型未安装时继续稳定降级且不得隐式联网。
2. 把首个预热触发从 `MemoryBoundary.status()` 移到 owner 创建路径；状态读取只观察，不启动新的加载根。
3. 让设置页首次 Memory 快照对 generation/transport/deadline 瞬时错误执行可取消的有界重试，使用稳定
   中文加载状态，不把 Router 原始错误直接显示给用户，也不在页签切换间建立两个轮询器。
4. 关闭设置时停止页面重试并使旧 window generation 失效；原生窗口若正在销毁，把一次立即重开请求
   排队到 `Destroyed` 清场后，用新的单调 generation 创建窗口。
5. 每次 Shell 启动覆盖 `data/logs/memory-initialization.jsonl`，串联 owner/preload、Memory 子进程中的
   mem0 import、embedding model load、Qdrant 创建、LLM client 创建、SQLite history 创建与 `mem0_ready`，
   再接上 Core Memory 请求、deadline、设置关闭与重开；只写固定事件、耗时、状态和错误类别。
6. 将 Memory provider 从 HuggingFace/SentenceTransformer 切到 Sakura 的 FastEmbed adapter；固定
   `qdrant/all-MiniLM-L6-v2-onnx` revision，只向 FastEmbed 传入已校验的本地 snapshot 和 CPU provider。
7. 模型下载、ZIP 导入、缓存识别和 Release 文件名切换到独立 ONNX cache；旧 PyTorch cache 原样保留，
   缺少 ONNX 模型时稳定降级，启动不得借 FastEmbed 隐式联网。
8. 以同一批中英日文本比较旧 SentenceTransformer 与新 FastEmbed 向量、样本间余弦矩阵及 384 维契约；
   兼容门通过后复用现有 Qdrant，不执行重建或迁移。
9. 从 Runtime 依赖清单移除 SentenceTransformer/PyTorch，加入固定 FastEmbed/ONNX Runtime 版本；重建
   实际 debug EXE，测量阶段耗时并确认 Core/Memory 进程没有导入 `torch` 或 `sentence_transformers`。
10. 为 OpenAI/HTTPX 固定加入轻量 `socksio` 依赖；Memory 子进程在 ONNX/Qdrant 前预加载 `socksio` 与
    `openai` 并记录固定事件。预检后的未知 `ImportError` 在同一子进程内等待 100 ms 后只重试一次，
    SOCKS 环境不得通过禁用系统代理绕过。
11. 运行定向 Python、前端和 Rust 测试，再运行 task required profiles；自动事实追加到 audit record。
12. WP-H-02A 插入并验收后，按负责人明确批准把 task base 前移到其验收提交；只允许沿原 base 后代
    前移，保留之后的 Memory 恢复预检记录，不把 Harness 纠正文件加入 Memory changed-set。

## 故障矩阵与退出条件

- 已安装模型、模型缺失、预热失败、Core 暂不可用、首读 deadline、generation 更换。
- 记忆页加载中切换页签、点击刷新、关闭设置、立即重开、连续重复打开及应用退出。
- 旧窗口的迟到成功/失败不得更新新窗口；同一窗口最多一个首读重试与一个列表轮询。
- 诊断时间线必须有界、每次启动清空，只记录当前运行；Qdrant、LLM client 或 SQLite 任一步失败时均有
  独立固定阶段与安全错误类别，secret/path/content/异常原文 scan 为零，日志失败不影响 Memory、设置
  或普通聊天。
- 正常聊天不等待 Memory；不隐式联网，不修改或修复 `data/memory/**`、旧 `memory.json` 或模型缓存。
- ONNX 模型不存在、文件缺失、revision 错误、维度错误、ONNX session 创建失败；均不得回退到 PyTorch，
  不得把旧 PyTorch cache 误报为已安装，也不得触发后台下载。
- 无代理、HTTP/HTTPS 代理、SOCKS5/SOCKS5H `ALL_PROXY`；固定代理依赖缺失必须在 ONNX/Qdrant 前精确
  失败，依赖完整时均进入 `mem0_ready`。正常启动不超过 20 秒，恢复一次未知 `ImportError` 不超过 30 秒。
- 固定兼容样本逐行新旧向量余弦至少 `0.99999`；现有 Qdrant 数据保持字节不变且可由新 backend 检索。
- 干净依赖解析包含 FastEmbed/ONNX Runtime 与 `socksio`，不包含 SentenceTransformer/PyTorch；快速接话仍保持
  Runtime v2 `unavailable`，不作为本包消费者或验收项。
- 最新候选的 docs、smoke、core-host、runtime-v2-shell 全绿，并另跑 python-full 扩大回归；Windows
  实机确认页面不再
  反复切换文案、不出现 deadline 原文，关闭后可以立即重新打开且退出无相关进程残留。

## 回退

停止新设置动作并正常退出应用，整体回退本 WP 的启动预热、首读重试、窗口重开协调和 FastEmbed provider。
若恢复旧依赖，只恢复代码与依赖清单，不删除新 ONNX cache，也不删除、恢复、迁移或修复任何用户 Memory、
旧 PyTorch cache、配置或聊天历史；WP-4-01 已接受的数据契约保持不变。
