---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-31
---

# Sakura 0.9.x 到 Runtime v2 数据迁移合同

## 入口与生命周期

迁移只出现在首次导航页。`first_run_guide_completed` 为 false 时 Shell 必须以 paused lifecycle 启动并主动显示
首次导航窗口，不得依赖等待 Core 的隐藏桌宠 WebView 来触发该窗口，也不得在用户
选择路线前打开 Core、Timeline、Memory 或插件。普通首次使用通过 `first_run_start_core` 显式启动；迁移路线只接受
原生目录选择器返回并由 Rust 保管的 opaque `selectionId`，WebView 不得提交路径。

命令固定为 `legacy_import_choose_source`、`legacy_import_state`、`legacy_import_start`、
`legacy_import_cancel`；进度事件为 `sakura://legacy-import-progress`。选择目录只保存 opaque selection 和脱敏目录名，
不得扫描文件、启动 Python 或校验数据库；用户点击“开始迁移”后才在后台执行 inspect 并连续进入迁移。状态为
`idle → selected → inspecting → staging → validating → committing → core_validating → completed/failed/cancelled`。
取消只在 staging/validating 接受，commit 后必须完成或回滚。

只支持同平台的 Windows 0.9.x → Windows v2 与 macOS 0.9.x → macOS v2；不支持跨平台搬运运行资源。
来源平台以发行 Runtime 布局识别，不能仅凭目录名或仓库中可能同时存在的多平台启动脚本推断。Windows/macOS 的
发行根与用户根可以相同；目标中已有角色、Timeline、Memory、
配置、TTS或用户插件不得阻止迁移重试。payload中的同名文件以本次旧版迁移结果覆盖，覆盖前必须进入事务 backup；
Core校验失败时恢复原目标文件。payload未涉及的目标文件保持不变。未恢复的 legacy import journal/staging仍阻止
新迁移并先走恢复。正常启动不得扫描旧目录。角色包和 TTS 是可恢复的最佳努力域；它们可以从本次 payload 缺席并以
warning 完成迁移。

## 事务与安全

导入器位于 `app/legacy_import`，使用当前发行 Python 离线运行，不 import 旧安装源码。源目录全程只读。inspect
按 `data/config`、`data/chat_history` 等结构与 schema 识别，不以目录名作为唯一依据。

完整 payload 写入目标同卷 `.legacy-import-staging-*`；提交逐文件 rename，并为既有同名文件保存
`.legacy-import-backup-*` 和脱敏 journal。journal一直保留到 Core达到 `ready/degraded/setup_required`：

- `ready/degraded` 且角色投影可用：写首次设置完成标记、finalize并进入桌宠；
- `setup_required`：finalize并进入缺失角色/Provider设置，不删除已迁移数据；
- `failed`、超时或不可读取：停止 generation并 rollback；下次可重新迁移；
- 进程异常退出后，下次启动在 Core 前依据 journal自动回滚。

journal中的文件操作必须先持久化意图再执行 rename。Core校验成功后先持久化 `finalizing` 再删除 backup；一旦进入
`finalizing`，恢复逻辑只能继续清理，禁止回滚已验证的数据。

回滚必须先持久化 `rolling_back`，并在每个反向文件或原子树操作完成后原子持久化剩余工作。删除操作可以安全重放；
backup 已恢复到目标但进度尚未落盘时，恢复逻辑必须识别目标已恢复并只推进进度，不得再次按 installed 删除它。
兼容旧 `committing`、`pending_core_validation` journal 时，也必须先保守识别已经恢复或从未移走的目标，再进入
`rolling_back`。backup、staging 或 journal 清理失败时保留 journal，下次启动只继续安全的剩余回滚或清理；末尾
清理不得删除迁移前已存在的空 `characters/`、`tts/` 目录。

报告固定为 `data/legacy-imports/<id>/report.json`，只含域、数量、大小、相对标识、哈希、稳定错误和警告。报告、
事件和日志不得含 API Key、聊天/记忆正文、绝对源路径或旧 `.env` 内容。大型 payload的逐文件哈希可以有界并行，
但输出顺序必须按相对路径确定，任务队列必须有界，取消仍需在分块哈希期间生效。
离线迁移进程不得打开或追加 `data/logs/sakura-runtime.log`，也不得接收日志文件路径。它只通过 stdout 机器协议向
Rust 父进程提交白名单内的结构化 diagnostic；Rust 丢弃子进程自由 message，使用固定中文目录投影并由唯一 writer
记录 import operation、领域、步骤、安全 diagnostic、异常类型、稳定 reason code 及 SQLite/OS 错误码。角色包或
TTS 被跳过时，报告和统一日志必须记录稳定 warning，但最终状态仍为 completed。未知迁移
事件必须丢弃，不得另建迁移日志。失败 UI 必须显示这一个统一日志的相对路径。

## 数据映射

- 旧 API profiles、密钥、地址、模型槽及允许名单内 `.env` 字段转为当前 `config/api.yaml`；旧 `system_config`
  只投影当前有效的工具循环、屏幕感知、记忆整理和 UI 字段。MCP Server 中已废止的
  `requires_confirmation` 字段（包括 tool policy 内嵌字段）直接删除，保留 Server 及其当前仍有效的配置。
  0.9.x PR#110 的 `text_*`、`vision_*` 选择字段必须转为当前 `chat`/`vision_chat` 模型槽；已有当前形态的
  `model_slots` 时以其为准，`text_enabled=false` 且尚无模型槽时由旧视觉选择生成 `chat`。输出必须删除这些
  选择字段及 `model_names`，并把旧 Provider 可接受的模型列表规范化为当前 `models[].name`，同时保留 Provider
  顺序、密钥和允许的未知字段。
  旧屏幕感知的 `enabled` 与 `screen_context_enabled` 合并为当前单一 `enabled` 字段；打包内置 Web MCP
  使用 `{core_root}` 定位 `core/app`，不得把发行根误当作 Python Core 根。
- Timeline和长期记忆必须先于其他域迁移。二者的角色身份来自旧聊天 scope、curation scope 和当前角色 ID；角色包
  只参与可唯一确定的大小写规范化，不拥有聊天或记忆。角色包随后尝试完整复制并由当前 `CharacterRegistry` 校验；
  复制、转换或校验失败时清除 staged `characters/`、确认该目录已不存在后记录
  `LEGACY_CHARACTER_IMPORT_SKIPPED` 并继续；锁或权限等原因导致清理无法确认完成时，整个迁移必须在 commit 前明确失败。
  旧版
  `compat_default` 等内部主题来源标记统一转为当前 `package`，保留实际主题颜色，不得因此拒绝角色。角色校验必须在
  大型 TTS复制前执行。voice 的当前选择写入
  `sakura.tts`，同时生成 `sakura.tts.gpt-sovits` 与 `sakura.tts.genie` 两个角色 extension，使迁移后切换已安装
  引擎不需要重新导入角色；能唯一匹配的 ONNX 只写入 Genie extension。大小写只能做唯一匹配，冲突阻止提交。
- JSONL 按 archive 后 active 顺序导入。user → human；相邻 assistant 行合并为一个 segments entry；已知 error
  不成为事实但原文进入隔离；未知 role、坏 JSON、非法时间或不安全 portrait严格失败。ID由源文件指纹、相对文件
  和行号确定性生成。
- 手动截图 marker 从 human正文剥离并生成 `manual_screen` observation；定时/自主 marker生成
  `scheduled_screen` observation；可关联的旧视觉摘要进入 observation，原始 store进入隔离区。
- `data/memory` 的 Qdrant、mem0 SQLite和 profile必须迁移且不重新 embedding。mem0 SQLite不得作为普通的
  主库/WAL/SHM 文件组合逐个复制；必须使用 SQLite backup API从旧库读取一个一致事务快照，合并已提交 WAL，
  且不得修改旧主库、WAL或复用旧进程的 SQLite `-shm`；只读备份连接正常更新的 `-shm` 读锁槽位不属于用户数据
  变更。快照只需通过 `quick_check`，不得把复制时序或加载器异常误报为
  旧数据库结构不兼容。
  mem0 SQLite在 staging中通过与 Core 相同的 SQLite manager补齐缺失的可空字段；旧 `history` 的额外字段和
  既有行必须保留，不得要求旧库预先符合当前新建库的精确结构。缺失或旧结构的 `messages` 短期缓存表可以补齐；
  只有缺失稳定行 ID、无法安全补齐时才清空并重建该缓存表。迁移器不得用手写的 Qdrant metadata、profile shape、
  精确 schema或向量维度门禁提前拒绝已完整复制的记忆；提交后的当前 Core 启动是最终兼容性校验。无法读取的 mem0 SQLite不得
  静默跳过或隔离为成功，必须保留旧源并明确失败。旧整理 count映射为当前角色 Timeline cursor。
  只要本次迁移包含长期记忆，迁移器还必须在提交前准备并校验当前 Runtime 固定 revision 的 FastEmbed ONNX
  模型。目标已有完整模型时直接保留；源目录含同一固定 revision 时复制到 payload；旧版 Hugging Face
  Safetensors/PyTorch缓存不得冒充 ONNX模型，否则使用当前正式下载逻辑把模型直接写入 payload。
  模型准备、固定工件校验或取消失败时，整个迁移保持原子失败并不得提交记忆数据库；不得把“迁移成功后再由用户安装模型”
  作为成功结果。报告记录 `memoryModelFiles` 和 `memoryModel` bytes，模型准备进度位于长期记忆阶段内且早于 TTS。
- Windows 顶层 TTS Junction只跟随一次，内部 link 在实际复制时拒绝该可选域；断链、目标重叠或未知布局在 inspect 中产生
  warning 并跳过 TTS，不得阻止聊天和记忆迁移。macOS 0.9.x 的 `data/tts_bundles/installed` 映射到 v2 `tts/`；
  仅保留词法目标仍在该 TTS 树内的相对符号链接，越界相对链接拒绝该可选域并产生 warning，绑定旧安装绝对路径的符号链接
  不复制并写入迁移 warning。可执行位等 POSIX 文件模式必须保留。识别资源复制到 v2 `tts/`；
  `data/tts_bundles/onnx` 中能唯一匹配角色 ID 的模型进入对应角色 `voice/onnx`，孤儿模型保留在
  `tts/onnx`；旧绝对运行路径不得保留，包括 Python `site-packages/*.pth` 中的旧安装目录。Windows 对空的
  TTS staging目录可以使用受控的多线程系统复制，
  但传给系统复制工具前必须去掉目录选择器产生且该工具不支持的 Win32 `\\?\` namespace前缀；必须保持相同的
  噪声排除和 link拒绝规则，支持取消，以实际复制字节持续发布进度，并在复制后复核文件数与总字节。系统复制
  返回码、安全诊断和复制前后统计必须经父进程进入统一 Runtime日志，以区分预扫描、系统复制、后扫描和 ONNX合并
  失败。任一 TTS 复制、合并、路径适配或配置校验失败必须清除该域的 staging 输出，并仅在确认该目录已不存在后记录
  `LEGACY_TTS_IMPORT_SKIPPED` 或相应稳定 warning 并继续提交；锁或权限等原因导致清理无法确认完成时，整个迁移必须
  在 commit 前明确失败。用户取消仍中止整个事务。历史、长期记忆、配置及
  其他非 TTS 用户数据必须先完成迁移和当前加载器校验，角色包的最佳努力结果也必须已确定；TTS作为最后一个数据域复制，
  避免轻量配置错误导致重复复制大型资源。校验完成后，独占的
  顶层 `tts/` 以单个目录事务提交，journal必须先记录目录安装/既有目录备份意图；不得为其中每个资源重复扩写
  journal。Core校验失败时整目录回滚并恢复安装器原有的空目录。
- inspect 的 `requiredBytes` 只表示完成非可选域所需空间，不包含角色包、TTS和 TTS bundle；可选域空间不足按上述
  warning 语义跳过，不能在 inspect 阶段拒绝核心迁移。
- 笔记、提醒、任务、角色工坊和 `sakura_mobile` 数据进入当前路径。其他旧插件代码、插件私有数据、运行事件、
  视觉原始记录和旧 history原文进入 `data/legacy-imports/<id>/quarantine`，不进入 `plugins/user` 且不执行。
- 资源根下的日志、diagnostics、无关 cache、lock、临时下载和旧 migration backup不迁移；不能仅按目录名
  递归排除依赖包内部的 `diagnostics`、`cache` 等真实代码目录。上一条明确要求的当前 ONNX记忆模型缓存属于
  长期记忆可用性工件，不在“无关 cache”排除范围内。

## 验收

自动测试必须覆盖 0.9.6/0.9.8/0.9.9 结构识别、paused Core、非空目标、确定性 Timeline、segment 合并、截图
marker、错误/未知 role、Memory cursor、TTS Junction、取消、空间不足和每个 commit阶段回滚。还必须覆盖角色包
损坏、TTS复制/后扫描失败及 TTS布局 warning 均能保留 Timeline和Memory，且用户取消不会被可选域吞掉。成功、带
warning完成、失败和取消
均需证明源文件 bytes/mtime/hash不变，且脱敏输出零命中凭据、正文、记忆和绝对源路径。发布前使用
`sakura-release` 的副本分别完成一次真实 Windows 与 macOS arm64 人工迁移，不直接改动原目录。macOS 验收必须覆盖
GPT-SoVITS Miniforge 内部相对符号链接、可执行位、托管 Python/推理配置路径以及迁移后真实 TTS 启动。
长期记忆回归还必须覆盖：无目标模型时把完整 ONNX模型纳入 staging/target、准备失败时目标不变、完整目标模型不重复准备，
以及模型准备完成后才开始最后的 TTS域。

迁移达到 `completed` 后，导航页必须保留可见状态并只显示一个主操作：无需补充设置时显示“完成”并关闭窗口，
仍为 `setup_required` 时显示“继续首次设置”；完成态不得继续提供“返回”。
