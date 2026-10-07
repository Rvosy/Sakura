"""Fill missing retained speech while the selected character is idle."""

from __future__ import annotations

import threading
from time import monotonic

try:
    from ._device_load import device_below_peak
except ImportError:
    from _device_load import device_below_peak


IDLE_FILL_PAGE_LIMIT = 40
IDLE_FILL_HEADROOM_BYTES = 2 * 1024 * 1024
IDLE_FILL_BACKOFF_SECONDS = 120.0
IDLE_FILL_INTERVAL_SECONDS = 15.0
JOB_POLL_SECONDS = 0.05


class IdleFill:
    def __init__(self, speech, logger):
        self.speech = speech
        self.logger = logger
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._job = None
        self._pause_until = 0.0
        self._character = ""
        self._latest = ""
        self._before = None

    def start(self):
        threading.Thread(target=self._run, name="sakura-voice-idle-fill", daemon=True).start()

    def close(self):
        self._stop.set()
        with self._lock:
            job = self._job
        if job is not None:
            self._cancel(job)

    def _cancel(self, job):
        try:
            self.speech.cancel(job)
        except Exception as error:
            if getattr(error, "code", "") not in {"SPEECH_JOB_NOT_FOUND", "GENERATION_INVALIDATED", "SERVICE_UNAVAILABLE"}:
                self.logger.warning("后台语音取消失败", fields={"reason_code": getattr(error, "code", "TTS_CANCEL_FAILED")})

    def _run(self):
        while not self._stop.wait(IDLE_FILL_INTERVAL_SECONDS):
            self.fill_once()

    def fill_once(self):
        if self._stop.is_set() or monotonic() < self._pause_until:
            return False
        job = None
        completed = False
        try:
            if not device_below_peak():
                return False
            status = self.speech.cache_status(IDLE_FILL_HEADROOM_BYTES)
            if not status["idleFill"] or status["busy"] or not status["hasRoom"]:
                return False
            character, latest = status["characterId"], status["latestCursor"]
            if character != self._character or latest != self._latest:
                self._character, self._latest, self._before = character, latest, None
            page = self.speech.cache_page(character, self._before, IDLE_FILL_PAGE_LIMIT)
            missing = next(((entry["entryId"], index)
                for entry in reversed(page["entries"]) if entry["kind"] == "assistant"
                for index, segment in enumerate(entry["segments"])
                if segment["hasText"] and not segment["suppressed"] and not segment["recorded"]), None)
            if missing is None:
                self._before = page["nextCursor"] if page["hasMore"] else None
                return False
            job = self.speech.begin(character, *missing, {"background": True, "exportAudio": False})["jobId"]
            with self._lock:
                self._job = job
            while not self._stop.is_set():
                result = self.speech.poll(job)
                if result["status"] == "completed":
                    completed = True
                    return True
                self._stop.wait(JOB_POLL_SECONDS)
            return False
        except Exception as error:
            code = getattr(error, "code", "TTS_SYNTHESIS_FAILED")
            if code in {"TTS_BACKGROUND_DEFERRED", "TTS_SERVICE_UNAVAILABLE", "TTS_SYNTHESIS_FAILED", "TTS_SYNTHESIS_TIMEOUT"}:
                self._pause_until = monotonic() + IDLE_FILL_BACKOFF_SECONDS
            if code not in {"TTS_DISABLED", "TTS_BACKGROUND_DEFERRED", "TTS_SYNTHESIS_CANCELLED",
                            "SPEECH_UNAVAILABLE", "SPEECH_CHARACTER_NOT_CURRENT", "GENERATION_INVALIDATED"}:
                self.logger.warning("后台语音补齐失败", fields={"reason_code": code})
            return False
        finally:
            with self._lock:
                self._job = None
            if job is not None and not completed:
                self._cancel(job)
