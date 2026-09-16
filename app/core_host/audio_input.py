"""Host-owned, generation-scoped microphone resources and reader leases."""

from __future__ import annotations

import re
import secrets
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from app.plugins.host_services import HOST_CALLER
from app.storage.paths import StoragePaths, sanitize_directory_component

HOST_AUDIO_INPUT_SERVICE = "sakura.host.audio_input"


class AudioInputError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Audio:
    resource_id: str
    path: Path
    provider_id: str
    service_key: str
    scope_id: str
    hub_id: str
    hub_scope: str
    producer: bool = True
    revoked: bool = False
    authorized: bool = False
    descriptor: dict = field(default_factory=dict)
    leases: set[str] = field(default_factory=set)


class AudioInputResources:
    """Only descriptors cross Services; only an authorized reader gets a path."""

    def __init__(self, user_root: Path, generation_id: str, identity: Callable) -> None:
        self.root = (StoragePaths(user_root).cache_dir / "asr-input"
                     / sanitize_directory_component(generation_id))
        self._identity = identity
        self._lock = threading.RLock()
        self._items: dict[str, _Audio] = {}
        self._leases: dict[str, _Audio] = {}
        self._closed = False

    def verifyProvider(self, provider_id: str, service_key: str) -> dict:
        self._require_hub()
        identity = self._identity(service_key, include_starting=True)
        if identity["providerId"] != provider_id:
            raise AudioInputError("ASR_PROVIDER_IDENTITY_INVALID")
        return dict(identity)

    def allocate(self, recording_id: str, provider_id: str, service_key: str, scope_id: str) -> dict:
        if not isinstance(recording_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", recording_id):
            raise AudioInputError("ASR_RECORDING_INVALID")
        hub = self._identity("sakura.asr")
        with self._lock:
            if self._closed:
                raise AudioInputError("STALE_GENERATION")
            if len(self._items) >= 8:
                raise AudioInputError("ASR_RESOURCE_LIMIT")
            resource_id = "audio_" + secrets.token_hex(16)
            self.root.mkdir(parents=True, exist_ok=True)
            # Never recycle a filename while an older producer may still hold it.
            item = _Audio(resource_id, self.root / (resource_id + ".wav"), provider_id,
                          service_key, scope_id, hub["providerId"], hub["scopeId"])
            self._items[resource_id] = item
            return {"resourceId": resource_id, "path": str(item.path)}

    def commit(self, resource_id: str) -> dict:
        with self._lock:
            item = self._get(resource_id)
            item.producer = False
            try:
                if item.revoked or self._closed:
                    raise AudioInputError("ASR_CANCELLED")
                if item.path.is_symlink() or item.path.resolve().parent != self.root.resolve():
                    raise AudioInputError("ASR_AUDIO_INVALID")
                size = item.path.stat().st_size
                if not 44 < size <= 1_920_128:
                    raise AudioInputError("ASR_AUDIO_INVALID")
                with wave.open(str(item.path), "rb") as audio:
                    frames = audio.getnframes()
                    if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                        raise AudioInputError("ASR_AUDIO_INVALID")
                    if not 0 < frames <= 960_000 or len(audio.readframes(frames)) != frames * 2:
                        raise AudioInputError("ASR_AUDIO_INVALID")
                item.descriptor = {"resourceId": resource_id, "mediaType": "audio/wav",
                                   "byteLength": size, "sampleRate": 16000, "channels": 1,
                                   "durationMs": round(frames / 16)}
                return dict(item.descriptor)
            except Exception as error:
                item.revoked = True
                self._cleanup(item)
                if isinstance(error, AudioInputError):
                    raise
                raise AudioInputError("ASR_AUDIO_INVALID") from error

    def producer_done(self, resource_id: str) -> None:
        with self._lock:
            item = self._items.get(resource_id)
            if item:
                item.producer = False
                item.revoked = True
                self._cleanup(item)

    def authorize(self, descriptor: Mapping, service_key: str) -> dict:
        hub = self._require_hub()
        with self._lock:
            item = self._get(descriptor.get("resourceId") if isinstance(descriptor, Mapping) else None)
            identity = self._identity(service_key)
            if (item.revoked or item.producer or dict(descriptor) != item.descriptor
                    or service_key != item.service_key
                    or identity != {"providerId": item.provider_id, "scopeId": item.scope_id}
                    or hub != {"providerId": item.hub_id, "scopeId": item.hub_scope}):
                raise AudioInputError("ASR_AUDIO_UNAUTHORIZED")
            item.authorized = True
            return dict(item.descriptor)

    def acquire(self, resource_id: str) -> dict:
        with self._lock:
            item = self._get(resource_id)
            if (self._closed or item.revoked or not item.authorized or item.producer
                    or HOST_CALLER.get() != item.provider_id
                    or self._identity(item.service_key) != {"providerId": item.provider_id, "scopeId": item.scope_id}
                    or self._identity("sakura.asr") != {"providerId": item.hub_id, "scopeId": item.hub_scope}):
                raise AudioInputError("ASR_AUDIO_UNAUTHORIZED")
            if item.leases:
                raise AudioInputError("ASR_AUDIO_BUSY")
            lease = "read_" + secrets.token_hex(16)
            item.leases.add(lease)
            self._leases[lease] = item
            return {**item.descriptor, "leaseId": lease, "path": str(item.path)}

    def release(self, lease_id: str) -> bool:
        with self._lock:
            item = self._leases.get(lease_id)
            if item is None:
                return False
            if HOST_CALLER.get() != item.provider_id:
                raise AudioInputError("ASR_AUDIO_UNAUTHORIZED")
            self._leases.pop(lease_id)
            item.leases.discard(lease_id)
            self._cleanup(item)
            return True

    def revoke(self, resource_id: str) -> bool:
        self._require_hub()
        return self.revoke_resource(resource_id)

    def revoke_resource(self, resource_id: str) -> bool:
        with self._lock:
            item = self._items.get(resource_id)
            if item is None:
                return False
            item.revoked = True
            self._cleanup(item)
            return True

    def revoke_scope(self, plugin_id: str) -> None:
        # Runtime calls this after the departing process tree is gone.
        with self._lock:
            for item in tuple(self._items.values()):
                if plugin_id in {item.provider_id, item.hub_id}:
                    item.revoked = True
                if plugin_id == item.provider_id:
                    for lease in item.leases:
                        self._leases.pop(lease, None)
                    item.leases.clear()
                self._cleanup(item)

    def close(self) -> None:
        # Called after the host stopped capture and the plugin manager stopped readers.
        with self._lock:
            self._closed = True
            for item in tuple(self._items.values()):
                item.producer = False
                item.revoked = True
                self._cleanup(item)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def _get(self, resource_id: str) -> _Audio:
        item = self._items.get(resource_id) if isinstance(resource_id, str) else None
        if item is None:
            raise AudioInputError("ASR_AUDIO_NOT_FOUND")
        return item

    def _require_hub(self) -> dict:
        hub = self._identity("sakura.asr")
        if HOST_CALLER.get() != hub["providerId"]:
            raise AudioInputError("ASR_AUDIO_UNAUTHORIZED")
        return hub

    def _cleanup(self, item: _Audio) -> None:
        if not item.revoked or item.producer or item.leases:
            return
        try:
            item.path.unlink(missing_ok=True)
        except OSError:
            # A producer may be unwinding on Windows. Keep ownership for its
            # capture_discarded notification or generation close.
            return
        self._items.pop(item.resource_id, None)
        try:
            self.root.rmdir()
        except OSError:
            pass
