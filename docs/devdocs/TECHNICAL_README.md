---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-26
---

# Sakura 技术讲解 README

Sakura 当前只有 Runtime v2 一条产品链：Tauri 负责桌面窗口和平台能力，bundled Python Core 负责
Assistant 与数据领域，Plugin API v3 Worker 负责隔离加载插件。历史 Qt 应用已经退役；需要查看迁移前
行为时直接使用 Git 历史。

```text
Tauri Shell -> Python Core Host -> Plugin API v3 Worker
```

## 运行边界

### Tauri Shell

`desktop/src-tauri/` 是唯一桌面生命周期根，拥有透明桌宠窗口、设置窗口、托盘、窗口命中与拖动、截图、
音频播放、Core 进程监管和退出排水。`desktop/frontend/` 只通过经过授权的 command/event 与原生层交互。

### Python Core Host

`python -m app.core_host` 启动无窗口 Core。它负责：

- Provider、角色、聊天和类型化 Timeline；
- AgentRuntime、上下文、Memory、Tools 与 MCP；
- TTS 合成与语音留存；
- Plugin Worker 生命周期和设置 DTO；
- 配置、旧数据迁移与运行日志桥接。

Core 的 stdout 只传协议帧，诊断经 stderr/Core bridge 交给 Rust 单写者。Core 不创建窗口，也不依赖 Qt。

### Plugin API v3 Worker

`app/plugins/kernel.py` 和 `app/core_host/plugin_worker_runtime.py` 构成 generation 私有插件运行环境。插件
只通过显式 Host Services 注册 Tool、Context、Settings、Artifact 等贡献；旧 PluginManager 和 Qt 设置面板
不属于当前 API。

## 启动流程

最终产品直接启动平台 Runtime v2 可执行文件。源码开发时可使用 `main.py` 定位已构建 Shell：

1. `main.py` 或平台脚本定位 `desktop/src-tauri/target/{release,debug}` 下的 Shell。
2. Shell 获取单实例锁并创建桌宠窗口。
3. Supervisor 从固定 Runtime 布局启动 bundled Python Core。
4. Core 读取配置和角色，完成协议握手并发布 readiness/Snapshot。
5. WebView 根据 Snapshot 展示聊天、设置和角色表现。
6. 退出时 Shell 依次排空事件、停止 Core 和完整后代进程树，再释放应用锁。

## 目录结构

```text
.
├── main.py                         # Runtime v2 开发启动兼容入口
├── desktop/
│   ├── frontend/                   # 桌宠与设置 WebView
│   └── src-tauri/                  # 唯一桌面生命周期根和平台 backend
├── app/
│   ├── agent/                      # AgentRuntime、Tools、Context、Memory 协作
│   ├── config/                     # 配置 DTO、reader、migration
│   ├── core/                       # 无窗口共享生命周期与聊天领域
│   ├── core_host/                  # IPC Server、Router、真实聊天与插件边界
│   ├── llm/                        # Provider 客户端与 Prompt Runtime
│   ├── plugins/                    # Plugin API v3 discovery/inventory/kernel
│   ├── storage/                    # Timeline、历史、原子写和路径
│   └── voice/                      # TTS 合成、服务监督、语音留存
├── plugins/                        # bundled Plugin API v3 插件
├── tools/
│   └── studio-tauri/               # 角色工作室
├── harness/                        # 产品能力验证入口
└── tests/                          # Python 单元与跨模块集成测试
```

旧安装的数据 parser、migration 和冻结 fixture 属于 Runtime v2 升级能力。它们可以读取历史格式，但不得
启动第二个应用、创建 UI 或写入真实 `data/`。测试必须使用显式临时应用根。

## 常用验证

Windows：

```powershell
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run core-host
runtime\python.exe -m harness run runtime-v2-shell
runtime\python.exe -m harness run python-full
```

macOS/Linux：

```bash
runtime/bin/python -m harness run smoke
runtime/bin/python -m harness run core-host
runtime/bin/python -m harness run runtime-v2-shell
runtime/bin/python -m harness run python-full
```

Rust 格式化从 manifest 执行：

```bash
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
```

## 维护原则

- 用户数据、角色包、Runtime 和构建产物不属于源码清理范围。
- 数据兼容通过 parser、migration 和 fixture 证明，不通过维护历史应用证明。
- 新能力只接入当前三层边界，不增加第二桌面根或兼容宿主。
- 可接受故障明确返回并由用户重试；不为假设场景增加自动治理或双实现。
