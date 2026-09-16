---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-16
---

# 开放插件生态验收

当前验收使用已有角色、模型和插件，检查正常 Assistant 与插件设置。执行器实验及专注样例已移除，历史 `chat_executor` 配置忽略。
当前能力与下一项工作见[总计划](../plans/runtime-v2/open-plugin-ecosystem.md)；旧里程碑和测试结果见[历史记录](../records/audits/PLUGIN_ECOSYSTEM_MILESTONES.md)。

## 使用已有角色包和配置

先从托盘退出正在运行的 Sakura，再运行[已有数据启动器](../../scripts/start-existing-data.bat)。
Windows 默认使用 `%LOCALAPPDATA%\Sakura Development`，读取原角色、模型、插件开关和聊天记录。
启动器不创建验收角色；聊天和主动保存直接写入这个日常目录。

使用其他已有目录时：

```powershell
scripts\start-existing-data.ps1 -UserRoot 'D:\Sakura'
```

启动器使用仓库 debug 程序，不自动编译。需要更新时，在仓库根目录运行：

```powershell
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked
```

## 默认对话与插件上下文

发一条普通消息，检查原角色、模型、历史和已有表现正常。停止回复后应能继续发下一条；缺少有效模型配置时仍要求完成配置。
已安装“对话规则”时，在其插件设置中查看当前文本，按需提问并检查当轮 Context 来源。安装与打包见[插件说明](../../plugins/optional/context_rules/README.md)。

验收不覆盖已有文本、模型或开关。模型回复可能变化，判断插件是否贡献内容以本轮 Trace 为依据；停用再启用后配置应保留。

## 插件设置容器的改造验收

使用已安装的便签记忆或 Mem0，原入口和布局保持不变。只操作专门创建的验收记录。

1. 打开已有记录但不改内容，回到主设置选择另一角色，应允许选择。打开空白“新增”后不填写，也不应产生未保存阻断。
2. 改动一条记录，角色切换应提示先保存或放弃。把字段恢复到原值后，应解除阻断。
3. 保存后切换角色再切回，列表仍按插件原有角色分区读取，成功保存的记录不丢失。
4. 搜索、编辑、删除正常。删除确认跨角色或插件重绑定后失效，不应把旧确认应用到新实例。

Mem0 与便签记忆均为 character 集合；新宿主会对旧版未声明 scope 的安装包保留同样保护，无需为本次修复重装。
global 集合由隔离前端夹具验证：待选角色时可查询与编辑，“应用”可完成角色切换并保留草稿；“保存并关闭”仍要求处理未保存记录。
本轮不为此增加新的业务插件。Collection 创建、更新和删除直接保存，主设置“应用”不会代为提交记录。

自动入口为 `desktop/frontend/tests/plugin-settings.test.js` 和 `settings-studio-integration.test.js`：
覆盖字段变化/还原、两种 scope、旧声明、Core 重绑定、同角色语音导入、删除确认、搜索恢复与重叠通知。
Promise 屏障在隔离夹具中控制竞态，不在日常进程中制造崩溃。自动测试不代替原生窗口人工验收。

## 便签记忆的实际对话

安装和新增、修改、重启、删除或停用的具体操作见[便签记忆 README](../../plugins/optional/fact_memory/README.md)。
匹配、预算、工具与数据兼容以[便签记忆规范](../specs/runtime-v2/fact-memory.md)为准，公共验收资料不重复维护算法。

使用用户专门创建的测试便签，检查它进入正常 Assistant 的当轮 Context；修改后重新提问，删除或停用后确认该来源不再贡献。
已有历史或其他记忆插件仍可能包含相同内容，不单凭回复文字推断便签删除失败。
真实 Core、插件进程与受控模型端点的自动入口是 `tests/integration/test_fact_memory_core_protocol.py`。

## 进程与设备边界

TTS/ASR 的旧任务、取消及迟到结果隔离通过现有真实插件进程和事件/屏障验证；公共 `context.bind()` 合同见
[SDK](SAKURA_PLUGIN_SDK.md#固定一次操作的服务实例)与 [Runtime Spec](../specs/runtime-v2/sakura-plugin-runtime-v4.md#51-显式绑定插件进程)。
不在日常使用的进程中故障注入。实际音频播放、麦克风识别和原生窗口分别体验、分别记录。

## 新建隔离环境

开发或自动回归仍可运行：

```powershell
runtime\python.exe scripts\prepare_plugin_acceptance.py
```

默认创建新的 `temp/plugin-acceptance-日期时间-随机标识`，已有目录拒绝写入；不使用或复制日常数据。
环境只准备默认 Assistant、“对话规则”和立绘加载所需材料，没有 API 配置，缺模型时应返回 `setup_required`。
准备脚本不编译程序，也不代表原生窗口或真实聊天通过。macOS/Linux 的 Python 路径为 `runtime/bin/python`。
