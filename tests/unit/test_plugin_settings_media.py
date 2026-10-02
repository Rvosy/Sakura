import base64

import pytest

from app.core_host.plugin_host_services import _SettingsHostService, HostServiceError


def test_image_load_action_clear_and_readonly_projection():
    image = {"dataUrl": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(), "alt": "登录二维码"}
    current = {"qr": image}
    calls = []
    def callback(_handle, method, *args):
        calls.append((method, args))
        return {"values": {"qr": None}} if method == "settings.action" else current
    settings = _SettingsHostService(callback)
    handle = "cb_" + "b" * 32
    settings.call("register", ["fixture", {
        "sectionId": "login", "title": "登录", "presentation": {"component": "form"},
        "fields": [{"key": "qr", "label": "扫码", "type": "image"}],
        "actions": [{"actionId": "clear", "label": "取消"}],
    }, {"load": handle, "save": None, "actions": {"clear": handle}}])
    assert settings.sections_for_plugin("fixture")[0]["values"]["qr"] == image
    assert settings.action("fixture", "login", "clear", {"qr": image})[1]["values"]["qr"] is None
    assert calls[-1] == ("settings.action", ({},))
    for url in ("https://example.com/track.png", "file:///secret", "data:image/svg+xml;base64,AAAA", "data:image/png;base64,AAAA"):
        current["qr"] = {"dataUrl": url, "alt": "二维码"}
        section = settings.sections_for_plugin("fixture")[0]
        assert section["values"]["qr"] is None and section["reasonCode"] == "SETTINGS_VALUE_INVALID"


def test_record_bindings_require_readonly_items_and_declared_actions():
    settings = _SettingsHostService(lambda *_: {})
    handle = "cb_" + "c" * 32
    descriptor = {"sectionId": "devices", "title": "设备",
        "presentation": {"component": "record-table", "itemsField": "items", "valueField": "grants",
                         "columns": [{"key": "name", "label": "名称", "type": "readonly"}]},
        "fields": [{"key": "items", "label": "设备", "type": "data", "readonly": False, "default": []},
                   {"key": "grants", "label": "权限", "type": "data", "default": {}}]}
    handles = {"load": handle, "save": handle, "actions": {}}
    with pytest.raises(HostServiceError, match="SETTINGS_PRESENTATION_INVALID"):
        settings.call("register", ["fixture", descriptor, handles])
    descriptor["fields"][0]["readonly"] = True
    settings.call("register", ["fixture", descriptor, handles])
    assert settings.sections_for_plugin("fixture")[0]["presentation"]["columns"][0]["readonly"]
