---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-20
---

# Runtime v2 Python 插件

Runtime v2 使用 Plugin API v3。插件在当前 Core generation 的私有 Plugin Worker 中运行；插件损坏、
不兼容或调用超时不会把普通聊天和 Core 控制一起卡住，Sakura 会按已保存的启停状态重建 Worker。

Plugin Worker 不是安全沙箱。插件仍以你的账户权限访问文件、网络和本机资源，请只安装可信来源的代码。

## 安装本地插件

1. 打开“设置 → 插件”。
2. 选择“安装 ZIP”或“安装文件夹”。ZIP 可以直接包含 `plugin.yaml`，也可以只有一层包装目录。
3. Sakura 会检查 Plugin API 版本、ID 冲突、路径与文件边界，然后把代码复制到用户插件目录。
4. 新插件安装后默认禁用，不会在安装过程中执行。检查名称、作者和来源后，再打开开关并保存。

Runtime v2 只安装 `api: 3` 插件，不会自动下载 Python 依赖，也不提供市场、在线更新或版本求解。安装包
不得包含符号链接、junction、特殊文件、路径逃逸或跨平台非法文件名。

插件代码与运行数据彼此分离：

```text
data/user_plugins/<plugin_id>/   用户安装的插件代码
data/plugins/<plugin_id>/        插件私有配置、数据库和缓存
```

详情页中的“卸载插件”只对用户安装的插件显示。卸载会删除代码并保留私有数据；Sakura 内置插件不能在此
卸载。目前设置页不提供删除私有数据的动作。

## 启用、禁用与详细设置

1. 在插件列表中修改开关。
2. 按需修改插件声明的字段、Action 或 Collection。设置页只渲染 Sakura 支持的声明式控件，不加载插件
   提供的网页、JavaScript 或 Qt 控件。
3. 点击“应用”或“保存”。启停会在当前 Plugin Worker 内生效，不会重启 Python Core。

插件配置保存后可能显示：

- `applied`：当前运行对象已经应用；
- `restart_required`：配置已保存，需要点击插件提供的“重新加载插件”；
- `error`：配置已保存，但当前运行对象未应用，应根据原因码修正或重载。

保存失败时，页面会尽量保留尚未提交的草稿。`CONFIG_REVISION_CONFLICT` 表示另一个设置窗口或操作已经
修改配置；刷新到当前状态后再重试。

## 状态说明

- `disabled`：已安装但未启用；安装新插件后的初始状态。
- `waiting`：缺少 required Service；Provider 出现后会自动尝试激活。
- `active`：setup 完成，Service 和 Effects 已整体发布。
- `failed`：manifest、导入、setup、依赖环或恢复失败。
- `conflict`：存在同名 Service Provider 冲突。
- `starting` / `ready` / `degraded` / `stopping` / `stopped`：Plugin Worker 的初始化、可用、降级或关闭状态。

常见原因码：

- `API_VERSION_UNSUPPORTED`：manifest 不是 `api: 3`；插件代码不会被导入。
- `MISSING_SERVICE`：缺少 required Service。
- `SERVICE_CONFLICT` / `DEPENDENCY_CYCLE`：Service 唯一性冲突或 required dependency 环。
- `PLUGIN_DISABLED`：插件按已保存状态保持禁用。
- `PLUGIN_CALL_TIMEOUT` / `PLUGIN_CALLBACK_TIMEOUT` / `PLUGIN_WORKER_EOF`：调用超时或 Worker 退出。原调用
  不会自动重放，避免重复执行未知副作用。
- `PLUGIN_ID_CONFLICT`：插件 ID 与内置或已安装插件重复。
- `PLUGIN_INSTALL_*`：安装包的来源、布局、路径、文件类型或大小不符合边界。
- `PLUGIN_INSTALL_ROLLBACK_FAILED` / `PLUGIN_UNINSTALL_ROLLBACK_FAILED`：运行时应用失败后的代码或配置恢复
  不完整；Sakura 会保持禁用或隔离残留代码，重启前不要手工移动这些目录。
- `PLUGIN_INSTALL_RECOVERY_FAILED` / `PLUGIN_UNINSTALL_RECOVERY_FAILED`：事务回滚后 Plugin Worker 仍未恢复；
  重启 Sakura 后再检查插件状态。
- `PLUGIN_UNINSTALL_CLEANUP_FAILED`：插件已停止且不再被发现，但隔离区中的代码残留未能删除。
- `BUNDLED_PLUGIN_LOCKED`：尝试卸载 Sakura 内置插件。

公开设置状态和统一运行日志不会包含插件入口、本地安装路径、数据路径、私有配置、消息正文、工具参数或
插件异常正文。排查运行问题可参阅[统一运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)。
