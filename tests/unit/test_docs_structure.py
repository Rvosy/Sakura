from __future__ import annotations

from pathlib import Path

from tools.check_docs import (
    _clean_link_target,
    _expected_kind,
    _parse_front_matter,
    check_docs,
)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _minimal_spec_repo(tmp_path: Path) -> Path:
    _write(
        tmp_path / "docs/specs/README.md",
        """---
kind: index
status: current
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# Specs

- [Runtime contract](runtime-v2/example.md)
""",
    )
    spec = tmp_path / "docs/specs/runtime-v2/example.md"
    _write(
        spec,
        """---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# Runtime contract
""",
    )
    return spec


def test_repository_documentation_satisfies_structure_contract() -> None:
    assert check_docs() == []


def test_front_matter_parser_requires_key_value_metadata() -> None:
    metadata, body, errors = _parse_front_matter(
        "---\nkind: spec\nstatus: normative\n---\n\n# Title\n"
    )

    assert errors == []
    assert metadata == {"kind": "spec", "status": "normative"}
    assert body == "# Title"

    _, _, errors = _parse_front_matter("# Missing metadata\n")
    assert errors == ["missing YAML front matter"]


def test_document_kind_follows_directory_role() -> None:
    assert _expected_kind("docs/userdocs/SETUP.md") == "userdoc"
    assert _expected_kind("docs/specs/runtime-v2/WP-2-01.md") == "spec"
    assert _expected_kind("docs/archive/plans/runtime-v2/old.md") == "plan"
    assert _expected_kind("docs/archive/adr/0008-old.md") == "adr"
    assert _expected_kind("docs/adr/README.md") == "index"


def test_link_target_strips_titles_and_anchors() -> None:
    assert _clean_link_target("<../README.md#docs> \"title\"") == "../README.md"
    assert _clean_link_target("https://example.com/page#section") == "https://example.com/page"


def test_runtime_spec_does_not_require_work_package_status(tmp_path: Path) -> None:
    _minimal_spec_repo(tmp_path)

    assert check_docs(tmp_path) == []


def test_docs_check_still_rejects_unindexed_documents_and_broken_links(
    tmp_path: Path,
) -> None:
    spec = _minimal_spec_repo(tmp_path)
    index = tmp_path / "docs/specs/README.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "- [Runtime contract](runtime-v2/example.md)", "No document links."
        ),
        encoding="utf-8",
    )

    assert any("active document is not linked" in error for error in check_docs(tmp_path))

    _minimal_spec_repo(tmp_path)
    spec.write_text(
        spec.read_text(encoding="utf-8") + "\n[missing](missing.md)\n",
        encoding="utf-8",
    )
    assert any("broken local link: missing.md" in error for error in check_docs(tmp_path))
