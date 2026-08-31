from __future__ import annotations

from pathlib import Path

from tools.check_docs import _parse_front_matter, check_docs


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
updated: 2026-08-26
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
updated: 2026-08-26
---

# Runtime contract
""",
    )
    return spec


def test_front_matter_parser_rejects_missing_metadata() -> None:
    metadata, body, errors = _parse_front_matter(
        "---\nkind: spec\nstatus: normative\n---\n\n# Title\n"
    )
    assert errors == []
    assert metadata == {"kind": "spec", "status": "normative"}
    assert body == "# Title"

    _, _, errors = _parse_front_matter("# Missing metadata\n")
    assert errors == ["missing YAML front matter"]


def test_docs_check_rejects_unindexed_documents_and_broken_links(tmp_path: Path) -> None:
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


def test_docs_check_accepts_the_indexed_root_changelog(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/README.md",
        """---
kind: index
status: current
audience: all
source_of_truth: self
updated: 2026-09-01
---

# Documentation

- [Changelog](CHANGELOG.md)
""",
    )
    _write(
        tmp_path / "docs/CHANGELOG.md",
        """---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-01
---

# Changelog
""",
    )

    assert check_docs(tmp_path) == []
