---
title: 角色包制作
description: 通过角色工坊编辑角色并导出 .char 包。
---

从“设置 → 角色与布局 → 修改角色”打开角色工坊，可以编辑已有角色或新建角色。填写名称与人设，添加形态资源、主题颜色和需要的语音资源。新角色点击“添加到角色列表”，已有角色点击“保存”。

编辑内容会先保存为草稿，之后可以继续修改。导出的 `.char` 文件用于分享和导入；单独分享形态使用 `.visual`，语音资源使用 `.voice`。语音开关与引擎选择保存在本机，导入者需要自行配置。

- [角色导入与首次配置](https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/SETUP.md#添加角色)
- [角色工坊的保存、草稿与导出契约](https://github.com/Rvosy/Sakura/blob/main/docs/specs/runtime-v2/character-studio.md)
- [表现资源与插件接口](https://github.com/Rvosy/Sakura/blob/main/docs/specs/runtime-v2/visual-plugin-boundary.md)

表现资源的格式由对应插件定义；开发自定义表现时，按插件接口准备资源和编辑器。
