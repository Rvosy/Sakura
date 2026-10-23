# 本地界面预览

本目录提供独立浏览器演示，数据只保存在页面内存中，不读取用户目录，也不执行真实安装、模型请求或语音服务。正式能力见[使用指南](../../../docs/README.md)。

在仓库根目录启动：

```powershell
runtime\python.exe -m http.server 8770 --bind 127.0.0.1 --directory desktop/frontend
```

macOS / Linux 改用 `runtime/bin/python`。打开对应地址：

| 路径 | 演示内容 |
|---|---|
| `/prototypes/asr/` | 录音状态、模拟识别与草稿回填 |
| `/prototypes/icon-motion/` | 输入栏、发送和语音状态的动效 |
| `/prototypes/legacy-data-import/` | 目录选择、冲突确认和迁移结果 |
| `/prototypes/marketplace/` | 插件目录、详情、模拟下载与安装 |
| `/prototypes/plugins/` | 插件列表、筛选、设置和资源状态 |
| `/prototypes/settings-demo/` | 角色显示方式与设置草稿 |
| `/prototypes/studio-demo/` | 多形态编辑、保存与设置预览 |

示例中的 Live2D、3D、插件条目和字段只用于展示。演示不定义公开接口，角色工坊和表现资源约定见[角色开发文档](../../../docs/development/characters.md)。浏览器预览不能验证原生窗口、点击穿透或系统材质。

设置动效预览直接加载正式设置页和内置插件前端，需要从仓库根目录提供文件。在仓库根目录运行 `runtime\python.exe -m http.server 8771 --bind 127.0.0.1`，打开 `/desktop/frontend/prototypes/settings-motion/`。顶部可逐项定位动效，并切换原有动效和补充动效。模型连接使用模拟请求和等待、成功、失败图标。语音测试在用户点击后请求麦克风权限，将实际音量交给主窗口的波形绘制器；音频不保存、不上传，预览不运行语音识别。
