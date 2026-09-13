---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-14
---

# 开放插件生态初版验收

日常验收使用已有角色包、模型配置和默认 Assistant，检查插件自己的“对话规则”上下文设置。
`f9fde091` 引入的主设置“互动方式”已撤回。旧 `chat_executor` 字段直接忽略，无需手工删除；
“专注陪伴”保留为开发样例，暂不能从聊天框选择。安装或启用它也不会切换聊天实现。

## 使用已有角色包和配置

先从托盘菜单退出正在运行的 Sakura，再运行仓库中的
[`scripts/start-existing-data.bat`](../../scripts/start-existing-data.bat)。
Windows 默认使用 `%LOCALAPPDATA%\Sakura Development`，这是仓库开发版的日常数据目录。
原来的角色选择、角色包、模型配置、插件开关和聊天记录都从这个目录读取；启动器不创建验收角色，也不重写这些配置。

如果平时使用的是安装版，Windows 安装版的数据位于其程序目录，可明确传入那个已有目录：

```powershell
scripts\start-existing-data.ps1 -UserRoot 'D:\Sakura'
```

启动器只为本次进程指定数据目录。聊天和用户主动保存的设置会直接写入所选目录，它们就是日常使用的数据。
当前正常聊天始终使用默认 Assistant；已有有效模型配置可以继续使用，缺少模型配置时仍需完成配置。

## 默认对话与插件上下文

先发一条普通消息，确认当前角色、模型对话和既有表现正常。主设置中应没有“互动方式”、执行器选择或预设的 Agent 模式。

在插件管理中查看“对话规则”。尚未安装时，可按[插件 README](../../plugins/optional/context_rules/README.md)打包并安装。
打开插件自己的设置，查看“上下文”字段。验收不会自动改写这段文字：已有内容、已有空白和新安装时的默认文本均按原值保留。
需要使用它时由用户启用插件，再发送一条适合当前上下文的问题，检查默认对话是否采用所填内容。

上下文可以同时包含要求与资料，不需要选择用途分类。模型输出有波动时，结合本次请求的 Trace 检查实际内容；
不要仅凭某一句回复断言插件没有生效。停用再启用插件后，设置文本应保持原值，其他插件和角色选择不应变化。

清空、改写文本或旧版配置合并的回归在隔离环境中完成，不为验收覆盖日常使用的内容。
“专注陪伴”不消费 Context，计时结果不能用来判断“对话规则”是否生效。

## 新建隔离验收环境

隔离环境供开发与自动回归使用。在 Windows 仓库根目录运行：

```powershell
runtime\python.exe scripts\prepare_plugin_acceptance.py
```

脚本默认创建 `temp/plugin-acceptance-日期时间-随机标识`。也可以用 `--user-root` 指定新目录；目录已存在时拒绝写入。
它安装“对话规则”并保持停用，只启用内置“立绘”，创建“Sakura 验收”角色和空 Timeline。
环境没有 API 配置，不预选执行器，也不安装计时样例。

脚本通过真实插件进程检查立绘加载；默认 Assistant 应返回 `setup_required/PROVIDER_SETUP_REQUIRED`。
这证明无模型时仍遵循默认聊天的配置要求，不表示聊天已经就绪。检查结束后关闭进程。
`acceptance-environment.json` 记录本次准备结果；未操作原生窗口时，`guiVerified` 保持 `false`。

需要手动查看这套空白环境时，先退出其他 Sakura 实例，再双击生成目录中的 `启动验收.bat`，
或用 PowerShell 运行同目录的 `启动验收.ps1`。启动器只对本次进程设置 `SAKURA_RUNTIME_USER_ROOT`，工作目录使用仓库根目录。
在其中体验模型对话，需要自行配置可用模型。

启动器使用 `desktop/src-tauri/target/debug/sakura.exe`。可执行文件缺失或需要更新时，在仓库根目录编译：

```powershell
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked
```

准备脚本和启动器不会自动编译。空白角色使用 Sakura 图标作为立绘；它只是检查资源加载的材料。
日常视觉、音频和聊天体验使用上面的已有数据入口验证，结果与自动测试分别记录。

## 执行服务开发回归

[专注陪伴样例](../../plugins/optional/focus_companion/README.md)和隔离测试继续验证真实计时、进度、取消、线程退出、
同操作重复受理、同服务重载及结果提交保护。这些检查由开发消费者显式绑定执行服务，不通过主设置、隐藏配置或插件开关接管聊天。
进度不写助手历史，最终结果只取得一次提交资格；停止未确认时保留占用，不能伪称任务已经退出。

`f9fde091` 的设置切换、无模型计时聊天和 Edge 设置页联接结果属于历史实现证据，不能作为当前产品入口的验收结果。
保留底层合同并不预定最终界面，也不自动启动默认模型或对话能力的进一步拆分。

相关方向见[开放插件生态总计划](../plans/runtime-v2/open-plugin-ecosystem.md)，插件接入见[Plugin API v4 开发指南](SAKURA_PLUGIN_SDK.md)。
