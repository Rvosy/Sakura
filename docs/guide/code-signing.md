# Code signing policy

Sakura Desktop Pet 的公开发行包由 [GitHub Actions](https://github.com/Rvosy/Sakura/actions) 从仓库源码构建，通过 [Releases](https://github.com/Rvosy/Sakura/releases) 分发。

## 维护与发布

[Rvosy](https://github.com/Rvosy) 负责项目维护、外部贡献审核和发行批准。签名范围是项目自行构建的程序与安装包，第三方二进制文件保留上游签名或原始状态，不使用项目证书重新签名。

Windows 构建通过 SignPath 提交代码签名。Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/)。正式证书当前为 CSR Pending，正式签名尚未启用；测试签名仅用于接入验证，不能作为可信发行包分发。配置与验证方法见[构建与发布](../development/release.md#signpath-配置)。

签名对象是 `sakura.exe` 和 NSIS 安装包。安装包内的主程序及 Portable 使用同一份已签名程序。NSIS 生成的卸载程序当前不单独签名。正式签名须经维护者在 SignPath 审批，构建流程不代替人工批准。Tauri 更新签名在安装包代码签名完成后生成，两者用途不同。

## 隐私与组件

数据发送范围、安装时的统计选项和关闭方式见[隐私政策](privacy.md)。

主安装包包含运行环境与内置插件，可选插件由用户另行安装。Sakura 自有代码采用 MIT 许可，第三方组件按各自许可分发；可选 Spine 插件中的运行库使用 [Spine Runtimes Software License](../../plugins/optional/sakura_spine/vendor/SPINE-LICENSE)，不属于项目 MIT 许可的授权范围。
