---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-04
---

# ADR-0044：角色工坊使用主 Tauri 应用内的独立窗口

## 背景

0.9.10 的角色工坊前端和 `CharacterStudioService` 仍可使用，但窗口由单独的 `sakura-studio` Tauri 二进制
承载，再通过 Python `QProcess` 和 stdout marker 调用服务。Runtime v2 已退役 Qt 桌面根；恢复这条链会产生
第二个应用生命周期、第二份 Rust crate 和一套只供工坊使用的进程协议。

角色编辑仍需要普通桌面窗口、原生文件选择、多显示器取色和大文件处理。把这些能力塞进设置页会让设置
dirty 状态、角色草稿和窗口关闭确认互相干扰。

## 决策

角色工坊保留独立窗口，但窗口属于 Sakura 主 Tauri 进程，label 固定为 `studio`，同一时间最多一个。设置页
通过类型化命令打开；重复请求只聚焦现有窗口。

Python Core 是角色数据的唯一写入 owner。Rust `studio_request` 使用固定方法白名单，把 schema v1 请求转给
当前 generation；WebView 和 Rust 不直接改写角色目录。文件选择产生的绝对路径只作为一次 Core 请求参数，
角色路径不会返回 WebView。

参考语音由 Core 验证，Rust 注册短期不透明 URL。取色由主进程创建每显示器覆盖层并在 Rust 内读取屏幕像素。
两类数据都不走旧 Qt 辅助程序。

发布当前角色沿用角色切换已经使用的 Core generation 重建。发布非当前角色只刷新目录。保存事务先完成，
运行态重载随后执行；重载失败不能撤销已经提交的角色包。

`tools/studio-tauri` crate 和旧进程桥删除。历史归档仍可作为 0.9.10 交互依据，但不再参与构建或运行。

## 备选方案

继续运行 `sakura-studio` 子进程可以少改旧前端宿主，但要保留 Python 进程桥和第二个 Tauri 生命周期。它还会
重复处理单实例、退出、Core generation 和资源授权，因此不采用。

把工坊做成设置页中的一个 section 可以少一个窗口。角色工坊有长时间资源复制、独立草稿和关闭保护，设置页
还有全局配置草稿，两者共用提交按钮会扩大状态组合，因此不采用。

恢复 PySide Studio 能直接复用旧取色器和窗口管理，但会重新把 Qt 带回发布包，与 ADR-0034 的单一桌面根冲突。

## 后果

主程序统一管理设置、工坊、桌宠置顶和退出顺序，也可以直接复用现有 Core restart。代价是主 Tauri crate 需要
维护工坊窗口状态、试听协议和取色覆盖层；这些代码只服务当前工坊，不抽成通用窗口框架。

Windows 和 macOS 的 WebView、屏幕权限、DPI 与多显示器行为仍需实机验收。自动测试通过不能替代这部分记录。
产品合同见 [Runtime v2 角色工坊](../specs/runtime-v2/character-studio.md)。
