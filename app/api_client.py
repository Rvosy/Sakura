from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.chat_reply import ChatReply, parse_chat_reply
from app.env_config import load_env_file, save_env_values


SEGMENTED_REPLY_INSTRUCTION_TEMPLATE = """
你必须只返回 JSON，不要使用 Markdown 代码块，不要输出额外解释。
JSON 格式如下：
{"segments":[{"ja":"日文原文","zh":"中文译文","tone":"中性"}]}

分段规则：
- 尽量输出 2-4 段文本，每段是一条可以单独显示的完整小消息，不要把一句话机械切碎。
- 单段建议 35-90 个中文或日文字符；内容需要完整自然，宁可少分段也不要短到像碎片。
- 如果用户只问很简单的问题，可以只输出 1-2 段。
- 需要对每段文本的语气进行标注，语气标签放在 tone 字段中。
- tone 只能从这些类别中选择：{tones}。
- ja 中只写夜乃桜要说出口的日文原文，必须是日语，适合直接交给日语 TTS 朗读。
- zh 中只写 ja 对应的自然中文译文，必须是中文，不要添加解释、括号动作、语气标签或额外内容。
- 无论用户使用什么语言，ja 和 zh 都必须同时输出；不要只输出其中一种语言。
- ja 和 zh 必须一一对应；不要为了翻译改变 ja 的角色语气或内容。
"""


class ApiConfigError(RuntimeError):
    """API 配置缺失或格式错误。"""


class ApiRequestError(RuntimeError):
    """API 请求失败。"""


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60

    @classmethod
    def load(cls, env_path: Path) -> "ApiSettings":
        values = load_env_file(env_path)

        base_url = (
            os.getenv("BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or values.get("BASE_URL")
            or values.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = (
            os.getenv("API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or values.get("API_KEY")
            or values.get("OPENAI_API_KEY")
            or ""
        )
        model = (
            os.getenv("MODEL")
            or os.getenv("OPENAI_MODEL")
            or values.get("MODEL")
            or values.get("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        timeout_text = (
            os.getenv("API_TIMEOUT_SECONDS")
            or values.get("API_TIMEOUT_SECONDS")
            or "60"
        )

        try:
            timeout_seconds = int(timeout_text)
        except ValueError:
            timeout_seconds = 60

        return cls(
            base_url=base_url.strip().rstrip("/"),
            api_key=api_key.strip(),
            model=model.strip(),
            timeout_seconds=timeout_seconds,
        )

    def save(self, env_path: Path) -> None:
        """将聊天 API 配置写入 .env，并保留其他配置项。"""
        save_env_values(
            env_path,
            {
                "BASE_URL": self.base_url.strip().rstrip("/"),
                "API_KEY": self.api_key.strip(),
                "MODEL": self.model.strip(),
                "API_TIMEOUT_SECONDS": str(self.timeout_seconds),
            },
        )


class OpenAICompatibleClient:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def update_settings(self, settings: ApiSettings) -> None:
        """运行时更新 API 配置，供设置界面保存后立即生效。"""
        self.settings = settings

    def test_connection(self) -> str:
        """发送一次最小聊天请求，验证 Base URL、API Key 和模型是否可用。"""
        self._ensure_chat_config("缺少 API_KEY。请在设置中填写 API Key。")

        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with only OK.",
                },
            ],
            "temperature": 0,
            "max_tokens": 8,
        }
        data = self._post_chat_completions(payload)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        return str(content).strip() or "OK"

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        reply_tones: list[str] | None = None,
    ) -> ChatReply:
        segmented_reply_instruction = _build_segmented_reply_instruction(reply_tones)
        content = self.complete_raw(
            f"{system_prompt.strip()}\n\n{segmented_reply_instruction}",
            messages,
            temperature=0.8,
        )

        return parse_chat_reply(content)

    def complete_raw(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.8,
    ) -> str:
        """返回模型原始文本，供 Agent Runtime 解析工具调用 JSON。"""
        self._ensure_chat_config("缺少 API_KEY。请在 .env 中配置 API_KEY、BASE_URL、MODEL。")

        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt.strip(),
                },
                *messages,
            ],
            "temperature": temperature,
        }
        data = self._post_chat_completions(payload)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        return str(content).strip()

    def _ensure_chat_config(self, api_key_message: str) -> None:
        if not self.settings.api_key:
            raise ApiConfigError(api_key_message)
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")
        if not self.settings.model:
            raise ApiConfigError("缺少 MODEL。")

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调用 OpenAI 兼容的 chat/completions 接口并返回 JSON 数据。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.settings.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.timeout_seconds,
            ) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ApiRequestError(f"API HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise ApiRequestError(f"API 请求失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ApiRequestError("API 请求超时。") from exc

        try:
            data: dict[str, Any] = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{response_body}") from exc

        return data


def _build_segmented_reply_instruction(reply_tones: list[str] | None) -> str:
    tones = [tone.strip() for tone in reply_tones or [] if tone.strip()]
    if not tones:
        tones = ["开心", "中性", "温柔", "甜蜜", "害羞"]
    return SEGMENTED_REPLY_INSTRUCTION_TEMPLATE.strip().replace("{tones}", "、".join(tones))

