# OpenAI Official

登录 ChatGPT 账号后，把账号可用的 Codex 模型用于 Sakura 的对话、图片和工具调用。使用账号的 Codex 额度，无需 API Key 或 Codex CLI。

## 使用

1. 保持“OpenAI 兼容模型”基础插件启用，在“设置 → 插件”中从文件夹安装本插件，启用并应用。主程序需包含公共 SDK 的 `sakura_model_provider` 和 `sakura_openai_responses` 模块。
2. 打开“模型服务 → 添加模型服务”，选择“OpenAI Official”。每次选择都会新建一条连接，可在右侧修改名称。点击“登录 ChatGPT”，复制登录代码到浏览器打开的登录页。
3. 登录成功后自动获取并保存全部可用模型，无需勾选或手工填写模型 ID。同步失败可在账号面板重试。
4. 到“模型”页把对话模型换成 Codex 模型。视觉对话或其他插件用途也在此选择。

设备码登录需要在 ChatGPT 的安全设置或工作区权限中启用，见 [OpenAI 登录文档](https://developers.openai.com/codex/auth#preferred-device-code-authentication-beta)。登录代码按服务端返回的有效期等待，未指定时为 15 分钟。取消、切换服务、离开模型服务页或关闭设置会结束本次等待。

模型同步不会替换已选择的模型用途。温度、Top P、最大输出 Token 和 JSON 输出格式参数不发送给订阅接口；模型仍会收到完整的消息与工具定义。

## 账号与网络

每条连接分别保存 ChatGPT 账号和模型目录，可以同时配置多个账号。退出登录或删除一条连接不影响其他连接；删除后，仍引用它的模型用途显示不可用，等待用户重新选择。新连接的凭据保存在插件私有数据目录的 `accounts/<profileId>.json`，已有单账号连接保留原来的 `auth.json` 和模型引用。不读取或改写 Codex CLI 的登录文件。

访问令牌到期时自动续期；有效期未知时先使用现有令牌。服务明确返回 401 时，续期后重试一次；网络中断、额度不足或服务故障不会自动重发生成请求。退出登录删除本插件的凭据，保留模型目录和用途。

登录连接 `auth.openai.com`，模型目录和请求连接 `chatgpt.com/backend-api/codex`，沿用 Sakura 的系统代理。403 时检查账号和模型权限，429 时检查订阅额度；登录过期且续期失败时重新登录。该订阅接口与公开 API 的兼容范围不同，接口变化时可能需要更新插件。

## 接入边界

插件负责账号、模型目录、订阅地址和参数差异。公共 SDK 的 `ModelProvider` 负责调用者隔离、任务、取消与 Artifact 交付，`sakura_openai_responses` 负责消息转换和流式结果；HTTP 与 SSE 解码使用 OpenAI SDK。主程序和其他插件继续使用公共 `ModelClient`。

模型服务工作台及扩展的依赖关系见[模型服务规格](../../../docs/specs/model-services.md#配置归属)。

实现参考 [MaiBot Codex 订阅插件](https://github.com/ChenXi2542/maibot-codex-subscription)、[OpenCode 的 OpenAI 供应商](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/plugin/provider/openai.ts)和 [pi 的共享 Responses 协议](https://github.com/earendil-works/pi/blob/main/packages/ai/src/api/openai-responses-shared.ts)。名称、OpenAI 图标和登录交互参考 [cc-switch](https://github.com/farion1231/cc-switch)。图标许可见[资源许可](../../../desktop/frontend/assets/providers/openai.LICENSE)。
