from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest

from plugins.optional.sakura_spine.plugin import (
    SpineService, atlas_pages, describe_resource, parse_control, parse_preview_control,
)
from tools.spine_preview import prepare, resolve_inside


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=')
ATLAS = 'texture.png\nsize: 1,1\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nbody\n  rotate: false\n  xy: 0,0\n  size: 1,1\n  orig: 1,1\n  offset: 0,0\n  index: -1\n'


@pytest.fixture
def spine_resource(tmp_path):
    root = tmp_path / 'resource'
    root.mkdir()
    skeleton = {
        'skeleton': {'spine': '3.6.53', 'width': 1, 'height': 1},
        'bones': [{'name': 'root'}],
        'skins': {'default': {}, 'normal': {}, 'smile': {}},
        'animations': {'idle': {}, 'wave': {}},
    }
    (root / 'skeleton.json').write_text(json.dumps(skeleton), encoding='utf-8')
    (root / 'skeleton.atlas.txt').write_text(ATLAS, encoding='utf-8')
    (root / 'texture.png').write_bytes(PNG)
    config = {'version': 1, 'skeleton': 'skeleton.json', 'atlas': 'skeleton.atlas.txt'}
    (root / 'spine-resource.json').write_text(json.dumps(config), encoding='utf-8')
    return root, config, skeleton


def test_resource_describes_actual_controls_and_freezes_parser_snapshot(spine_resource):
    root, config, skeleton = spine_resource
    description = describe_resource(config, lambda rel: resolve_inside(root, rel))
    assert description['outputSchema']['properties']['skin']['enum'] == ['default', 'normal', 'smile']
    assert description['rendererData']['textures'] == {'texture.png': 'texture.png'}
    assert description['rendererData']['config']['defaultAnimation'] == 'idle'
    skeleton['animations']['unbound'] = {}
    (root / 'skeleton.json').write_text(json.dumps(skeleton), encoding='utf-8')
    snapshot = description['parserData']
    assert parse_control(snapshot, {'skin': 'smile', 'speed': 1.5, 'action': 'wave'}) == {
        'state': {'skin': 'smile', 'speed': 1.5}, 'actions': [{'animation': 'wave'}],
    }
    assert parse_control(snapshot, {}) == {'state': {}, 'actions': []}
    assert parse_control(snapshot, None, {'portrait': 'happy'}) == {'state': {}, 'actions': []}
    with pytest.raises(ValueError, match='SPINE_CONTROL_INVALID'):
        parse_control(snapshot, {'action': 'unbound'})


@pytest.mark.parametrize('payload', [
    {'skin': 'unknown'}, {'action': 'unknown'}, {'speed': True}, {'speed': float('nan')},
    {'speed': 0}, {'speed': 3.1}, {'action': ['wave']}, {'animation': None}, {'bone': 'root'}, [],
])
def test_rejects_invalid_plugin_controls_without_coercion(payload):
    with pytest.raises(ValueError, match='SPINE_CONTROL_INVALID'):
        parse_control({'animations': ['idle', 'wave'], 'skins': ['default'],
                       'modelControls': ['skin', 'animation', 'speed', 'action']}, payload)


@pytest.mark.parametrize('single_animation', [True, False])
def test_skin_only_model_protocol_keeps_speed_and_animation_in_editor(spine_resource, single_animation):
    root, config, skeleton = spine_resource
    if single_animation:
        # Any single-loop resource gets this default, regardless of folder/name.
        skeleton['animations'] = {'breathe': {}}
        (root / 'skeleton.json').write_text(json.dumps(skeleton), encoding='utf-8')
    else:
        config['modelControls'] = ['skin']
    description = describe_resource(config, lambda rel: resolve_inside(root, rel))
    assert set(description['outputSchema']['properties']) == {'skin'}
    assert description['outputSchema']['additionalProperties'] is False
    assert 'speed' not in description['prompt']
    assert 'animations' not in description['prompt']
    assert description['rendererData']['config']['speed'] == 1
    snapshot = description['parserData']
    assert parse_control(snapshot, {'skin': 'smile'}) == {'state': {'skin': 'smile'}, 'actions': []}
    animation = snapshot['animations'][0]
    for extra in ({'speed': 1.5}, {'animation': animation}, {'action': animation}):
        with pytest.raises(ValueError, match='SPINE_CONTROL_INVALID'):
            parse_control(snapshot, {'skin': 'smile', **extra})
    assert parse_preview_control(snapshot, {'speed': 1.5}) == {'state': {'speed': 1.5}, 'actions': []}
    assert parse_preview_control(snapshot, {'action': animation})['actions'] == [{'animation': animation}]


@pytest.mark.parametrize('controls', [['unknown'], ['skin', 'skin'], 'skin'])
def test_invalid_model_controls_are_rejected_as_resource_config(spine_resource, controls):
    root, config, _ = spine_resource
    with pytest.raises(ValueError, match='SPINE_CONFIG_INVALID'):
        describe_resource({**config, 'modelControls': controls}, lambda rel: resolve_inside(root, rel))


@pytest.mark.parametrize('version,bones,expected', [
    ('3.8.99', [{'name': 'root'}], 'SPINE_VERSION_UNSUPPORTED'),
    ('4.2.0', [{'name': 'root'}], 'SPINE_VERSION_UNSUPPORTED'),
    ('3.6.53', [], 'SPINE_SKELETON_INCOMPLETE'),
])
def test_rejects_wrong_runtime_and_metadata_only_exports(spine_resource, version, bones, expected):
    root, config, skeleton = spine_resource
    skeleton['skeleton']['spine'] = version
    skeleton['bones'] = bones
    (root / 'skeleton.json').write_text(json.dumps(skeleton), encoding='utf-8')
    with pytest.raises(ValueError, match=expected):
        describe_resource(config, lambda rel: resolve_inside(root, rel))


@pytest.mark.parametrize('page', ['../private.png', 'C:/private.png', 'https://example.com/private.png', '%2e%2e/private.png', 'foo\\private.png'])
def test_atlas_cannot_reference_external_resources(page):
    with pytest.raises(ValueError, match='SPINE_RESOURCE_PATH_INVALID'):
        atlas_pages(ATLAS.replace('texture.png', page))


def test_missing_texture_fails_before_renderer_mount(spine_resource):
    root, config, _ = spine_resource
    (root / 'texture.png').unlink()
    with pytest.raises(FileNotFoundError):
        describe_resource(config, lambda rel: resolve_inside(root, rel))


def test_service_resolves_only_the_selected_character_resource(spine_resource):
    root, config, _ = spine_resource
    calls = []

    class Character:
        def resolve_resource(self, character_id, path):
            calls.append((character_id, path))
            return str(resolve_inside(root, path.removeprefix('visual/')))

    description = SpineService(Character()).describe({'characterId': 'alice', 'resource': {
        'id': 'model', 'type': 'spine.json@1', 'root': 'visual', 'entry': 'spine-resource.json',
    }})
    assert description['rendererData']['config']['skeleton'] == config['skeleton']
    assert all(character == 'alice' and path.startswith('visual/') for character, path in calls)
    assert not any(str(root) in str(value) for value in description.values())


def test_preparation_keeps_source_and_copies_only_component_dependencies(spine_resource, tmp_path):
    root, _, _ = spine_resource
    (root / 'unrelated.txt').write_text('preserve me', encoding='utf-8')
    (root / 'metadata.json').write_text('{"animations":{"idle":{}}}', encoding='utf-8')
    source_json = (root / 'skeleton.json').read_bytes()
    output = tmp_path / 'prepared'
    catalog = prepare(root, output)
    assert len(catalog['models']) == 1
    component = output / catalog['models'][0]['resource']['root']
    assert (component / 'skeleton.json').read_bytes() == source_json
    assert (root / 'skeleton.json').read_bytes() == source_json
    assert not (component / 'unrelated.txt').exists()
    assert not (component / 'metadata.json').exists()
    assert json.loads((component / 'spine-resource.json').read_text())['defaultSkin'] == 'normal'
    with pytest.raises(ValueError, match='输出目录已存在'):
        prepare(root, output)


def test_installed_spine_runs_through_real_v4_host_and_expires_on_disable(spine_resource, tmp_path):
    from app.agent.tools import ToolRegistry
    from app.config.character_resources import CharacterVisualResource
    from app.core_host.plugin_application import PluginApplicationHost
    from app.plugins.installer import LocalPluginInstaller
    from app.plugins.inventory import PluginDesiredStateStore
    from app.storage.runtime_roots import RuntimeRoots

    source, _, _ = spine_resource
    roots = RuntimeRoots(tmp_path / 'distribution', tmp_path / 'user')
    roots.distribution_root.mkdir()
    package = roots.user_root / 'characters/alice'
    shutil.copytree(source, package / 'visual')
    (package / 'card.md').write_text('测试角色', encoding='utf-8')
    (package / 'default.png').write_bytes(PNG)
    # Current public loader still requires a portrait; the Spine provider never uses it.
    (package / 'character.json').write_text(json.dumps({
        'id': 'alice', 'display_name': 'Alice', 'card': 'card.md', 'portrait': {'default': 'default.png'},
    }), encoding='utf-8')
    plugin = Path(__file__).resolve().parents[2] / 'plugins/optional/sakura_spine'
    installed = LocalPluginInstaller(roots).install(plugin, 'folder')
    PluginDesiredStateStore(roots.user_root).set('sakura.visual.spine', True)
    application = PluginApplicationHost(roots, 'spine-test', ToolRegistry())
    try:
        application.start()
        host = application.application.visuals
        assert host.candidates('spine.json@1')[0]['reasonCode'] == 'READY'
        resource = CharacterVisualResource('model', 'spine.json@1', 'visual', 'spine-resource.json')
        binding = host.bind('alice', package, resource)
        assert binding.description['outputSchema']['properties']['action']['enum'] == ['idle', 'wave']
        envelope = {'version': 1, 'resourceId': 'model', 'payload': {'skin': 'smile', 'action': 'wave'}}
        result = binding.parse_control(envelope)
        assert result.reason_code == 'READY'
        assert result.control['state'] == {'skin': 'smile'}
        assert result.control['actions'] == [{'animation': 'wave'}]
        invalid = binding.parse_control({**envelope, 'payload': {'speed': 5}})
        assert invalid.control is None
        assert invalid.reason_code == 'VISUAL_CONTROL_REJECTED'
        application.set_enabled(installed.install_id, False)
        assert binding.parse_control(envelope).reason_code == 'VISUAL_BINDING_EXPIRED'
    finally:
        application.close()


def test_preview_draft_survives_reload_without_rewriting_component_or_source(spine_resource, tmp_path):
    import http.client
    import threading
    from tools.spine_preview import create_preview_server

    source, _, _ = spine_resource
    output = tmp_path / 'draft-test'
    catalog = prepare(source, output)
    model_id = catalog['default']
    component = output / model_id
    resource_config = json.loads((component / 'spine-resource.json').read_text())
    resource_config['modelControls'] = ['skin']
    (component / 'spine-resource.json').write_text(json.dumps(resource_config), encoding='utf-8')
    original = (component / 'spine-resource.json').read_bytes()
    source_before = (source / 'skeleton.json').read_bytes()

    def exercise(write):
        server = create_preview_server(output, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            client.request('GET', f'/api/describe/{model_id}')
            config = json.loads(client.getresponse().read())['rendererData']['config']
            if not write:
                assert config['defaultSkin'] == 'smile'
                assert config['speed'] == 1.5
                assert config['modelControls'] == ['skin']
                return
            config['defaultSkin'] = 'smile'
            config['speed'] = 1.5
            headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}'}
            # Editing speed remains available while the model route rejects it.
            for route, expected in [('/api/control', 400), ('/api/preview', 200)]:
                client.request('POST', route, json.dumps({'modelId': model_id, 'payload': {'speed': 1.5}}), headers)
                response = client.getresponse()
                assert response.status == expected
                response.read()
            body = json.dumps({'modelId': model_id, 'config': config})
            for origin, expected in [('http://example.invalid', 403), (f'http://127.0.0.1:{server.server_port}', 200)]:
                client.request('POST', '/api/draft', body, {'Content-Type': 'application/json', 'Origin': origin})
                response = client.getresponse()
                assert response.status == expected
                response.read()
                if expected == 403:
                    assert not (component / 'spine-draft.json').exists()
            client.request('GET', f'/api/describe/{model_id}')
            updated = json.loads(client.getresponse().read())
            assert updated['rendererData']['config']['defaultSkin'] == 'smile'
            assert updated['rendererData']['config']['speed'] == 1.5
            assert set(updated['outputSchema']['properties']) == {'skin'}
            client.request('GET', '/assets/../private.txt')
            response = client.getresponse()
            assert response.status == 404
            response.read()
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    exercise(True)
    exercise(False)
    assert (component / 'spine-resource.json').read_bytes() == original
    assert (source / 'skeleton.json').read_bytes() == source_before
