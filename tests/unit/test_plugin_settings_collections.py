from __future__ import annotations

import pytest

from app.core_host.plugin_host_services import HostServiceError, _SettingsHostService


@pytest.fixture
def settings():
    query_requests = []

    def invoke(_handle, shape, request):
        assert shape == "settings.collection.query"
        query_requests.append(request)
        return {"items": [], "nextCursor": None, "total": 0}

    service = _SettingsHostService(invoke)
    service.call("register", [
        "fixture.plugin",
        {"sectionId": "data", "title": "Data", "fields": []},
        {"load": None, "save": None, "actions": {}},
    ])
    return service, query_requests


def _register(service, extra):
    return service.register_collection(
        "fixture.plugin",
        "data",
        {
            "collectionId": "entries",
            "title": "Entries",
            "columns": [{"key": "content", "label": "Content", "type": "string"}],
            **extra,
        },
        {"query": "cb_" + "a" * 32, "create": None, "update": None, "delete": None},
    )


@pytest.mark.parametrize(("extra", "surface", "expected"), [
    ({}, None, "character"),
    ({"scope": "character"}, None, "character"),
    ({"scope": "global"}, None, "global"),
    ({"scope": "global"}, "memory", "global"),
], ids=["legacy-default", "explicit-character", "explicit-global", "global-on-memory-surface"])
def test_collection_scope_is_public_metadata_independent_of_surface(settings, extra, surface, expected):
    service, query_requests = settings
    if surface:
        service.register_surface("fixture.plugin", "data", surface)
    _register(service, extra)

    section, = service.sections_for_plugin("fixture.plugin")
    collection, = section["collections"]
    assert collection["scope"] == expected

    query = {"cursor": None, "limit": 25, "search": "", "filters": {}}
    assert service.collection("query", "fixture.plugin", "data", "entries", query)["items"] == []
    assert query_requests == [query]


@pytest.mark.parametrize("scope", [None, "session", []], ids=["null", "unknown", "non-string"])
def test_invalid_collection_scope_is_rejected_without_registration(settings, scope):
    service, _query_requests = settings
    with pytest.raises(HostServiceError, match="SETTINGS_DESCRIPTOR_INVALID"):
        _register(service, {"scope": scope})
    section, = service.sections_for_plugin("fixture.plugin")
    assert section["collections"] == []
