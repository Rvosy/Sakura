"""GPT-SoVITS endpoint resolution and managed-runtime ownership adapter."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable
from urllib.parse import urlparse

from app.core.runtime_log import log_event
from app.voice.tts_contracts import (
    ResolvedTtsEndpoint,
    TtsError,
    TtsErrorCode,
    relative_reference_path,
)
from app.voice.tts_service import (
    TTSServiceSupervisor,
    _probe_gpt_sovits_http,
    _probe_tcp_port,
)
from app.voice.tts_settings import (
    DEFAULT_GPT_SOVITS_BASE_URL,
    GPTSoVITSTTSSettings,
)


def is_loopback_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class GptSovitsEndpointResolver:
    """Derive deployment from custom_base_url; no persisted mode exists."""

    def __init__(
        self,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        resource_manager: object,
        is_closed: Callable[[], bool],
    ) -> None:
        endpoint = ResolvedTtsEndpoint(
            settings.custom_base_url or DEFAULT_GPT_SOVITS_BASE_URL,
            settings.tts_path,
            "custom" if settings.custom_base_url is not None else "managed",
            settings.custom_base_url is None,
        )
        self.settings = replace(settings, api_url=endpoint.synthesis_url)
        self._endpoint = endpoint
        self._custom_checked = False
        self._is_closed = is_closed
        self._runtime = None
        if settings.custom_base_url is None:
            self._runtime = TTSServiceSupervisor(
                self.settings,
                base_dir=base_dir,
                resource_manager=resource_manager,
                is_closed=is_closed,
                adopt_existing_service=False,
            )

    @property
    def endpoint(self) -> ResolvedTtsEndpoint:
        return self._endpoint

    @property
    def runtime(self) -> TTSServiceSupervisor | None:
        return self._runtime

    def ensure_available(self, fail: Callable[[str], None]) -> bool:
        if self._is_closed():
            return False
        if self._runtime is not None:
            return self._runtime._ensure_service_available(fail)
        if self._custom_checked:
            return True
        parsed = urlparse(self.endpoint.base_url)
        host = parsed.hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        if not host:
            fail("INVALID_CONFIGURATION: 自定义 GPT-SoVITS 服务地址无效。")
            return False
        port = port or (443 if parsed.scheme == "https" else 80)
        timeout = min(self.settings.timeout_seconds, 3)
        started = time.perf_counter()
        if not _probe_tcp_port(host, port, timeout):
            fail("CONNECTION_FAILED: 无法连接自定义 GPT-SoVITS 服务。")
            return False
        if not _probe_gpt_sovits_http(self.endpoint.synthesis_url, timeout):
            fail("RUNTIME_UNAVAILABLE: 自定义 GPT-SoVITS HTTP 服务尚未就绪。")
            return False
        self._custom_checked = True
        log_event(
            "TTS",
            "GPT-SoVITS endpoint ready",
            {
                "provider": "gpt-sovits",
                "endpoint_kind": "custom",
                "endpoint": self.endpoint.base_url,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
            event="tts.endpoint.ready",
        )
        return True

    def ensure_character_weights(self, fail: Callable[[str], None]) -> bool:
        # Model files on a custom endpoint are owned by its operator.
        if self._runtime is None:
            return True
        return self._runtime._ensure_character_weights(fail)

    def restart_owned_after_http_failure(self, status_code: int, body: str) -> bool:
        if self._runtime is None:
            return False
        return self._runtime._restart_local_service_after_http_failure(status_code, body)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()


class GptSovitsEndpointSupervisor:
    """Compatibility surface consumed by the existing synthesis queue.

    Runtime operations are delegated only for a managed endpoint.  Custom
    endpoints expose the same protocol surface without process ownership.
    """

    def __init__(self, resolver: GptSovitsEndpointResolver) -> None:
        self.resolver = resolver
        self.settings = resolver.settings

    @property
    def service_ready(self) -> bool:
        runtime = self.resolver.runtime
        return bool(self.resolver._custom_checked or (runtime is not None and runtime.service_ready))

    @property
    def endpoint_kind(self) -> str:
        return self.resolver.endpoint.kind

    def ensure_ready(self) -> tuple[bool, str]:
        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "RUNTIME_UNAVAILABLE: GPT-SoVITS 服务不可用。"
        if not self._ensure_character_weights(messages.append):
            return False, messages[-1] if messages else "SYNTHESIS_FAILED: GPT-SoVITS 模型准备失败。"
        return True, "TTS 服务已就绪。"

    def _ensure_service_available(self, fail: Callable[[str], None]) -> bool:
        return self.resolver.ensure_available(fail)

    def _ensure_character_weights(self, fail: Callable[[str], None]) -> bool:
        return self.resolver.ensure_character_weights(fail)

    def _restart_local_service_after_http_failure(self, status_code: int, body: str) -> bool:
        return self.resolver.restart_owned_after_http_failure(status_code, body)

    def close(self) -> None:
        self.resolver.close()


def reference_path_for_endpoint(
    settings: GPTSoVITSTTSSettings,
    reference_path: Path,
) -> str:
    custom = settings.custom_base_url
    if custom is None or is_loopback_base_url(custom):
        return str(reference_path)
    root = str(settings.remote_reference_root or "").strip()
    package_dir = settings.character_package_dir
    character_id = settings.character_id.strip()
    if not root or package_dir is None or not character_id:
        raise TtsError(
            TtsErrorCode.REFERENCE_AUDIO_UNAVAILABLE,
            "远程 GPT-SoVITS 需要配置参考音频根目录。",
        )
    relative = relative_reference_path(reference_path, package_dir)
    parts = (character_id, *relative.parts)
    if root.startswith("\\\\") or (len(root) >= 2 and root[1] == ":") or "\\" in root:
        return str(PureWindowsPath(root).joinpath(*parts))
    return str(PurePosixPath(root).joinpath(*parts))
