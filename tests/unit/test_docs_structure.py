from __future__ import annotations

from tools.check_docs import (
    _clean_link_target,
    _expected_kind,
    _parse_front_matter,
    check_docs,
)


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
