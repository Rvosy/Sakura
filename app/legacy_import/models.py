from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class DomainInspection:
    present: bool = False
    files: int = 0
    bytes: int = 0
    items: int = 0

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyInspection:
    schema_version: int
    compatible: bool
    detected_version: str
    source_platform: str
    source_label: str
    tts_external_link: bool
    required_bytes: int
    available_bytes: int
    domains: dict[str, DomainInspection]
    overwrite_domains: tuple[str, ...] = ()
    blockers: tuple[dict[str, object], ...] = ()
    warnings: tuple[dict[str, object], ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "compatible": self.compatible,
            "detectedVersion": self.detected_version,
            "sourcePlatform": self.source_platform,
            "sourceLabel": self.source_label,
            "ttsExternalLink": self.tts_external_link,
            "requiredBytes": self.required_bytes,
            "availableBytes": self.available_bytes,
            "domains": {
                name: value.to_public_dict() for name, value in self.domains.items()
            },
            "overwriteDomains": list(self.overwrite_domains),
            "requiresOverwriteConfirmation": bool(self.overwrite_domains),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass
class ImportReport:
    import_id: str
    detected_version: str
    outcome: str = "completed"
    counts: dict[str, int] = field(default_factory=dict)
    bytes: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, object]] = field(default_factory=list)
    quarantined: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[dict[str, object]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "importId": self.import_id,
            "detectedVersion": self.detected_version,
            "outcome": self.outcome,
            "counts": dict(sorted(self.counts.items())),
            "bytes": dict(sorted(self.bytes.items())),
            "warnings": self.warnings,
            "quarantined": self.quarantined,
            "artifacts": self.artifacts,
        }
