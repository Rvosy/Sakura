"""The product host remains manageable without a model implementation or SDK."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]


def test_core_without_model_plugin_or_transport_dependencies_keeps_character_and_settings(tmp_path):
    user, distribution = tmp_path / "user", tmp_path / "distribution"
    shutil.copytree(REPO / "tests/fixtures/runtime_v2/wp_3_01/ready", user)
    (user / "characters/sakura/portraits/neutral.txt").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII="))
    for name in ("sakura_assistant", "sakura_portrait"):
        shutil.copytree(REPO / "plugins/builtin" / name, distribution / "plugins/builtin" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    script = '''
import importlib.abc, sys
from pathlib import Path
class NoModelDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openai', 'httpx', 'httpcore', 'socksio'}:
            raise AssertionError('Core imported a model dependency: ' + fullname)
sys.meta_path.insert(0, NoModelDependencies())
sys.path.insert(0, sys.argv[1])
from app.core_host.server import HostConfig, ReadinessController
from app.core_host.provider_settings import ProviderSettingsBoundary
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.storage.runtime_roots import RuntimeRoots
config = HostConfig(RuntimeRoots(Path(sys.argv[2]), Path(sys.argv[3])), 'no-model', 'a' * 32)
controller = ReadinessController(config)
try:
    controller.begin({})
    controller._worker.join(10)
    assert not controller._worker.is_alive()
    snapshot = controller.snapshot()
    assert snapshot['readiness'] == 'setup_required', snapshot
    assert snapshot['characterPresentation']['characterId'] == 'sakura', snapshot
    assert controller.published_session() is None
    application = controller.published_plugin_application()
    plugins = PluginSettingsBoundary(config.generation_id, config.generation_credential, config.roots,
        application_provider=lambda: application).snapshot()
    assert any(item['pluginId'] == 'sakura.assistant.default' and item['state'] == 'active' for item in plugins['plugins'])
    settings = ProviderSettingsBoundary(config.generation_id, config.generation_credential, config.user_root,
        plugin_application_provider=lambda: application)
    settings.enable()
    models = settings._snapshot()
    assert models['providers'] == []
    assert models['model_slots'][0]['reasonCode'] == 'MODEL_REFERENCE_UNAVAILABLE'
    assistant = next(p for p in plugins['plugins'] if p['pluginId'] == 'sakura.assistant.default')
    assert any(s.get('placement', {}).get('pageId') == 'host:model' for s in assistant['sections'])  # Assistant settings remain editable.
finally:
    controller.close()
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(REPO), str(distribution), str(user)],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout + result.stderr
