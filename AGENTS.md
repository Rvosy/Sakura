# Sakura 开发指南

通用协作、表达、反过度防御与 Git 规则沿用全局 `AGENTS.md`，本文件仅保留 Sakura 专属约定与入口。

## 项目约束

- 主程序尽量单薄，具体能力优先通过插件扩展。
- 界面修改遵循 [设计规范](docs/development/ui-design.md) 的适用范围；设置页、角色工坊、聊天记录和运行日志按各自用途设计，保留未保存输入。
- 通过 PR 合并到 `main`，PR 标题沿用提交格式，正文用中文说明改动和验证结果。

## 开发与验证入口

文档见 [文档总览](docs/README.md)，验证见 [Harness](harness/README.md)。

使用项目自带 Python：Windows 为 `runtime\python.exe`，macOS/Linux 为 `runtime/bin/python`。下列 `python` 替换为对应路径，按改动范围运行局部检查或测试：

```
python -m harness list
python -m harness run <profile>
python -m pytest -q <相关测试文件>
```
