from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


PET_STATE_MOODS: tuple[str, ...] = (
    "neutral",
    "happy",
    "sad",
    "angry",
    "shy",
    "anxious",
    "curious",
    "tired",
)

PET_STATE_TRIGGER_VALUES: tuple[str, ...] = (
    "startup",
    "user_message",
    "assistant_reply",
    "runtime_event",
    "tool_result",
    "harness",
)

_TEXT_LIMITS = {
    "last_user_signal": 120,
    "last_trigger": 40,
    "reason": 240,
    "force_reason": 240,
}

_DISPLAY_BY_MOOD: dict[str, tuple[str, str]] = {
    "neutral": ("平静", "站立待机"),
    "happy": ("开心", "微笑"),
    "sad": ("低落", "低落"),
    "angry": ("不满", "生气"),
    "shy": ("害羞", "害羞"),
    "anxious": ("不安", "担心"),
    "curious": ("好奇", "好奇"),
    "tired": ("疲惫", "困倦"),
}


@dataclass(frozen=True)
class PetAffect:
    valence: float = 0.0
    arousal: float = 0.2
    confidence: float = 0.7

    def to_dict(self) -> dict[str, float]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class PetStateEvidence:
    last_user_signal: str = ""
    last_trigger: str = "startup"
    reason: str = "默认初始状态。"

    def to_dict(self) -> dict[str, str]:
        return {
            "last_user_signal": self.last_user_signal,
            "last_trigger": self.last_trigger,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PetStateDisplay:
    label: str = "平静"
    idle_expression_hint: str = "站立待机"

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "idle_expression_hint": self.idle_expression_hint,
        }


@dataclass(frozen=True)
class PetState:
    mood: str = "neutral"
    affect: PetAffect = field(default_factory=PetAffect)
    evidence: PetStateEvidence = field(default_factory=PetStateEvidence)
    display: PetStateDisplay = field(default_factory=PetStateDisplay)
    updated_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mood": self.mood,
            "affect": self.affect.to_dict(),
            "evidence": self.evidence.to_dict(),
            "display": self.display.to_dict(),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class PetStateRecord:
    state: PetState = field(default_factory=PetState)
    last_model_delta: dict[str, Any] | None = None
    last_harness_decision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "last_model_delta": deepcopy(self.last_model_delta),
            "last_harness_decision": deepcopy(self.last_harness_decision),
        }


def default_pet_state_record() -> PetStateRecord:
    state = PetState(
        mood="neutral",
        affect=PetAffect(),
        evidence=PetStateEvidence(),
        display=display_for_mood("neutral"),
        updated_at=_now_iso(),
    )
    return PetStateRecord(state=state)


def pet_state_record_from_dict(data: dict[str, Any]) -> PetStateRecord:
    raw_state = data.get("state") if isinstance(data.get("state"), dict) else data
    state = pet_state_from_dict(raw_state if isinstance(raw_state, dict) else {})
    last_model_delta = data.get("last_model_delta")
    last_harness_decision = data.get("last_harness_decision")
    return PetStateRecord(
        state=state,
        last_model_delta=deepcopy(last_model_delta) if isinstance(last_model_delta, dict) else None,
        last_harness_decision=deepcopy(last_harness_decision) if isinstance(last_harness_decision, dict) else None,
    )


def pet_state_from_dict(data: dict[str, Any]) -> PetState:
    mood = _coerce_mood(data.get("mood", "neutral"))
    affect_data = data.get("affect") if isinstance(data.get("affect"), dict) else {}
    evidence_data = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    updated_at = data.get("updated_at")
    return PetState(
        mood=mood,
        affect=PetAffect(
            valence=_clamp_number(affect_data.get("valence", 0.0), 0.0, -1.0, 1.0),
            arousal=_clamp_number(affect_data.get("arousal", 0.2), 0.2, 0.0, 1.0),
            confidence=_clamp_number(affect_data.get("confidence", 0.7), 0.7, 0.0, 1.0),
        ),
        evidence=PetStateEvidence(
            last_user_signal=_short_text(
                evidence_data.get("last_user_signal", ""),
                _TEXT_LIMITS["last_user_signal"],
            ),
            last_trigger=_normalize_trigger(evidence_data.get("last_trigger", "startup")),
            reason=_short_text(evidence_data.get("reason", "默认初始状态。"), _TEXT_LIMITS["reason"]),
        ),
        display=display_for_mood(mood),
        updated_at=updated_at if isinstance(updated_at, str) and updated_at.strip() else _now_iso(),
    )


def apply_pet_state_delta(
    record: PetStateRecord,
    delta: dict[str, Any],
    *,
    forced: bool = False,
    force_fields: list[str] | None = None,
    force_reason: str = "",
) -> tuple[PetStateRecord, dict[str, Any]]:
    if not isinstance(delta, dict):
        raise ValueError("pet_state_update.delta 必须是 JSON object。")
    unsupported = sorted(set(delta) - {"mood", "affect", "evidence"})
    if unsupported:
        raise ValueError(f"pet_state_update.delta 包含不支持字段：{', '.join(unsupported)}")

    force_fields = _normalize_force_fields(force_fields)
    revised_fields: list[str] = []
    state = record.state
    mood = state.mood
    affect = state.affect
    evidence = state.evidence

    if "mood" in delta:
        mood = _coerce_mood(delta.get("mood"))

    if "affect" in delta:
        affect_delta = delta.get("affect")
        if not isinstance(affect_delta, dict):
            raise ValueError("pet_state_update.delta.affect 必须是 JSON object。")
        unsupported_affect = sorted(set(affect_delta) - {"valence", "arousal", "confidence"})
        if unsupported_affect:
            raise ValueError(f"pet_state_update.delta.affect 包含不支持字段：{', '.join(unsupported_affect)}")
        affect, affect_revisions = _apply_affect_delta(affect, affect_delta)
        revised_fields.extend(affect_revisions)

    if "evidence" in delta:
        evidence_delta = delta.get("evidence")
        if not isinstance(evidence_delta, dict):
            raise ValueError("pet_state_update.delta.evidence 必须是 JSON object。")
        unsupported_evidence = sorted(set(evidence_delta) - {"last_user_signal", "last_trigger", "reason"})
        if unsupported_evidence:
            raise ValueError(f"pet_state_update.delta.evidence 包含不支持字段：{', '.join(unsupported_evidence)}")
        evidence, evidence_revisions = _apply_evidence_delta(evidence, evidence_delta)
        revised_fields.extend(evidence_revisions)

    state_values_changed = (
        state.mood != mood
        or state.affect != affect
        or state.evidence != evidence
    )
    next_state = PetState(
        mood=mood,
        affect=affect,
        evidence=evidence,
        display=display_for_mood(mood),
        updated_at=_now_iso() if state_values_changed else state.updated_at,
    )
    before_state = state.to_dict()
    after_state = next_state.to_dict()
    changed = before_state != after_state
    submitted_at = _now_iso()
    model_delta = {
        "submitted_at": submitted_at,
        "delta": deepcopy(delta),
        "forced": bool(forced),
        "force_fields": force_fields,
    }
    if force_reason:
        model_delta["force_reason"] = _short_text(force_reason, _TEXT_LIMITS["force_reason"])
    decision = _build_phase1_decision(
        changed=changed,
        forced=bool(forced),
        revised_fields=revised_fields,
    )
    return (
        PetStateRecord(
            state=next_state,
            last_model_delta=model_delta,
            last_harness_decision=decision,
        ),
        decision,
    )


def display_for_mood(mood: str) -> PetStateDisplay:
    label, hint = _DISPLAY_BY_MOOD.get(mood, _DISPLAY_BY_MOOD["neutral"])
    return PetStateDisplay(label=label, idle_expression_hint=hint)


def _apply_affect_delta(
    current: PetAffect,
    delta: dict[str, Any],
) -> tuple[PetAffect, list[str]]:
    revised: list[str] = []
    values = current.to_dict()
    ranges = {
        "valence": (-1.0, 1.0),
        "arousal": (0.0, 1.0),
        "confidence": (0.0, 1.0),
    }
    for field_name, (minimum, maximum) in ranges.items():
        if field_name not in delta:
            continue
        raw = _require_number(delta.get(field_name), f"affect.{field_name}")
        clamped = max(minimum, min(maximum, raw))
        if clamped != raw:
            revised.append(f"affect.{field_name}")
        values[field_name] = clamped
    return PetAffect(**values), revised


def _apply_evidence_delta(
    current: PetStateEvidence,
    delta: dict[str, Any],
) -> tuple[PetStateEvidence, list[str]]:
    revised: list[str] = []
    values = current.to_dict()
    for field_name in ("last_user_signal", "reason"):
        if field_name not in delta:
            continue
        raw = delta.get(field_name)
        if not isinstance(raw, str):
            raise ValueError(f"evidence.{field_name} 必须是字符串。")
        shortened = _short_text(raw, _TEXT_LIMITS[field_name])
        if shortened != raw.strip():
            revised.append(f"evidence.{field_name}")
        values[field_name] = shortened
    if "last_trigger" in delta:
        raw_trigger = delta.get("last_trigger")
        trigger = _normalize_trigger(raw_trigger)
        if trigger != str(raw_trigger or "").strip():
            revised.append("evidence.last_trigger")
        values["last_trigger"] = trigger
    return PetStateEvidence(**values), revised


def _build_phase1_decision(
    *,
    changed: bool,
    forced: bool,
    revised_fields: list[str],
) -> dict[str, Any]:
    unique_revised = sorted(set(revised_fields))
    if not changed and not unique_revised:
        status = "noop"
        reason = "delta 没有造成状态变化。"
    elif forced:
        status = "model_forced"
        reason = "Phase 1 记录 forced 请求；仅执行 schema、范围和长度校验。"
    elif unique_revised:
        status = "revised"
        reason = "Phase 1 已按 schema 范围或长度限制修正部分字段。"
    else:
        status = "applied"
        reason = "Phase 1 已通过 schema 校验并应用。"
    return {
        "status": status,
        "reason": reason,
        "revised_fields": unique_revised,
        "rejected_fields": [],
    }


def _coerce_mood(value: Any) -> str:
    mood = str(value or "").strip().lower()
    if mood not in PET_STATE_MOODS:
        raise ValueError(f"不支持的 pet_state mood：{mood or '<empty>'}")
    return mood


def _normalize_trigger(value: Any) -> str:
    trigger = str(value or "").strip()
    if not trigger:
        return "user_message"
    if trigger in PET_STATE_TRIGGER_VALUES:
        return trigger
    return _short_text(trigger, _TEXT_LIMITS["last_trigger"])


def _normalize_force_fields(value: list[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("pet_state_update.force_fields 必须是字符串数组。")
    fields: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("pet_state_update.force_fields 必须是字符串数组。")
        name = item.strip()
        if name:
            fields.append(name)
    return sorted(set(fields))


def _require_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字。") from exc
    if number != number:
        raise ValueError(f"{field_name} 不能是 NaN。")
    return number


def _clamp_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = _require_number(value, "value")
    except ValueError:
        return default
    return max(minimum, min(maximum, number))


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
