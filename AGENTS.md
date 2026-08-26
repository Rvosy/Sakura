# Sakura Agent Guide

## Goal

先理解现有设计，再解决用户的实际问题和根因。允许跨模块调查、修改与重构；不要为了满足预设文件范围
制造 workaround，也不要顺手扩大到无关业务。

## Repository

- `main.py`：Runtime v2 开发启动兼容入口。
- `app/`：无窗口 Python Core、Assistant、存储和领域实现。
- `desktop/`：Runtime v2 的 Tauri/Rust/WebView 桌面应用。
- `plugins/`、`app/plugins/`：插件实现与插件系统。
- `characters/`、`data/`：角色包、配置和运行数据。
- `docs/specs/`：当前必须保持的产品行为和不变量。
- `docs/adr/`：重要架构决策、替代方案与取舍。
- `harness/`：按产品能力组织测试并生成 JSON 报告。
- `tests/`：Python 单元、集成、UI 和固定测试夹具。
- `third_party/`、`tools/mcp/`：第三方或外部工具代码。

## Before changing code

按任务需要阅读相关代码、Spec 和 ADR。优先从真实调用链和现有测试确认行为，不要求遍历无关文档。

## Engineering Simplicity

Sakura 是以个人维护为主、用户规模较小的项目。设计和实现应优先考虑可理解、易修改和低维护成本，而不是企业级完备性。

* 复杂度必须解决当前真实存在的问题，不要为假设中的未来需求提前建设机制。
* 可以保留保护数据、进程、线程和用户体验的必要保险丝，但避免建立复杂的自动治理、自愈、权限、兼容和状态管理体系。
* 对可接受的异常，优先选择“明确失败 + 清晰报错”，而不是自动重试、降级、恢复或切换。
* 能通过简单重启、重新加载或直接调用解决的问题，不要为了热更新、动态协调或抽象统一引入大量基础设施。
* 少量重复代码通常比错误的抽象更便宜。
* 不要仅因为“最佳实践”“更通用”“更安全”或“以后可能需要”增加复杂度。

核心原则：**架构用于提供必要的能力边界，保险丝用于防止真实损失；除此之外，保持简单。**


## Implementation

- 可以跨模块调查和修改真正拥有根因的代码。
- 保持改动聚焦，避免无关格式化、重命名或业务重写。
- 尊重工作树中已有修改，不覆盖或还原用户工作。
- 新抽象应服务当前真实消费者，不为假设中的未来需求建设框架。

## Documentation

- 长期产品行为、不变量、公共接口或兼容契约变化时，更新对应 Spec。
- 产生新的重要架构取舍时，新增 ADR；普通 Bug、UI 调整和小型重构通常不需要 ADR。
- 普通实现缺陷以修复和 regression test 为主；Plan、Record 只在大型分阶段工作、发布、事故或确有历史
  证据价值时使用。

## Verification

先运行受影响能力的 focused tests，再按风险选择 Harness profile 或 journey。Harness 入口：

```text
python -m harness list
python -m harness run smoke
python -m harness run <profile>
```

Windows 使用 `runtime\python.exe`，macOS/Linux 使用 `runtime/bin/python`。CI 负责完整矩阵；本地无需为
无关层级重复运行全部测试。无法执行的平台或设备验证应明确报告为未验证及其风险。

## Safety

- 不泄漏凭据、私密配置或真实用户数据。
- 不执行未经请求的破坏性 Git 操作、force push、生产部署或不可逆数据迁移。
- 修改角色资源、用户数据、二进制或第三方代码前，确认它们确实属于任务范围并保留兼容与回退能力。
