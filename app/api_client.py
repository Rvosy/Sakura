from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.chat_reply import ChatReply, parse_chat_reply


SEGMENTED_REPLY_INSTRUCTION = """
你必须只返回 JSON，不要使用 Markdown 代码块，不要输出额外解释。
JSON 格式如下：
{"segments":[{"text":"回复片段","tone":"中性"}]}

分段规则：
- 按语义和情绪变化切分回复；没有明显变化时可以只返回 1 段。
- 单段建议不超过 45 个中文或日文字符。
- tone 只能从这些类别中选择：开心、中性、温柔、甜蜜、害羞。
- text 中只写角色要说的话，不要写语气标签。
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
        values = _load_env_file(env_path)

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
        _save_env_values(
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

    def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> ChatReply:
        if not self.settings.api_key:
            raise ApiConfigError("缺少 API_KEY。请在 .env 中配置 API_KEY、BASE_URL、MODEL。")
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")
        if not self.settings.model:
            raise ApiConfigError("缺少 MODEL。")

        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": f"{system_prompt.strip()}\n\n{SEGMENTED_REPLY_INSTRUCTION.strip()}",
                },
                *messages,
            ],
            "temperature": 0.8,
        }
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
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{response_body}") from exc

        return parse_chat_reply(str(content).strip())


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _save_env_values(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    saved_keys: set[str] = set()
    output_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            output_lines.append(raw_line)
            continue

        key = raw_line.split("=", 1)[0].strip()
        if key not in updates:
            output_lines.append(raw_line)
            continue

        if key in saved_keys:
            continue
        output_lines.append(f"{key}={_format_env_value(updates[key])}")
        saved_keys.add(key)

    for key, value in updates.items():
        if key not in saved_keys:
            output_lines.append(f"{key}={_format_env_value(value)}")

    path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")


def _format_env_value(value: str) -> str:
    if not value or any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
