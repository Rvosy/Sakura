from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent.screen_awareness import SCREEN_AWARENESS_IMAGE_DETAIL
from app.core.cancellation import CancelChecker, OperationCancelled
from app.llm.prompts.runtime import wrap_untrusted_runtime_facts
from app.storage.atomic import atomic_write_text


VISUAL_OBSERVATION_RECENT_MINUTES = 10
VISUAL_OBSERVATION_RETENTION_DAYS = 7
VISUAL_OBSERVATION_RETENTION_LIMIT = 200

VISUAL_CONTEXT_KEYWORDS = (
    "刚才",
    "截图",
    "画面",
    "屏幕",
    "台词",
    "文字",
    "看到了什么",
    "看到什么",
    "写了什么",
    "说了什么",
    "那句话",
    "那段话",
)

_SENSITIVE_PATTERNS = (
    re.compile(r"\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=：]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(密码|口令|密钥|令牌|银行卡|信用卡|身份证)\s*[:：]\s*\S+"),
)


@dataclass(frozen=True)
class VisualObservationRecord:
    """一条可追问的视觉观察记录；不保存原始截图。"""

    id: str
    created_at: str
    source: str
    user_text: str
    screen_name: str
    width: int
    height: int
    summary: str
    visible_texts: list[str]
    uncertain_texts: list[str]
    notable_elements: list[str]
    confidence: float
    sensitive_redacted: bool = False


@dataclass(frozen=True)
class VisualObservationJob:
    """交给后台 Worker 生成视觉摘要的任务。"""

    id: str
    source: str
    user_text: str
    observation: Any | None = None
    screen_contexts: list[dict[str, Any]] | None = None


class VisualObservationStore:
    """按角色保存短期视觉观察记录。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: VisualObservationRecord) -> None:
        redacted_record, _ = _redact_record_dict(asdict(record))
        records = [*self._load_raw_records(), redacted_record]
        records = self._prune(records)
        text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
        atomic_write_text(self.path, text, encoding="utf-8")

    def recent(self, limit: int = 3, since_minutes: int | None = None) -> list[VisualObservationRecord]:
        threshold = None
        if since_minutes is not None:
            threshold = datetime.now().astimezone() - timedelta(minutes=since_minutes)

        records: list[VisualObservationRecord] = []
        for item in reversed(self._load_raw_records()):
            record = _record_from_dict(item)
            if record is None:
                continue
            if threshold is not None:
                created_at = _parse_iso(record.created_at)
                if created_at is None or created_at < threshold:
                    continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def search(self, keyword: str, limit: int = 3) -> list[VisualObservationRecord]:
        normalized = keyword.strip().casefold()
        if not normalized:
            return self.recent(limit=limit)

        records: list[VisualObservationRecord] = []
        for item in reversed(self._load_raw_records()):
            record = _record_from_dict(item)
            if record is None:
                continue
            haystack = "\n".join(
                [
                    record.summary,
                    *record.visible_texts,
                    *record.uncertain_texts,
                    *record.notable_elements,
                ]
            ).casefold()
            if normalized not in haystack:
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def _load_raw_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        records: list[dict[str, Any]] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(_redact_record_dict(data)[0])
        return records

    def _prune(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        threshold = datetime.now().astimezone() - timedelta(days=VISUAL_OBSERVATION_RETENTION_DAYS)
        kept: list[dict[str, Any]] = []
        for item in records:
            created_at = _parse_iso(str(item.get("created_at", "")))
            if created_at is not None and created_at < threshold:
                continue
            kept.append(item)
        return kept[-VISUAL_OBSERVATION_RETENTION_LIMIT:]


def generate_visual_observation_id() -> str:
    return f"vis_{uuid.uuid4().hex[:10]}"


def summarize_visual_observation(
    api_client: Any,
    job: VisualObservationJob,
    *,
    cancel_checker: CancelChecker | None = None,
) -> VisualObservationRecord:
    """调用视觉模型生成结构化观察；失败时返回可追踪的最小记录。"""
    metadata = _job_metadata(job)
    fallback = _fallback_record(job, metadata, "视觉摘要生成失败。")
    image_parts = _job_image_parts(job)
    if not image_parts:
        return fallback

    try:
        content = api_client.complete_raw(
            _build_visual_summary_prompt(),
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _build_visual_summary_user_text(job, metadata),
                        },
                        *image_parts,
                    ],
                }
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            cancel_checker=cancel_checker,
        )
    except OperationCancelled:
        raise
    except Exception as exc:
        return _fallback_record(job, metadata, f"视觉摘要生成失败：{exc}")

    parsed = _load_json_object(content)
    if parsed is None:
        return _fallback_record(job, metadata, "视觉摘要返回格式无法解析。")

    record, redacted = _record_from_summary(job, metadata, parsed)
    if redacted:
        return VisualObservationRecord(
            **{
                **asdict(record),
                "sensitive_redacted": True,
            }
        )
    return record


def extract_visual_observation_summary(content: str) -> dict[str, Any] | None:
    data = _load_json_object(content)
    if data is None:
        return None
    summary = data.get("visual_observation")
    return summary if isinstance(summary, dict) else None


def visual_observation_record_from_summary(
    job: VisualObservationJob,
    summary: dict[str, Any],
) -> VisualObservationRecord | None:
    if not _summary_has_content(summary):
        return None
    record, redacted = _record_from_summary(job, _job_metadata(job), summary)
    if redacted:
        return VisualObservationRecord(
            **{
                **asdict(record),
                "sensitive_redacted": True,
            }
        )
    return record


def build_visual_context_message(
    user_text: str,
    records: list[VisualObservationRecord],
) -> dict[str, str] | None:
    if not records:
        return None

    intro = "\n".join(
        [
            "以下是最近截图/屏幕观察提炼出的短期视觉记忆。它们是纯文本摘要，不包含原图；回答用户关于刚才截图、画面、台词或可见文字的问题时，应优先依据这些记录，不要臆造看不清的内容。",
            f"用户当前问题：{user_text.strip()}",
        ]
    )
    record_lines: list[str] = []
    for record in records:
        record_lines.append(
            "\n".join(
                [
                    f"- visual_id={record.id} source={record.source} time={record.created_at}",
                    f"  屏幕：{record.screen_name}，尺寸：{record.width}x{record.height}，置信度：{record.confidence:.2f}",
                    f"  摘要：{record.summary or '无摘要'}",
                    f"  可见文字/台词：{_format_list(record.visible_texts)}",
                    f"  不确定文字：{_format_list(record.uncertain_texts)}",
                    f"  关键元素：{_format_list(record.notable_elements)}",
                ]
            )
        )
    # 截图 OCR 文本属于外部不可信内容（可能含注入），包进“事实非指令”信封并标 untrusted；
    # 宿主对如何使用这些记录的引导（intro）保持可信、置于信封之外。
    content = wrap_untrusted_runtime_facts(
        "\n".join(record_lines),
        source="visual_memory",
        fragment_id="visual_memory",
        intro=intro,
    )
    return {"role": "system", "content": content}


def should_inject_visual_context(user_text: str) -> bool:
    normalized = "".join(user_text.split()).casefold()
    return any(keyword.casefold() in normalized for keyword in VISUAL_CONTEXT_KEYWORDS)


def _build_visual_summary_prompt() -> str:
    return """
你是桌面 Agent 的视觉观察整理器。你只负责把截图转成后续对话可检索的短期视觉记忆，并且只输出 JSON。
不要输出 Markdown，不要解释，不要生成角色回复。

提取重点：
- 优先围绕 user_text 判断用户最可能关心的区域、文字和状态；但不要因此丢掉明显可见的错误、标题、按钮文字和台词。
- 屏幕里明确可见的台词、字幕、聊天气泡、窗口标题、按钮文字、错误信息、代码片段。
- 用户可能追问的对象、位置、状态和关键界面元素。
- 看不清或不确定的文字放入 uncertain_texts，不要强行猜。
- 遇到 API Key、token、密码、身份证、银行卡等敏感内容，必须打码为 [REDACTED]，并把 sensitive_redacted 设为 true。

只返回如下 JSON：
{
  "summary": "一句到三句话概括画面",
  "visible_texts": ["明确可见文字或台词"],
  "uncertain_texts": ["不确定或部分可见文字"],
  "notable_elements": ["关键窗口、控件、人物、状态"],
  "confidence": 0.0,
  "sensitive_redacted": false
}
""".strip()


def _build_visual_summary_user_text(
    job: VisualObservationJob,
    metadata: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "source": job.source,
            "user_text": job.user_text,
            "screen_name": metadata["screen_name"],
            "width": metadata["width"],
            "height": metadata["height"],
            "captured_at": metadata["captured_at"],
            "screen_context_count": len(job.screen_contexts or []) or 1,
        },
        ensure_ascii=False,
        indent=2,
    )


def _job_image_parts(job: VisualObservationJob) -> list[dict[str, Any]]:
    image_urls: list[tuple[str, str]] = []
    if job.observation is not None:
        data_url = getattr(job.observation, "data_url", "")
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            image_urls.append((data_url, "low"))
    for context in job.screen_contexts or []:
        data_url = context.get("data_url")
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            image_urls.append(
                (
                    data_url,
                    _normalize_image_detail(
                        context.get("detail"),
                        default=SCREEN_AWARENESS_IMAGE_DETAIL,
                    ),
                )
            )

    return [
        {
            "type": "image_url",
            "image_url": {
                "url": image_url,
                "detail": detail,
            },
        }
        for image_url, detail in image_urls[:3]
    ]


def _normalize_image_detail(value: Any, *, default: str = "low") -> str:
    detail = str(value or "").strip().lower()
    if detail in {"low", "high", "original", "auto"}:
        return detail
    return default


def _job_metadata(job: VisualObservationJob) -> dict[str, Any]:
    if job.observation is not None:
        return {
            "width": int(getattr(job.observation, "width", 0) or 0),
            "height": int(getattr(job.observation, "height", 0) or 0),
            "screen_name": str(getattr(job.observation, "screen_name", "") or "unknown"),
            "captured_at": str(getattr(job.observation, "captured_at", "") or _now_iso()),
        }

    contexts = job.screen_contexts or []
    if contexts:
        first = contexts[0]
        last = contexts[-1]
        return {
            "width": _int_value(last.get("width")),
            "height": _int_value(last.get("height")),
            "screen_name": str(last.get("screen_name") or "unknown"),
            "captured_at": str(first.get("captured_at") or _now_iso()),
        }

    return {
        "width": 0,
        "height": 0,
        "screen_name": "unknown",
        "captured_at": _now_iso(),
    }


def _record_from_summary(
    job: VisualObservationJob,
    metadata: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[VisualObservationRecord, bool]:
    summary_text, summary_redacted = _redact_text(_text_value(summary.get("summary")))
    visible_texts, visible_redacted = _redact_text_list(summary.get("visible_texts"))
    uncertain_texts, uncertain_redacted = _redact_text_list(summary.get("uncertain_texts"))
    notable_elements, notable_redacted = _redact_text_list(summary.get("notable_elements"))
    user_text, user_text_redacted = _redact_text(job.user_text)
    return (
        VisualObservationRecord(
            id=job.id,
            created_at=_now_iso(),
            source=job.source,
            user_text=user_text,
            screen_name=metadata["screen_name"],
            width=metadata["width"],
            height=metadata["height"],
            summary=summary_text,
            visible_texts=visible_texts,
            uncertain_texts=uncertain_texts,
            notable_elements=notable_elements,
            confidence=_confidence_value(summary.get("confidence")),
            sensitive_redacted=bool(summary.get("sensitive_redacted", False)),
        ),
        summary_redacted
        or visible_redacted
        or uncertain_redacted
        or notable_redacted
        or user_text_redacted,
    )


def _summary_has_content(summary: dict[str, Any]) -> bool:
    return bool(
        _text_value(summary.get("summary"))
        or _string_list(summary.get("visible_texts"))
        or _string_list(summary.get("uncertain_texts"))
        or _string_list(summary.get("notable_elements"))
    )


def _fallback_record(
    job: VisualObservationJob,
    metadata: dict[str, Any],
    summary: str,
) -> VisualObservationRecord:
    return VisualObservationRecord(
        id=job.id,
        created_at=_now_iso(),
        source=job.source,
        user_text=_redact_text(job.user_text)[0],
        screen_name=metadata["screen_name"],
        width=metadata["width"],
        height=metadata["height"],
        summary=summary,
        visible_texts=[],
        uncertain_texts=[],
        notable_elements=[],
        confidence=0.0,
        sensitive_redacted=False,
    )


def _record_from_dict(data: dict[str, Any]) -> VisualObservationRecord | None:
    try:
        redacted_data, sensitive_redacted = _redact_record_dict(data)
        return VisualObservationRecord(
            id=_text_value(redacted_data.get("id")),
            created_at=_text_value(redacted_data.get("created_at")),
            source=_text_value(redacted_data.get("source")),
            user_text=_text_value(redacted_data.get("user_text")),
            screen_name=_text_value(redacted_data.get("screen_name")),
            width=_int_value(redacted_data.get("width")),
            height=_int_value(redacted_data.get("height")),
            summary=_text_value(redacted_data.get("summary")),
            visible_texts=_string_list(redacted_data.get("visible_texts")),
            uncertain_texts=_string_list(redacted_data.get("uncertain_texts")),
            notable_elements=_string_list(redacted_data.get("notable_elements")),
            confidence=_confidence_value(redacted_data.get("confidence")),
            sensitive_redacted=bool(redacted_data.get("sensitive_redacted", False)) or sensitive_redacted,
        )
    except (TypeError, ValueError):
        return None


def _redact_record_dict(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    redacted = dict(data)
    sensitive_redacted = False
    for key in ("user_text", "summary"):
        redacted[key], changed = _redact_text(_text_value(redacted.get(key)))
        sensitive_redacted = sensitive_redacted or changed
    for key in ("visible_texts", "uncertain_texts", "notable_elements"):
        redacted[key], changed = _redact_text_list(redacted.get(key))
        sensitive_redacted = sensitive_redacted or changed
    if sensitive_redacted:
        redacted["sensitive_redacted"] = True
    redacted.pop("data_url", None)
    redacted.pop("image_url", None)
    return redacted, sensitive_redacted


def _redact_text_list(value: Any) -> tuple[list[str], bool]:
    result: list[str] = []
    changed = False
    for item in _string_list(value):
        redacted, item_changed = _redact_text(item)
        if redacted:
            result.append(redacted)
        changed = changed or item_changed
    return result, changed


def _redact_text(text: str) -> tuple[str, bool]:
    redacted = text
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, redacted != text


def _load_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _format_list(items: list[str]) -> str:
    return "；".join(item for item in items if item.strip()) or "无"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _confidence_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
