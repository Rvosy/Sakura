"""Prepare Spine components and serve an isolated local plugin workbench.

Run with the bundled runtime: python -m tools.spine_preview --help.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from plugins.optional.sakura_spine.plugin import (
    _read_json, describe_resource, parse_control, parse_preview_control, relative_path,
)

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/optional/sakura_spine'


def resolve_inside(root, relative):
    path = (root / relative_path(relative)).resolve(strict=True)
    path.relative_to(root.resolve(strict=True))
    return path


def prepare(source: Path, output: Path, *, premultiplied_alpha=False, exclude_skins=()):
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError('输出目录已存在，请使用新目录')
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('输出目录不能与原始资源目录重叠')
    found, skipped = [], []
    for skeleton in sorted(source.rglob('*.json')):
        data = _read_json(skeleton, 16 * 1024 * 1024)
        if not data.get('bones') or not data.get('animations'):
            skipped.append({'file': skeleton.relative_to(source).as_posix(), 'reason': '无完整骨骼或动画'})
            continue
        atlas = next((p for p in (skeleton.with_suffix('.atlas'), skeleton.with_suffix('.atlas.txt')) if p.is_file()), None)
        if atlas is None:
            skipped.append({'file': skeleton.relative_to(source).as_posix(), 'reason': '缺少图集'})
            continue
        skeleton.relative_to(source)
        config = {'version': 1, 'skeleton': skeleton.relative_to(source).as_posix(), 'atlas': atlas.relative_to(source).as_posix()}
        config['premultipliedAlpha'] = premultiplied_alpha
        if exclude_skins:
            config['selectableSkins'] = [name for name in data.get('skins', {}) if name not in exclude_skins]
        if 'normal' in data.get('skins', {}) and 'normal' not in exclude_skins:
            config['defaultSkin'] = 'normal'
        description = describe_resource(config, lambda rel: resolve_inside(source, rel))
        # Effect-only skeletons have no standalone layout; retain them in source,
        # but do not pretend to know the game's layering/event choreography.
        metadata = data['skeleton']
        if metadata.get('width', 0) <= 0 or metadata.get('height', 0) <= 0:
            skipped.append({'file': config['skeleton'], 'reason': '独立特效，无角色布局'})
            continue
        found.append(description)
    if not found:
        raise ValueError('未找到可独立预览的 Spine 3.6 JSON 角色')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='spine-', dir=output.parent) as directory:
        staging = Path(directory) / 'components'
        staging.mkdir()
        models = []
        for description in found:
            data = description['rendererData']
            original = data['config']
            config = {**original, 'skeleton': 'model/skeleton.json', 'atlas': 'model/skeleton.atlas'}
            model_id = 'spine-' + uuid.uuid4().hex
            root = staging / model_id
            root.mkdir()
            files = {config['skeleton']: original['skeleton'], config['atlas']: original['atlas']}
            files.update({'model/' + page: relative for page, relative in data['textures'].items()})
            for relative, source_path in sorted(files.items()):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resolve_inside(source, source_path), target)
            (root / 'spine-resource.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            path = Path(original['skeleton'])
            name = '房间' if path.stem.casefold() == 'room' else path.stem
            if len(found) > 1 and len(path.parts) > 1:
                name = path.parts[0] + ' · ' + name
            models.append({'name': name, 'resource': {'id': model_id, 'type': 'spine.json@1', 'root': model_id, 'entry': 'spine-resource.json'}})
        default = next((m['resource']['id'] for m in models if m['name'] == '房间' or m['name'].endswith(' · 房间')), models[0]['resource']['id'])
        catalog = {'version': 1, 'default': default, 'models': models, 'skipped': skipped}
        (staging / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.rename(output)
    return catalog


def export_components(root: Path, output: Path):
    """Package prepared resources using Sakura's ordinary resource archive writer."""
    from app.config.character_resources import CharacterVisualResource
    from app.config.visual_archive import export_visual_archive

    root = root.resolve(strict=True)
    catalog = _read_json(root / 'catalog.json', 65536)
    if output.exists():
        raise ValueError('输出目录已存在，请使用新目录')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='spine-export-', dir=output.parent) as directory:
        staging = Path(directory) / 'archives'
        staging.mkdir()
        for index, model in enumerate(catalog['models'], 1):
            resource = CharacterVisualResource.from_mapping({**model['resource'], 'name': model['name']})
            model_root = resolve_inside(root, resource.root)
            config = _read_json(resolve_inside(model_root, resource.entry), 65536)
            description = describe_resource(config, lambda rel: resolve_inside(model_root, rel))
            data = description['rendererData']
            files = {config['skeleton'], config['atlas'], *data['textures'].values()}
            projection = {'entry': 'spine-resource.json', 'data': data['config'],
                          'assets': {path: resource.root + '/' + path for path in files},
                          'pluginRequirements': [{'kind': 'visual', 'type': 'spine.json@1',
                                                  'plugins': [{'id': 'sakura.visual.spine', 'name': 'Spine'}]}]}
            export_visual_archive(root, resource, projection, staging / f'spine-{index}.visual')
        staging.rename(output)
    return sorted(output.glob('*.visual'))


def create_preview_server(root: Path, port: int):
    root = root.resolve(strict=True)
    catalog = _read_json(root / 'catalog.json', 65536)
    models = {item['resource']['id']: item for item in catalog['models']}

    def describe(model_id):
        resource = models[model_id]['resource']
        model_root = resolve_inside(root, resource['root'])
        config = _read_json(resolve_inside(model_root, resource['entry']), 65536)
        description = describe_resource(config, lambda rel: resolve_inside(model_root, rel))
        draft_path = model_root / 'spine-draft.json'
        if draft_path.is_file():
            draft = _read_json(draft_path, 65536)
            if draft['skeleton'] != config['skeleton'] or draft['atlas'] != config['atlas']:
                raise ValueError('草稿资源路径不匹配')
            description = describe_resource(draft, lambda rel: resolve_inside(model_root, rel))
        return description

    # Freeze the parser contribution for this server's lifetime, like VisualHost.
    descriptions = {key: describe(key) for key in models}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, data, mime='application/json; charset=utf-8'):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.respond(403, {'error': '请求来源无效'})
                return
            route = unquote(urlsplit(self.path).path)
            try:
                if route == '/api/catalog':
                    self.respond(200, catalog)
                elif route.startswith('/api/describe/'):
                    self.respond(200, descriptions[route.removeprefix('/api/describe/')])
                else:
                    if route.startswith('/assets/'):
                        relative = route.removeprefix('/assets/')
                        # Only copied skeletons/atlases/textures and component data are served.
                        path = resolve_inside(root, relative)
                    elif route.startswith('/host-ui/'):
                        path = resolve_inside(Path(__file__).resolve().parents[1] / 'desktop/frontend', route.removeprefix('/host-ui/'))
                        if path.suffix not in ('.css', '.woff2', '.svg'):
                            raise ValueError('unsupported stylesheet asset')
                    else:
                        relative = 'preview/index.html' if route == '/' else route.removeprefix('/')
                        path = resolve_inside(PLUGIN, relative)
                    allowed = {'.mjs': 'text/javascript', '.js': 'text/javascript', '.html': 'text/html; charset=utf-8', '.css': 'text/css',
                               '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.json': 'application/json', '.atlas': 'text/plain', '.txt': 'text/plain'}
                    if path.suffix not in allowed:
                        raise ValueError('unsupported file')
                    self.respond(200, path.read_bytes(), allowed[path.suffix])
            except (OSError, ValueError, KeyError):
                self.respond(404, {'error': '资源不存在'})

        def do_POST(self):
            # A browser on another origin cannot write local drafts.
            origin = self.headers.get('Origin')
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if origin != expected_origin or self.headers.get('Content-Type') != 'application/json':
                self.respond(403, {'error': '请求来源无效'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('请求过大')
                body = json.loads(self.rfile.read(length))
                model_id = body['modelId']
                description = descriptions[model_id]
                if self.path == '/api/control':
                    parsed = parse_control(description['parserData'], body['payload'])
                    self.respond(200, parsed)
                elif self.path == '/api/preview':
                    # Editor controls are authored by the user. They do not expand
                    # the prompt/schema or the exported model parser's allowlist.
                    parsed = parse_preview_control(description['parserData'], body['payload'])
                    self.respond(200, parsed)
                elif self.path == '/api/draft':
                    model_root = resolve_inside(root, models[model_id]['resource']['root'])
                    updated = describe_resource(body['config'], lambda rel: resolve_inside(model_root, rel))
                    config = updated['rendererData']['config']
                    original = description['rendererData']['config']
                    if any(config[key] != original[key] for key in ('skeleton', 'atlas', 'modelControls')):
                        raise ValueError('资源绑定不可修改')
                    model_root = resolve_inside(root, models[model_id]['resource']['root'])
                    # Explicit preview draft, never an installed character's manifest.
                    target = model_root / 'spine-draft.json'
                    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=model_root, suffix='.tmp', delete=False) as handle:
                        temp_path = Path(handle.name)
                        json.dump(config, handle, ensure_ascii=False, indent=2)
                    try:
                        temp_path.replace(target)
                    finally:
                        temp_path.unlink(missing_ok=True)
                    descriptions[model_id] = updated
                    self.respond(200, {'saved': True})
                else:
                    self.respond(404, {'error': '请求不存在'})
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.respond(400, {'error': str(error) if isinstance(error, ValueError) else '请求无效'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(root: Path, port: int):
    server = create_preview_server(root, port)
    print(f'Spine preview: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description='Spine 3.6 本地资源预览')
    sub = parser.add_subparsers(dest='command', required=True)
    preparation = sub.add_parser('prepare')
    preparation.add_argument('source', type=Path)
    preparation.add_argument('output', type=Path)
    preparation.add_argument('--premultiplied-alpha', action='store_true', help='输入贴图已预乘透明度')
    preparation.add_argument('--exclude-skin', action='append', default=[], help='保留资源但不将此皮肤暴露为可选表情')
    preview = sub.add_parser('serve')
    preview.add_argument('root', type=Path)
    preview.add_argument('--port', type=int, default=8786)
    export = sub.add_parser('export')
    export.add_argument('root', type=Path)
    export.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.source, args.output, premultiplied_alpha=args.premultiplied_alpha, exclude_skins=args.exclude_skin)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == 'export':
        print('\n'.join(str(path) for path in export_components(args.root, args.output)))
    else:
        serve(args.root, args.port)


if __name__ == '__main__':
    main()
