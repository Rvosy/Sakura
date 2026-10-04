---
kind: record
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-10-04
---

# SakuraTTS 整合包与插件联调

本记录保存发布前的本地联调结果，使用 SakuraTTS 0.1.0a1 源码与 Sakura 的可选插件 `sakura.tts.sakuratts`。插件尚未发布到插件市场。
后续 CI 构建的两平台整合包已上传[魔搭](https://modelscope.cn/models/SuzushimaArisu/SakuraTTS)，构建与验收结果见 [GitHub Actions 运行记录](https://github.com/Rvosy/SakuraTTS/actions/runs/37191031853)。
产品契约见 [插件 Spec](../../specs/runtime-v2/sakuratts-plugin.md)。

插件安装包为 Sakura 工作区的 `temp/sakuratts-acceptance-20261004/Sakura-SakuraTTS-0.1.0.sakplugin.zip`。它需要配合包含原生文件选择和对话开始事件的 Sakura 主程序代码；旧版主程序不具备这两个接入点。

## 预览文件

| 文件 | 保存位置 | 体积 |
|---|---|---|
| 插件安装 ZIP | Sakura 工作区 `temp/sakuratts-acceptance-20261004/Sakura-SakuraTTS-0.1.0.sakplugin.zip` | 约 12 KB |
| macOS Apple silicon 完整包 | SakuraTTS 工作区 `dist/macos-plugin-preview-release/SakuraTTS-macOS-plugin-preview.tar.gz` | 964,487,381 字节 |
| Windows x64 统一完整包 | `vit-admin-86`：`D:/SakuraTTS-build-20261004-unified/release2/SakuraTTS-Windows-x64-preview2.tar.gz` | 2,884,443,491 字节 |

安装插件并启用，在插件设置中导入对应平台的完整包，然后在语音页选择 SakuraTTS。整合包更新使用同一个导入入口。Windows 完整包包含 CPU、NVIDIA CUDA 和 DirectML；本次真实合成只验收了 CPU。

## macOS 实际运行

在 Apple silicon 本机从原始构建输入生成完整包，压缩包为 `SakuraTTS-macOS-plugin-preview.tar.gz`，964,487,381 字节，解压内容约 2.41 GB。包内包含 MLX、Python 和 CPU 准备组件，不包含角色发声权重或个人参考音频。

随包 `check-runtime --backend mlx` 通过 Metal 矩阵运算。插件在隔离数据目录执行真实导入，完成原始 GPT / SoVITS 转换、参考音频准备和两次合成。首次测试音频为 32 kHz、36,480 帧；本记录不作听感或性能结论。

为缩短生命周期验证，将空闲时间设为 2 秒。第一次合成后 `/runtime` 返回 `state=sleeping`、`model_loaded=false`、`worker_pid=null`；第二次合成成功。关闭插件后控制进程退出，模型缓存保存在版本目录外。这不代表 Windows CUDA 显存已验收。

本机原始结果保存在 `temp/sakuratts-acceptance-20261004/report.json`，音频为同目录的 `audio0.wav` 和 `audio1.wav`。构建输入、GPU 检查和归档结果在 SakuraTTS 工作区的 `outputs/unified-20261004/`。

## 自动化与原生边界

- 插件在隔离宿主加载，设置贡献和原生文件选择元数据可见。
- 离线导入失败、取消和非法路径不会替换旧环境；7z 在可取消子进程中解压。
- 取消合成后先停止写入，再释放产物；无需依赖调用方继续轮询。
- 对话开始通知失败不会阻止聊天；打开设置和启动应用不触发模型加载。
- 设置文件选择参数通过前端行为测试和 Tauri `cargo check`。尚未在打包后的桌面窗口点击验收。

首次聊天集成测试暴露通知异常阻止聊天的问题；在通知边界记录异常并继续聊天后，相关 48 项测试通过。后续补充了 7z 解压和安装器关闭后拒绝新任务的检查。前端控制器 22 项检查通过；整合包相关测试 33 项，其中 Windows bat 检查在 macOS 跳过。

## Windows 构建检查

首个统一包在 `vit-admin-86` 的 `D:/SakuraTTS-build-20261004-unified/` 装配，解压内容 5,470,522,855 字节。该候选包因后续首次合成失败而停止交付。
CPU 矩阵运算通过；Windows 运行环境测试 14 项通过，包含原先在 macOS 跳过的 bat UTF-8 检查。
managed HTTP 无模型启动返回 `sleeping`、`model_loaded=false` 和 `worker_pid=null`。

独立准备组件导入探针初次使用包根目录作为工作目录，导致上游 `sv.py` 按工作目录查找 `GPT_SoVITS/eres2net` 时失败。
产品准备入口 `prepare_resources.py` 会先切换到官方源码目录；探针按同一路径修正后导入通过，报告为 `torch 2.7.0+cpu`、`torch.version.cuda=None`。原始错误保留为构建目录的 `preparation-import-wrong-cwd.log`。

首次 CPU 合成在 GPT 导出阶段失败：`Your model ir_version 10 is higher than the checker's (9)`。上游整合包中的旧 ONNX 不支持当前导出器明确生成的 IR 10。准备组件构建入口已要求 `onnx>=1.16`，并增加依赖解析回归检查，拒绝 1.14.1、接受 1.16.2；准备组件相关 8 项测试通过。重建输入使用 ONNX 1.17.0，原候选目录和错误日志保留。

重建后首次合成成功，但 2 秒空闲配置下的休眠检查失败。原因是 `/runtime/wake` 默认追加 60 秒保活，首次合成结束时该期限尚未到达。插件改为显式传入 `keep_alive_seconds=0`，让休眠只遵循用户配置；接口回归检查覆盖此参数。原始结果保留为构建目录的 `keepalive60-speech-report.json` 和 `keepalive60-speech-server.log`。

修正保活参数后，Windows CPU 合成生成 32 kHz、33,920 帧音频，随后进入 `sleeping`，`model_loaded=false`、`worker_pid=null`。macOS 插件同样复验了合成、休眠、再次合成和关闭进程，结果为 Sakura 工作区的 `temp/sakuratts-acceptance-20261004/prewake-report.json`。Windows 使用合成参考音频验证运行链路，不作听感结论；本机保存的报告为 SakuraTTS 工作区的 `outputs/unified-20261004/windows-speech-report.json`。

最终 Windows 归档含 20,149 个文件，解压内容 5,471,519,105 字节。构建器完成归档内容检查；报告保存在 SakuraTTS 工作区的 `outputs/unified-20261004/windows-compression-report.json`。运行日志、模型与参考缓存不进入归档。

## 待验收

首轮 CPU 准备依赖下载被构建机网络以 Windows 错误 10013 拒绝；从本机获取官方相同版本 wheel、核对上游 wheel 摘要并离线传入后完成构建。CUDA、DirectML 真机合成与显存释放尚未验收。魔搭仓库、版本清单和下载地址已建立，插件尚未接入在线下载与检查更新。完整设计稿中的角色试听、管理弹窗和回退入口尚未接入。
