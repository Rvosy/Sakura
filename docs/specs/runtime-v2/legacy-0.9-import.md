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

命令固定为 `legacy_import_choose_source`、`legacy_import_inspect`、`legacy_import_state`、`legacy_import_start`、
`legacy_import_cancel`；进度事件为 `sakura://legacy-import-progress`。选择目录只保存 opaque selection 和脱敏目录名，
随后由独立 inspect 命令扫描并显示来源领域、阻断项和需要授权覆盖冲突项的领域。用户点击“开始迁移”前，存在冲突领域时必须
显示弹窗；start 参数中的确认领域必须与 inspect 结果完全一致。`overwriteDomains` 保持 v1 schema，但只表示该领域
存在需要授权覆盖的冲突，不表示授权删除整个领域。状态为
`idle → selected → ready → staging → validating → committing → core_validating → completed/failed/cancelled`。
取消只在 staging/validating 接受，commit 后必须完成或回滚。

只支持同平台的 Windows 0.9.x → Windows v2 与 macOS 0.9.x → macOS v2；不支持跨平台搬运运行资源。
来源平台以发行 Runtime 布局识别，不能仅凭目录名或仓库中可能同时存在的多平台启动脚本推断。Windows/macOS 的
0.9.x 来源与 1.0.x 目标必须位于不同物理目录；相同、包含或被包含关系继续 fail closed。1.0.x 安装器、
Updater 和普通启动不扫描或复用 0.9.x，也不建立旧目录 snapshot；唯一入口是用户显式选择后的只读导入。
各自版本内部的发行根与用户根可以相同；目标中已有角色、Timeline、Memory、配置、TTS或用户插件不得阻止迁移重试。首次迁移使用
“合并并保留”：目标独有内容保留，只有同路径文件或同稳定身份记录发生内容冲突时才要求确认并允许旧版数据覆盖；
跨角色身份冲突永远不可覆盖。配置会跨文件投影到当前 schema，只要源、目标配置树均非空就保守列为冲突领域。
payload中的同名文件以本次旧版迁移结果覆盖，覆盖前必须进入事务 backup；
Core校验失败时恢复原目标文件。payload未涉及的目标文件保持不变。未恢复的 legacy import journal/staging仍阻止
新迁移并先走恢复。正常启动不得扫描旧目录。角色包和 TTS 是可恢复的最佳努力域；它们可以从本次 payload 缺席并以
warning 完成迁移。

Timeline 按稳定 entry ID、Memory 按 point ID、history row ID 和 profile key 合并：目标独有记录保留、完全相同记录
跳过、同角色同身份冲突经确认后由旧版记录覆盖。`characters`、`tts` 以现有目标树为基础叠加旧版结果，保留目标独有文件，
同相对路径或路径类型冲突由旧版 payload 覆盖；任一可选域无法完整构造时必须丢弃该域全部 staging 输出，保留整个原目标树
并产生 warning。`data/chat_history`、`data/memory` 中目标独有的未知文件同样保留，Memory 中旧版未知扩展文件按同路径
覆盖。合并后删除可重建的 curation state，由 Core 按角色重建游标，不复用只描述单套 Timeline 进度的旧 cursor。

## 事务与安全

导入器位于 `app/legacy_import`，使用当前发行 Python 离线运行，不 import 旧安装源码。源目录全程只读。inspect
按 `data/config`、`data/chat_history` 等结构与 schema 识别，不以目录名作为唯一依据。
所有 legacy-import Python 命令必须经同一个跨平台 managed process-tree runner 启动：stdout 按行流式解析机器协议，
stderr 持续排空；正常结束释放托管关系，协议错误、异常退出、父进程退出或取消时终止整棵子进程树，不得留下 descendant。
每次执行使用绝对 operation deadline：`inspect-data` 15 分钟，`inspect`、`recover`、`finalize`、
`rollback`、`apply-data` 各 30 分钟，完整 `run` 2 小时。每次 pipe poll 后都必须同时检查 deadline；到期后
取消 stdout/stderr reader，并在既有 10 秒 finalization deadline 内终止整棵进程树。安全终止返回
`LEGACY_IMPORT_OPERATION_TIMEOUT`，先按 journal 完成并确认 recover/rollback，再允许重启 Core；进程树
状态无法确认时返回 `LEGACY_IMPORT_PROCESS_TERMINATION_FAILED`，保持 Core 停止并保留 journal，禁止继续
恢复或启动。两个错误在首次导航、设置页和统一运行日志中使用固定中文投影，不得包含子进程输出或路径；
不增加 heartbeat、自动重试或常驻 watchdog。

完整 payload 写入目标同卷 `.legacy-import-staging-*`；`characters`、`tts`、`data/chat_history` 和
`data/memory` 按原子树 rename，其余文件逐文件 rename，并为既有同名目标保存
`.legacy-import-backup-*` 和脱敏 journal。journal一直保留到 Core达到 `ready/degraded/setup_required`：

- `ready/degraded` 且角色投影可用：finalize并确认 journal 已清除，再写首次设置完成标记并进入桌宠；
- `setup_required`：finalize并确认 journal 已清除，再进入缺失角色/Provider设置，不删除已迁移数据；
- `failed`、超时或不可读取：停止 generation并 rollback；下次可重新迁移；
- 进程异常退出后，下次启动在 Core 前依据 journal自动回滚。

journal中的文件操作必须先持久化意图再执行 rename。Core校验成功后先持久化 `finalizing` 再删除 backup；一旦进入
`finalizing`，恢复逻辑只能继续清理，禁止回滚已验证的数据。
journal 读取是 `Missing`、`Readable(state)`、`Unreadable(error)` 三态：只有真正缺失才表示没有待恢复事务或 finalize
已经完成，损坏 JSON 和未知 state 一律以 `LEGACY_JOURNAL_INVALID` fail closed。增量 apply 返回成功前必须读到
`pending_core_validation`。apply 子进程异常退出、协议错误或 finalize 失败后，Rust 必须先恢复事务：
`committing`、`pending_core_validation`、`rolling_back` 完成 rollback，`finalizing` 只继续清理。journal 不可读或恢复失败时
Core 保持停止；只有恢复完成且再次确认 journal 已清除后才能重启 Core，禁止在混合树上启动。

目标路径及每个现存祖先必须在 inspect 和实际 rename 前拒绝符号链接、Junction/reparse point。覆盖确认不授予写出
`user_root` 的权限；检查后被并发替换的祖先也必须在 commit 边界再次失败。

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

- 旧 API profiles、密钥、地址、模型槽及允许名单内 `.env` 字段转为当前 `config/api.yaml`。旧环境变量
  白名单固定为 `BASE_URL → llm.base_url`、`API_KEY → llm.api_key`、`MODEL → llm.model`；仅当 YAML
  对应值缺失、为 null 或空白时填充，已有非空 YAML 始终优先。只读取源根仍在使用的 `.env`；
  `.env.migrated` 表示旧版迁移器已成功归档，不得重放。旧 `system_config`
  只投影当前有效的工具循环、屏幕感知、记忆整理和 UI 字段。MCP Server 中已废止的
  `requires_confirmation` 字段（包括 tool policy 内嵌字段）直接删除，保留 Server 及其当前仍有效的配置。
  MCP Server 的 `command`、`args` 或 `env` 若引用旧来源根或其子路径，必须按路径组件边界识别并隔离，
  Windows 路径匹配须统一正反斜杠、大小写和 `\\?\` namespace 前缀，不能继续执行旧安装源码。
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
- JSONL 按 archive 后 active 顺序导入。user → human；相邻 assistant 行合并为一个或多个不超过上限的 segments
  entry；已知 error、未知 role、坏 UTF-8/JSON及非法时间不成为事实，原始行 bytes进入隔离，其余记录继续。
  不安全 portrait清空该字段并隔离原始行，但文字仍导入。ID由角色 scope、role、规范时间和同时间同 role出现序号
  确定，不得依赖整份文件哈希；active 文件尾部追加不能改变此前 ID。实现必须以二进制逐行迭代，发现问题时立即写入
  quarantine，assistant 只保留当前不超过 `MAX_SEGMENTS` 的分块，不得缓存整文件 bytes、完整 parsed-record 列表或
  完整 issue 列表；隔离内容必须保持原始行 bytes 不变。
- 手动截图 marker 从 human正文剥离并生成 `manual_screen` observation；定时/自主 marker生成
  `scheduled_screen` observation；可关联的旧视觉摘要进入 observation，原始 store进入隔离区。
- `data/memory` 的 Qdrant、mem0 SQLite和 profile必须迁移且不重新 embedding。mem0 SQLite不得作为普通的
  主库/WAL/SHM 文件组合逐个复制；必须使用 SQLite backup API从旧库读取一个一致事务快照，合并已提交 WAL，
  且不得修改旧主库、WAL或复用旧进程的 SQLite `-shm`；只读备份连接正常更新的 `-shm` 读锁槽位不属于用户数据
  变更。快照只需通过 `quick_check`，不得把复制时序或加载器异常误报为旧数据库结构不兼容。无法打开的源 SQLite或
  Qdrant子存储必须原样进入隔离区，其他可读 Timeline/Memory继续提交并产生 warning；不得用一个损坏的源子存储回滚
  所有不可替代数据。目标 SQLite、Qdrant 或 profile 无法打开/读取时必须返回
  `LEGACY_DATA_TARGET_MEMORY_INVALID`，不得删除或重建；目标 collection 创建或 upsert 失败返回
  `LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED` 并终止事务，不得误报 completed 或 quarantined。
  mem0 SQLite在 staging中通过与 Core 相同的 SQLite manager补齐缺失的可空字段；旧 `history` 的额外字段和
  既有行必须保留，不得要求旧库预先符合当前新建库的精确结构。缺失或旧结构的 `messages` 短期缓存表可以补齐；
  只有缺失稳定行 ID、无法安全补齐时才清空并重建该缓存表。迁移器不得用手写的 Qdrant metadata、profile shape、
  精确 schema或向量维度门禁提前拒绝已完整复制的记忆；提交后的当前 Core 启动是最终兼容性校验。旧整理 count 和
  cursor 均不能同时描述合并后的新旧两套 Timeline 进度，必须按可重建缓存清除并由 Core 从空 cursor 按角色重建。
  只要本次迁移包含长期记忆，迁移器还必须在提交前准备并校验当前 Runtime 固定 revision 的 FastEmbed ONNX
  模型。目标已有完整模型时直接保留；源目录含同一固定 revision 时复制到 payload；旧版 Hugging Face
  Safetensors/PyTorch缓存不得冒充 ONNX模型，否则使用当前正式下载逻辑把模型直接写入 payload。
  用户取消仍中止迁移；模型准备或固定工件校验失败只产生 warning，必须先提交已保全的 Memory，随后允许当前插件按正式
  资源流程补齐模型。报告记录 `memoryModelFiles` 和 `memoryModel` bytes，模型准备进度位于长期记忆阶段内且早于 TTS。
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

## 系统页增量导入

Settings → System 的“导入角色历史记录和记忆”只接受已识别为 0.9.x 的用户目录；1.0/Runtime v2 目录和普通
文件夹必须明确拒绝。该入口接受 Rust保管的 opaque selection，只读取旧目录的
`data/chat_history` 与 `data/memory`。inspect时 Shell停止 Core，比较 Timeline entry、Qdrant point、mem0 history
row和 `core_profiles.json` 后重新启动；公共计划只包含角色 ID、计数、稳定冲突 ID和 plan token，不含正文、向量、绝对
路径或记忆内容。

相同稳定 ID且规范内容一致时跳过；目标缺失时新增；同 ID且内容不同时列为冲突，并且只有此时 UI显示覆盖确认。跨角色的
entry/turn/point身份冲突不可覆盖。apply再次停止 Core并重新生成计划，token不一致返回 stale，不使用旧确认。合并只在
当前 `data/chat_history`、`data/memory` 的 staging副本上进行，清除可重建 curation state，再通过原子树 journal提交。
Core启动失败时回滚并重新启动原数据。此入口不得导入配置、角色包、TTS、插件或辅助数据。
Memory scope 必须同时校验 Qdrant payload 的 `user_id`/`scope`、history 的 `user_id` 以及
`memory_id → point scope`；任意非空身份不一致都是不可覆盖的 hard conflict。只有源记录所有身份均缺失时才允许使用旧
`current_character_id` 作为最后回退，绝不据此推断目标记录。无法归属的源 point 和 history row 分别写入确定性的本地
quarantine JSONL 并计入 `recoverableErrors`；已存在但无法归属的目标记录禁止被覆盖。history row 的 inspect、plan token
和 apply 必须使用同一个 canonical representation：固定为源列顺序追加 canonical `user_id`，目标额外列保持不变。
正文、向量和原始字段不得进入公共 plan、report、事件或日志。

Settings 在新增、冲突或 `recoverableErrors` 任一非零时都必须调用 apply；只有可隔离错误时不弹覆盖确认，但仍必须完成
quarantine 并展示结果。仅当三者全为零时才可直接显示“没有新数据”。
0.9.x Memory 必须先冻结到 staging；SQLite使用 Backup API，预览和 apply合并读取同一份冻结副本，不能在生成计划后
再次读取活动源目录。首次迁移同样必须在实际 staging前重新检查目标覆盖域；最新覆盖域与已确认列表不一致时确认失效。
若 `data/sakura.lock` 中的 PID 能确认仍存活，inspect必须返回 `LEGACY_SOURCE_ACTIVE`；陈旧、损坏或无法证明存活的锁
不能阻止脏数据救援。该检查是并发写入保险丝，不允许迁移器修改或接管旧锁。

## 验收

自动测试必须覆盖 0.9.6/0.9.8/0.9.9 结构识别、paused Core、非空目标确认、追加后稳定 Timeline ID、segment
分块、截图 marker、逐行隔离错误/未知 role、Memory cursor、TTS Junction、目标祖先 link拒绝、取消、空间不足和每个
commit阶段回滚，包括四棵原子树在 target→backup 和 staging→target 之间硬退出后的完整恢复。还必须覆盖角色包
损坏、TTS复制/后扫描失败及 TTS布局 warning 均能保留 Timeline和Memory，且用户取消不会被可选域吞掉。成功、带
warning完成、失败和取消
均需证明源文件 bytes/mtime/hash不变，且脱敏输出零命中凭据、正文、记忆和绝对源路径。发布前使用
`sakura-release` 的副本分别完成一次真实 Windows 与 macOS arm64 人工迁移，不直接改动原目录。macOS 验收必须覆盖
GPT-SoVITS Miniforge 内部相对符号链接、可执行位、托管 Python/推理配置路径以及迁移后真实 TTS 启动。
长期记忆回归还必须覆盖：无目标模型时把完整 ONNX模型纳入 staging/target、准备失败时仍提交已保全的 Memory并产生
warning、完整目标模型不重复准备，以及模型准备完成或明确跳过后才开始最后的 TTS域。

增量导入还必须覆盖多角色隔离、首次新增、重复导入全部跳过、同 ID不同内容产生冲突、计划失效拒绝、确认后只覆盖冲突项、
quarantine-only apply、坏 JSONL保留有效记录、SQLite WAL快照、Qdrant point/profile/history合并、目标 Memory 读写失败、
corrupt/unknown journal 禁止 Core 重启、`finalizing` 续清理、managed child descendant 回收，以及 Core校验失败时两个原子树
一起回滚。大 JSONL 回归必须禁止整文件读取并锁定稳定 ID、segment 顺序和原始隔离 bytes。

迁移达到 `completed` 后，导航页必须保留可见状态并只显示一个主操作：无需补充设置时显示“完成”并关闭窗口，
仍为 `setup_required` 时显示“继续首次设置”；完成态不得继续提供“返回”。
