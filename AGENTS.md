# Sakura 开发指南

通用协作规则沿用全局 `AGENTS.md`，本文件保留 Sakura 的补充要求和开发入口。

## 项目约束

- 界面修改遵循[设计规范](docs/development/ui-design.md)的适用范围。设置页、角色工坊、聊天记录和运行日志按各自用途设计，保留未保存输入。
- 不自行添加 SHA-256 等哈希校验，也不换算法保留同类机制；仅沿用外部协议、签名或认证明确要求的哈希。
- 非关键清理失败不阻断无关流程。诊断入口完整保留原始错误，不用通用文案、重复包装或截断遮住原因。
- 测试不以大段快照代替行为验证，不向生产代码添加兼容逻辑迎合旧测试。只补重要行为缺口和真实故障的最小回归，不为覆盖率或数量凑用例。
- 项目文档不保留临时实施阶段、进度、验收过程或作废决策；不新增计划、交接、决策历史文档，也不另存历史版。历史由 Git 保留。

## Git 规范

- 提交标题使用 `<type>: <中文说明>`，半角冒号后留一个空格，例如 `fix: 修复角色切换异常`。
- 常用 type：`feat` 新功能、`fix` 修复、`refactor` 重构、`perf` 性能、`docs` 文档、`test` 测试、`build` 构建、`ci` 持续集成、`chore` 维护。
- 工作分支使用 `<type>/<简短英文描述>`，描述用小写英文和连字符，例如 `feat/character-import`、`fix/character-switch`。
- 一次提交只处理一个完整目的，提交前检查差异，只暂存相关文件或代码块。
- PR 标题沿用提交格式，正文用中文说明改动和验证结果；按任务授权提交、推送和合并。

## 开发与验证入口

文档见[文档总览](docs/README.md)，验证见 [Harness](harness/README.md)。

使用项目自带 Python：Windows 为 `runtime\python.exe`，macOS/Linux 为 `runtime/bin/python`。下列 `python` 替换为对应路径，按改动选择 profile 或测试：

```
python -m harness list
python -m harness run <profile>
python -m pytest -q <相关测试文件>
```
