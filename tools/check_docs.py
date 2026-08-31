"""Validate Sakura's documentation layout, metadata, indexes, and local links."""

from __future__ import annotations

import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
REQUIRED_FIELDS = ("kind", "status", "audience", "source_of_truth", "updated")
ALLOWED_KINDS = {"index", "userdoc", "devdoc", "spec", "adr", "plan", "record"}
ALLOWED_TOP_LEVEL = {
    "CHANGELOG.md",
    "README.md",
    "README.en.md",
    "userdocs",
    "devdocs",
    "specs",
    "adr",
    "plans",
    "records",
    "archive",
}
STATUS_BY_KIND = {
    "index": {"current"},
    "userdoc": {"current", "deprecated"},
    "devdoc": {"current", "deprecated"},
    "spec": {"draft", "normative", "superseded", "archived"},
    "adr": {"proposed", "accepted", "superseded", "deprecated", "archived"},
    "plan": {"planned", "active", "stabilizing", "accepted", "cancelled", "archived"},
    "record": {"recorded", "archived"},
}
OLD_PATH_MARKERS = (
    "docs/runtime-v2/",
    "docs/superpowers/",
    "docs/releases/",
    "docs/SETUP.md",
    "docs/API_CONFIG.md",
    "docs/MACOS_SETUP.md",
    "docs/TECHNICAL_README.md",
    "docs/SAKURA_PLUGIN_SDK.md",
    "docs/TEST_SUITE_AUDIT.md",
    "docs/TTS_SHUTDOWN_NATIVE_CRASH.md",
    "docs/DESKTOP_PET_EXPERIENCE_ARCHITECTURE_PLAN.md",
    "docs/RUNTIME_RESOURCE_MANAGER_PLAN.md",
    "docs/RESOURCE_MANAGER_",
    "docs/TTS_PROVIDER_SPLIT_PLAN.md",
    "docs/context-token-budget.md",
    "docs/README.zh.md",
)
FRONT_MATTER_SEPARATOR = "---"
MARKDOWN_LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)\n]+)\)")
DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Document:
    path: Path
    relative_path: str
    metadata: dict[str, str]
    body: str


def _parse_front_matter(text: str) -> tuple[dict[str, str], str, list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_SEPARATOR:
        return {}, text, ["missing YAML front matter"]

    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == FRONT_MATTER_SEPARATOR),
        None,
    )
    if end is None:
        return {}, text, ["unterminated YAML front matter"]

    metadata: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(lines[1:end], start=2):
        if not line.strip():
            continue
        if ":" not in line:
            errors.append(f"front matter line {line_number} is not key: value")
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            errors.append(f"front matter line {line_number} has an empty key or value")
        elif key in metadata:
            errors.append(f"duplicate front matter key: {key}")
        else:
            metadata[key] = value
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return metadata, body, errors


def _documents(repo_root: Path) -> tuple[list[Document], list[str]]:
    docs_root = repo_root / "docs"
    if not docs_root.is_dir():
        return [], ["docs directory does not exist"]

    documents: list[Document] = []
    errors: list[str] = []
    for path in sorted(docs_root.rglob("*.md")):
        if "__pycache__" in path.parts:
            continue
        metadata, body, parse_errors = _parse_front_matter(path.read_text(encoding="utf-8"))
        relative_path = path.relative_to(repo_root).as_posix()
        documents.append(Document(path, relative_path, metadata, body))
        errors.extend(f"{relative_path}: {error}" for error in parse_errors)
    return documents, errors


def _expected_kind(relative_path: str) -> str | None:
    parts = Path(relative_path).parts
    if len(parts) < 2 or parts[0] != "docs":
        return None
    if len(parts) == 2 and parts[1] == "CHANGELOG.md":
        return "userdoc"
    if Path(relative_path).name == "README.md" or (
        len(parts) == 2 and parts[1] == "README.en.md"
    ):
        return "index"
    category = parts[1]
    if category == "archive":
        if len(parts) == 3 and parts[2] == "README.md":
            return "index"
        return {
            "adr": "adr",
            "plans": "plan",
            "specs": "spec",
            "records": "record",
        }.get(parts[2] if len(parts) > 2 else "")
    if category in {"userdocs", "devdocs", "specs", "adr", "plans", "records"}:
        if Path(relative_path).name == "README.md":
            return "index"
        return {
            "userdocs": "userdoc",
            "devdocs": "devdoc",
            "specs": "spec",
            "adr": "adr",
            "plans": "plan",
            "records": "record",
        }[category]
    return None


def _category_key(relative_path: str) -> str:
    parts = Path(relative_path).parts
    return "root" if len(parts) == 2 else parts[1]


def _resolve_reference(repo_root: Path, document: Document, value: str) -> Path | None:
    if value == "self":
        return None
    candidate = Path(value)
    if value.startswith("docs/"):
        return (repo_root / candidate).resolve()
    return (document.path.parent / candidate).resolve()


def _clean_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split()[0]
    return urllib.parse.unquote(target.split("#", 1)[0])


def _contains_retired_path(text: str, marker: str) -> bool:
    if marker.startswith("docs/"):
        return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(marker)}", text) is not None
    return marker in text


def _linked_targets(repo_root: Path, documents: list[Document]) -> dict[str, set[str]]:
    linked_by_category: dict[str, set[str]] = {}
    for document in documents:
        if document.path.name != "README.md":
            continue
        category = _category_key(document.relative_path)
        targets = linked_by_category.setdefault(category, set())
        for raw_target in MARKDOWN_LINK.findall(document.body):
            target = _clean_link_target(raw_target)
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            destination = (document.path.parent / target).resolve()
            if destination.is_file():
                targets.add(destination.as_posix())
    return linked_by_category


def check_docs(repo_root: Path = REPO_ROOT) -> list[str]:
    documents, errors = _documents(repo_root)
    docs_root = repo_root / "docs"

    for entry in sorted(docs_root.iterdir()) if docs_root.is_dir() else []:
        if entry.name in {".gitkeep", "__pycache__"}:
            continue
        if entry.name not in ALLOWED_TOP_LEVEL and not (
            entry.is_dir() and not any(child.suffix == ".md" for child in entry.rglob("*"))
        ):
            errors.append(f"docs: unexpected top-level entry: {entry.name}")

    linked_by_category = _linked_targets(repo_root, documents)
    for document in documents:
        relative_path = document.relative_path
        metadata = document.metadata
        prefix = f"{relative_path}:"
        for field in REQUIRED_FIELDS:
            if not metadata.get(field):
                errors.append(f"{prefix} missing metadata field: {field}")

        kind = metadata.get("kind")
        status = metadata.get("status")
        expected_kind = _expected_kind(relative_path)
        if kind not in ALLOWED_KINDS:
            errors.append(f"{prefix} unsupported kind: {kind or '<missing>'}")
        elif expected_kind and kind != expected_kind:
            errors.append(f"{prefix} kind {kind!r} does not match expected {expected_kind!r}")
        if kind in STATUS_BY_KIND and status not in STATUS_BY_KIND[kind]:
            errors.append(f"{prefix} invalid status {status!r} for kind {kind!r}")
        if metadata.get("updated") and not DATE_VALUE.fullmatch(metadata["updated"]):
            errors.append(f"{prefix} updated must use YYYY-MM-DD")

        for reference_field in ("source_of_truth", "status_source"):
            reference = metadata.get(reference_field)
            if not reference or reference == "self":
                continue
            destination = _resolve_reference(repo_root, document, reference)
            if destination is None or not destination.exists():
                errors.append(f"{prefix} {reference_field} does not resolve: {reference}")

        if status not in {"archived", "superseded", "deprecated"} and kind != "index":
            category = _category_key(relative_path)
            if document.path.resolve().as_posix() not in linked_by_category.get(category, set()):
                errors.append(f"{prefix} active document is not linked by a category README")

        for raw_target in MARKDOWN_LINK.findall(document.body):
            target = _clean_link_target(raw_target)
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            destination = (document.path.parent / target).resolve()
            if not destination.exists():
                errors.append(f"{prefix} broken local link: {target}")

        combined = f"{document.body}\n{document.metadata}"
        for marker in OLD_PATH_MARKERS:
            if _contains_retired_path(combined, marker):
                errors.append(f"{prefix} contains retired documentation path: {marker}")

    return sorted(set(errors))


def main() -> int:
    errors = check_docs()
    if errors:
        for error in errors:
            print(f"[docs] ERROR {error}", file=sys.stderr)
        print(f"[docs] failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("[docs] documentation structure, metadata, links, and source-of-truth checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
