# Code signing policy

Sakura Desktop Pet 的公开发行包由 [GitHub Actions](https://github.com/Rvosy/Sakura/actions) 从仓库源码构建，通过 [Releases](https://github.com/Rvosy/Sakura/releases) 分发。

## 维护与发布

[Rvosy](https://github.com/Rvosy) 负责项目维护、外部贡献审核和发行批准。签名范围是项目自行构建的程序与安装包，第三方二进制文件保留上游签名或原始状态，不使用项目证书重新签名。

Sakura 正在准备申请 [SignPath Foundation](https://signpath.org/) 的免费代码签名服务，尚未接入。当前发布包是否已签名，以文件的实际数字签名为准。Tauri 更新签名用于验证更新包，不能代替 Windows 代码签名。

## 隐私与组件

数据发送范围、安装时的统计选项和关闭方式见[隐私政策](privacy.md)。

主安装包包含运行环境与内置插件，可选插件由用户另行安装。Sakura 自有代码采用 MIT 许可，第三方组件按各自许可分发；可选 Spine 插件中的运行库使用 [Spine Runtimes Software License](../../plugins/optional/sakura_spine/vendor/SPINE-LICENSE)，不属于项目 MIT 许可的授权范围。
