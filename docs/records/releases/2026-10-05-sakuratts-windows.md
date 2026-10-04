---
kind: record
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-10-05
---

# SakuraTTS Windows 本地环境复用验证

Windows / RTX 5060 上，SakuraTTS 插件已完成离线导入、CUDA 与 CPU 实际合成、空闲休眠、再次唤醒、取消及进程回收验证。使用本地旧运行环境装配最新源码，不代表已验收魔搭发布压缩包的全部依赖组合。插件契约见 [SakuraTTS 插件 Spec](../../specs/runtime-v2/sakuratts-plugin.md)，上一轮发布与 Mac 联调见 [2026-10-04 记录](2026-10-04-sakuratts-integration.md)。

## 代码与环境

- Sakura：`codex/sakuratts-plugin-20261005`，`78fa35dc9ae21e987eee5195f888f7bcab9a728e`。
- SakuraTTS：`codex/portable-modelscope-20261005`，`585a1fb74b32bcfaa019ae4091bdf02c7d9aa873`。
- GPU：RTX 5060，8 GB，驱动 610.62。
- 复用 `D:/Project/SakuraTTS/portable/runtime` 的 Python 3.11.15 主环境、Python 3.9 声学和准备组件、CuPy 14.2.0、CUDA 库、CPU PyTorch 2.7.0，以及本地 Sakura 角色权重和参考音频。
- 主环境的 ONNX Runtime 为 1.30.0，CUDA 声学环境为 1.19.2。`threadpoolctl 3.6.0` 从本地 wheel 补入。

旧准备组件带有 ONNX 1.14.0，不能验证新转换器要求的 IR 10。其他本地新版 ONNX 属于 Python 3.11 或 3.12 ABI，不能复制到 Python 3.9 环境。仅下载了 `onnx-1.17.0-cp39-cp39-win_amd64.whl`，约 14.5 MB；大型运行库、模型与参考资源均来自本机。替换发生在验证副本，原整合包和原仓库的未提交修改保持不变。

## 实际运行结果

最新源码和启动器装入运行环境副本后，生成本地 ZIP，交给插件 `BundleStore` 实际解压、检查和切换。导入成功。Provider 启动预热没有创建服务进程；对话事件启动 managed 服务并提前唤醒，自动模式选择 CUDA。

CUDA 检查通过 NVRTC 内核、FP32/FP16 矩阵运算、CUDA Graph 和 ONNX Runtime CUDA 实际执行；CPU 检查通过 ONNX Runtime 矩阵运算。

使用原始角色权重和 OGG 参考音频，两个后端均完成首次转换、参考准备和两次合成：

| 后端 | 首次请求 WAV 帧数 | 休眠后请求 WAV 帧数 | 采样率 |
|---|---:|---:|---:|
| CUDA 自动选择 | 134,400 | 145,920 | 32 kHz |
| CPU 显式选择 | 153,600 | 139,520 | 32 kHz |

空闲时间设置为 2 秒，四次合成后均观察到 `state=sleeping`、`model_loaded=false`、`worker_pid=null`，对应推理进程已退出。关闭 Provider 后，追踪到的自有进程全部退出。没有测量 WDDM 显存数值，也没有进行人工听感验收。

另一次 CUDA 长文本请求在 `/runtime` 报告 `busy=true` 后取消，约 0.109 秒完成回收；任务返回 `cancelled`，产物释放一次，没有写入取消音频，没有残留自有进程。随后再次合成成功，得到 49,280 帧音频。

## 自动化验证与失败记录

- 插件导入、设置与取消相关单元测试 15 项通过；前端插件设置行为测试 22 项通过。
- 引擎打包、资源准备、managed 服务和 NVIDIA 启动相关测试首次执行为 47 项通过、18 项因测试解释器缺少 HTTP 依赖跳过。改用复用的主运行环境执行对应两个模块，22 项全部通过，覆盖原先跳过的 18 项。
- Windows `cargo check --locked --offline` 通过，使用本地 Cargo 缓存。
- 聊天集成首次在夹具复制依赖阶段报 `WinError 17`：Runtime 位于 D 盘、worktree 位于 C 盘，硬链接不能跨卷。`tests/conftest.py` 改为普通复制后，夹具成功准备。
- 直接调用原嵌入式 Python 时，其 `python312._pth` 固定导入旧仓库；因此出现旧历史行为与新断言不一致的 10 项失败。在 worktree 内复制轻量解释器入口并显式复用原标准库、依赖目录后，子进程导入位置已确认属于最新 worktree。
- 正确源码环境下，插件与聊天组合测试为 49 项通过、1 项失败：`test_cancel_interrupts_blocked_provider_read_with_one_terminal` 的 `received.wait(3)` 超时。日志已到 `api.request.started`，尚无 HTTP 服务收到请求的证据。只进行了一次带线程栈探针的单例诊断，结果通过；没有复现原超时，原因未明，不能据此宣称修复。未修改期限、断言或增加自动重试。

## 真实开发环境联调

使用已有 `Sakura Development` 配置、角色与聊天记录启动桌面程序，启动前备份配置和角色 JSON。内置模型服务、MCP 和联网插件此前失败，是源码 worktree 缺少发行根 `plugins/dependencies` 中对应的依赖；用户插件依赖目录不能替代此位置。从本机已有仓库复制依赖后，真实启动日志确认模型服务、Assistant、ASR、SenseVoice、MCP、Mem0、TTS Hub 等插件正常加载。

主程序已通过原生文件入口安装 SakuraTTS，并导入 Windows 整合包，界面显示 `0.1.0a1-local-585a1fb · windows-x64` 已安装。真实聊天正常返回，但首次语音失败，错误为 `TTS_CHARACTER_CONFIG_INVALID`，插件详情记录 `'toneRefs'`。根因是 Provider 只读取自身扩展，而角色工作室将共享模型与参考表保存在 `extensions.sakura.tts.gpt-sovits`。修复后经宿主资源接口读取共享配置，保留显式 SakuraTTS 字段的优先级；不修改角色包。回归测试覆盖无私有扩展、语气选择、模型覆盖及读取不改写，插件测试共 10 项通过。修复后的桌面语音播放仍待验收。

设置界面移除多余分割线和路径表单，强化分组标题，状态改为未加载、正在加载、已加载、正在合成、已卸载。修复轮询被编辑草稿覆盖导致“状态未知”的问题，前端相关测试 76 项通过；Windows `cargo build --locked --offline` 通过。

## 本机产物

### 深目录中的 HTTP 400

真实开发环境切换到 N.A.V.I. 后，首次模型准备返回 HTTP 400。引擎日志显示 `sklearn.metrics` 加载 `_middle_term_computer` 时报告 DLL 文件名过长。相同准备解释器与同一安装位置可以稳定复现；单纯给解释器路径加 `\\?\` 前缀不能解决 Python 的模块加载路径问题。

引擎在 Windows 准备和推理子进程中，仅为原生扩展的 DLL 加载传入扩展长度路径；模块公开路径、Python 搜索路径和普通资源路径保持原表示。直接修改整个搜索路径会破坏 CuPy 中混用路径分隔符的头文件读取，因此没有采用该方式。模型准备失败通过 HTTP 返回末尾诊断行，插件同时保留 HTTP 响应正文。

隔离安装根长度为 165 字符，长于真实安装根的 131 字符。空缓存下使用真实角色只读资源，完成 GPT、SoVITS 转换、参考音频准备和 CUDA 合成：首次 120,320 帧，空闲卸载后再次合成 50,560 帧，均为 32 kHz。验证没有操作真实桌面窗口，也不代表扬声器播放验收。

- 插件测试 11 项通过，覆盖 HTTP 失败原因保留。
- 引擎转换、子进程引导和日志测试 21 项通过，包括超过 260 字符目录中的实际原生扩展加载。
- 推理进程生命周期测试 25 项通过、1 项按既有平台条件跳过。初次所用推理解释器缺少测试依赖 `psutil`；确认本地包为 `cp37-abi3-win_amd64` 后，仅在测试进程显式载入该包，完成原测试，没有下载或增加产品依赖。
- 引导测试首次在主推理解释器缺少 `onnx`；改用本机已有准备解释器后，相关测试全部通过。

产物为 `dist/SakuraTTS-Windows-CUDA-CPU-585a1fb-winpath1.zip`（3,083,440,719 字节）及 `dist/Sakura-SakuraTTS-0.1.0-winpath1.sakplugin.zip`。整合包修复原生扩展加载；插件包包含角色共享配置读取和 HTTP 错误详情。证据在 `temp/long-path-synthesis-final/report.json`、`temp/long-path-tests-release.log`、`temp/long-path-process-tests-complete.log`。

### 参考准备复用与 TTS 诊断

真实运行日志中，一次合成耗时 20.549 秒，参考准备占 19.432 秒，GPT 与 SoVITS 分别为 0.704 秒和 0.408 秒。未缓存的参考音频每次都启动准备解释器并加载 CPU 模型。改为同一推理会话复用准备进程后，独立测量的首个参考准备为 15.664 秒，第二个不同参考为 1.400 秒，两次使用同一进程；会话关闭后该进程退出。

插件在对话开始时提前加载模型并准备默认参考音频。隔离缓存、N.A.V.I. 只读角色资源和 CUDA 下，20 字短句完整插件调用结果如下；输出均为有效的 32 kHz WAV，不与旧日志中的不同长度文本直接比较总耗时。

| 场景 | 插件总耗时 | 参考准备耗时 |
|---|---:|---:|
| 默认参考提前准备后的首次合成 | 2.468 秒 | 0.241 秒 |
| 切换到未缓存的语气 | 1.875 秒 | 1.415 秒 |
| 重复使用该语气 | 0.500 秒 | 0.0015 秒 |
| 空闲卸载后的再次合成 | 4.031 秒 | 0.222 秒 |

空缓存下的模型转换、唤醒和默认参考准备共 36.657 秒，首次冷启动开销仍然存在。另一个未缓存语气在准备进程启动后取消，0.094 秒完成回收，任务返回 `cancelled`，产物释放一次，没有残留自有进程或误报运行失败。证据保存在 SakuraTTS 工作副本的 `temp/reference-preparation-perf/persistent-report.json`、`temp/tts-diagnostics-validation-v2/report.json` 和 `cancel-report.json`。

插件经宿主日志接口向“TTS”栏输出阶段耗时、缓存状态、音频时长和错误原因。设置读取成功使用 debug 级别，失败保留 warning。设备选项及状态中的 DirectML 显示为“AMD / Intel 显卡”；本机整合包仍仅包含 CUDA / CPU，未增加或验证 DirectML 后端。

引擎相关测试共 48 项通过；其中 30 项最初因主解释器缺少 HTTP 测试依赖而跳过，显式复用本机 HTTP 依赖后全部通过。插件测试 12 项、Rust 轮询日志测试和文档 Harness 通过。前端配色、角色切换与插件设置相关测试 84 项通过，覆盖同一 Core 内切换角色后刷新配色，以及缩放松手后不再回弹。

Windows 原生程序编译、链接成功，最终覆盖原 `sakura.exe` 因正在运行而失败；新链接产物另存为 `sakura-tts-fix.exe`。更新包为 `SakuraTTS-Windows-CUDA-CPU-585a1fb-perf1.zip` 和 `Sakura-SakuraTTS-0.1.0-perf1.sakplugin.zip`，已核对关键源码与插件 ZIP 完整性。没有替换正在运行的程序，桌面播放仍待验收。

### 前期验证文件

SakuraTTS 验证目录为 `D:/Project/SakuraTTS-windows-validation`：

- `temp/prepare_local.py`：本地依赖复用与源码装配脚本。
- `temp/validate_plugin.py`、`temp/validate_cancel.py`：真实 Provider 验证脚本。
- `temp/plugin-validation/report.json`、`cancel-report.json`：合成、休眠、取消与进程结果。
- `temp/plugin-validation/audio1.wav` 至 `audio4.wav`：CUDA 与 CPU 输出。
- `temp/local-runtime.zip`：本地重组包，不是发布产物。
- `temp/plugin-validation/data/bundles/current.json`：隔离安装的当前版本指针。

Sakura worktree 的 `temp/chat-validation.log` 保留旧解释器导入导致的失败；`temp/chat-validation-current-source.log` 保存正确源码下的 49/1 结果；`temp/cancel-diagnostic.log` 保存一次单例诊断结果。

本地整合包为 `dist/SakuraTTS-Windows-CUDA-CPU-585a1fb.zip`，约 3.08 GB；角色配置修复版插件为 `dist/Sakura-SakuraTTS-0.1.0-character-fix.sakplugin.zip`。

未验证 DirectML、精确显存释放量和人工听感。桌面安装和整合包导入已验证，修复后的实际播放仍待验证。发布压缩包本体未下载；本记录不能替代其完整发行验收。聊天取消测试的原始超时仍待调查。
