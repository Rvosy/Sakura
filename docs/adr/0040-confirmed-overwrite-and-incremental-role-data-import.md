---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0038-explicit-transactional-legacy-import (empty-target and merge policy), 0039-prioritize-irreplaceable-legacy-data (dirty core-data failure policy)
updated: 2026-08-31
---

# ADR-0040：保留式合并，并提供按角色增量导入历史与记忆

## 背景

旧用户可能已经在 Runtime v2 中产生配置、Timeline 和 Memory，也可能持有被截断、混入未知记录或部分损坏的多个
0.9.x 数据目录。把“首次设置未完成”等同于“目标为空”既不可靠，简单禁止非空目标也不能满足用户修复遗漏数据的需要。
聊天历史和长期记忆不可重新下载，优先级高于配置、角色包、TTS 和插件资源。

## 决策

- 首次迁移以现有目标数据为基础构造最终 payload，再叠加旧版转换结果。目标独有内容保留；只有同路径文件或同稳定身份
  记录发生内容冲突时，inspect 才通过 v1 `overwriteDomains` 返回需要授权的领域。WebView 显示明确确认弹窗，并把完全
  相同的领域列表作为命令参数回传；确认表示允许旧版数据覆盖这些冲突项，不表示授权删除整域。配置存在跨文件 schema
  投影，只要源、目标配置均非空就保守要求确认。计划改变或未确认时拒绝开始。
- Timeline 按 entry ID，Memory 按 point ID、history row ID 和 profile key 合并；相同记录跳过，目标独有记录与未知文件
  保留，同角色同身份冲突确认后由源覆盖，跨角色冲突永远阻断。角色和 TTS 保留目标独有路径，同路径或路径类型冲突由源
  覆盖；可选域构造失败时丢弃该域全部 staging 输出并保留整个原目标树。合并完成后清除 curation state，由 Core 重建。
- 覆盖授权只覆盖用户根内已列出的领域。任一目标祖先是符号链接、Junction 或 reparse point 时，在 inspect 和每次
  commit rename 前都拒绝，不能借覆盖确认写出用户根。
- Settings → System 提供独立的“导入角色历史记录和记忆”。它只接受 0.9.x 用户目录，并处理 Timeline、Qdrant、mem0 history 和角色常驻
  profile，不导入配置、角色包、TTS、插件或其他数据。
- 系统页导入先停止 Core，对源和目标生成带哈希的冻结计划；相同记录跳过，缺失记录新增，同一稳定身份但内容不同才是
  冲突。只有冲突需要第二次确认；只有可隔离错误时仍执行 apply，但不要求覆盖确认。apply 必须重新检查 plan token，
  过期计划拒绝执行。
- 0.9.x Memory先快照到 staging，inspect和apply合并使用同一冻结副本。首次迁移进入 staging前也重新检查目标覆盖域；
  最新列表与确认列表不一致时拒绝执行，避免预览后的目标变化绕过确认。
- 0.9.x `data/sakura.lock` 的 PID 若能确认仍存活则拒绝导入；陈旧或损坏锁不阻断，也不修改源目录。
- Timeline 和 Memory 在同卷 staging 副本中合并，`data/chat_history` 与 `data/memory` 作为两个原子树进入既有
  rollback journal。apply 成功前 journal 必须到达 `pending_core_validation`；Core 重新启动并达到可用状态后才
  finalize，否则回滚并恢复原数据。
- 角色 scope 是不可放宽的安全边界。Timeline entry/turn 以及 Memory point payload 的 `user_id`/`scope`、history
  `user_id` 和其引用 point 的 scope 必须相互一致；非空身份不一致属于不可覆盖冲突。只有源记录所有身份都缺失时才使用
  旧当前角色作为最后回退，目标记录绝不推断。仍无法归属的源 point/history row 分别写入确定性 quarantine JSONL；
  已存在但无法归属的目标记录不可覆盖。history inspect、token 和 apply 共用“源列顺序 + canonical user_id”表示，并
  保留目标额外列。
- JSONL 以二进制逐行迭代并逐条隔离。坏 UTF-8/JSON、未知 role、非法时间及旧 error 记录保留原始字节到隔离区，
  其余记录继续导入；assistant 只缓存当前 segment 分块，不保留整文件记录或 issue 列表。不安全 portrait 清空该字段但
  保留文字。
- 稳定 Timeline 身份不再依赖整份文件哈希，而由角色、role、规范时间及同时间同 role 的出现序号生成。旧文件尾部追加
  不能改变此前记录 ID；同一身份的正文变化才会成为冲突。
- 损坏的配置、辅助数据、源 Memory 子存储或可重新下载的 embedding 模型不能回滚已成功转换的 Timeline/Memory。
  能读取的源子域继续导入，无法读取的原始字节进入隔离区并产生 warning；目标 Memory 无法打开/读取则 fail closed，
  创建 collection 或 upsert 失败终止事务。整理 cursor 作为可重建缓存清除。
- 所有 Python 命令共用跨平台 managed process-tree runner，流式解析 stdout、排空 stderr，并在异常或父进程退出时回收
  整棵进程树。journal 读取区分 missing、readable 和 unreadable；损坏 JSON、未知 state、恢复失败都保持 Core 停止。
  `committing`/`pending_core_validation`/`rolling_back` 继续回滚，`finalizing` 只续清理，且只有恢复完成并确认 journal
  清除后才允许重启 Core。

## 后果

升级和事后修复都可以保留多角色不可替代数据，重复导入不会制造重复消息。代价是导入期间 Core 会短暂停止，Memory
合并需要额外同卷空间，并且冲突预览、计划失效、隔离报告和原子树回滚都成为长期兼容合同。真实 Windows 验收仍需覆盖
Junction、旧 Qdrant 格式、活动 WAL、进程中断和大量脏记录。
