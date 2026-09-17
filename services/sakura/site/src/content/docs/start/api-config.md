---
title: API 配置
description: 在 Sakura 中填写 Base URL、API Key 和模型名称。
---

# API 配置

Sakura 使用 OpenAI 兼容接口连接大模型。首次配置只需要准备 3 个信息。

| 配置项 | 填什么 |
|---|---|
| `Base URL` | 服务商提供的 OpenAI 兼容接口地址，通常以 `/v1` 结尾 |
| `API Key` | 服务商生成的密钥 |
| `模型` | 服务商模型列表中的完整模型名称 |

Sakura 需要模型支持图像输入。屏幕观察、主动观察等功能会把截图发给模型；如果模型不支持多模态，相关功能会失败。新手优先选 Gemini Flash 系列，不建议用 DeepSeek 系列作为 Sakura 主模型。

## 使用中转站配置

下面以 OpenAI 兼容中转站为例。你也可以用其他服务商，只要能拿到 `Base URL`、`API Key` 和模型名称即可。

### 1. 注册账号

进入服务商网站并创建账号。可以先试用小额或免费额度验证配置是否可用。

### 2. 创建令牌

进入控制台的令牌管理页面。

![进入令牌管理](/images/gemai_01.webp)

点击新建，创建一个新的 API 令牌。创建时：

- 名称填一个容易识别的，例如 `sakura`
- 令牌分组保持默认即可
- 过期时间可以先选不过期，后续需要时再删除或重建
- 额度设置不确定就保持默认

![创建新的令牌](/images/gemai_02.webp)

创建完成后复制密钥。密钥通常以 `sk-` 开头。不要把完整密钥截图发到公开群、Issue 或社交平台。

![复制密钥](/images/gemai_03.webp)

### 3. 挑选模型

进入服务商的模型列表，优先选支持视觉输入的 Gemini Flash 系列。

![选择 Gemini Flash 模型](/images/gemai_04.webp)

示例模型名：

```text
[福利]gemini-3.5-flash
```

不同模型的计费方式和价格不同。桌宠聊天、主动观察、工具调用等场景都会请求模型，长时间运行会消耗额度。

### 4. 填写 Sakura 设置

打开 Sakura 的设置窗口，进入"模型"页面。

![配置模型](/images/setup_02.webp)

| 字段 | 示例 | 说明 |
|---|---|---|
| `Base URL` | `https://api.example.com/v1` | 填到 `/v1` 这一层，不要填到 `/chat/completions` |
| `API Key` | `sk-...` | 粘贴刚才复制的令牌密钥 |
| `模型` | `[福利]gemini-3.5-flash` | 填模型列表里的完整模型名称 |
| `超时` | `60 秒` | 保持默认即可 |

填写完成后点击"检测模型"。如果服务商暴露了 `/models` 接口，Sakura 会读取可用模型列表，你可以从下拉框中选择模型。

再点击"测试 API"。弹出测试成功提示后，点击保存。

## 验证配置

1. 在设置页点击"测试 API"，确认能返回成功提示。
2. 保存设置并回到主界面。
3. 发送一句普通消息，确认 Sakura 能回复。
4. 用一次屏幕观察相关功能，确认当前模型支持图片输入。

前三步成功、第四步失败的话，通常是模型不支持多模态。换一个支持视觉的模型即可。

## YAML 最小配置

如果从源码运行，也可以直接编辑 `data/config/api.yaml`：

```yaml
llm:
  base_url: https://api.openai.com/v1
  api_key: your_api_key_here
  model: gpt-4.1-mini
  timeout_seconds: 60
```
