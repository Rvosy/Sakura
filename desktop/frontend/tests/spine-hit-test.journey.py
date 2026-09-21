"""Compare CPU point picking with the real WebGL output; readPixels is test-only."""
import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def run():
    plugin = load('spine_plugin', ROOT / 'plugins/optional/sakura_spine/plugin.py')
    journey = load('spine_journey', Path(__file__).with_name('spine-plugin.journey.py'))
    model = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'artifacts/spine/196104-room/spine-1'
    config = json.loads((model / 'spine-resource.json').read_text(encoding="utf-8"))
    data = plugin.describe_resource(config, lambda path: model / path)['rendererData']
    journey.Handler.assets['/hit-model'] = model
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(journey.Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL') or ('msedge' if sys.platform == 'win32' else None))
            page = browser.new_page(viewport={'width': 480, 'height': 640}, device_scale_factor=1.25)
            page.goto(origin + '/desktop/frontend/prototypes/asr/')
            result = page.evaluate("""async data => {
                document.body.replaceChildren(); document.body.style.margin='0';
                const container = document.createElement('div');
                Object.assign(container.style, {width:'480px', height:'640px'}); document.body.append(container);
                const {createRenderer} = await import('/plugins/optional/sakura_spine/renderer.mjs');
                const renderer = await createRenderer({container, rendererData:data, bindingId:'hit', resourceId:'hit',
                    signal:new AbortController().signal, enableHitTest:true,
                    resolveAssetUrl: path => '/hit-model/' + Array.from(new TextEncoder().encode(path), c=>c.toString(16).padStart(2,'0')).join('')});
                renderer.setPaused(true);
                const canvas=container.querySelector('canvas'), gl=canvas.getContext('webgl');
                const failures=[], counts={transparent:0,opaque:0,edge:0};
                // Rendering and picking share the pose, including skins, transformed vertices and clipping.
                for (const skin of ['normal', 'smile']) {
                    const applied=renderer.applyControl({version:1,bindingId:'hit',resourceId:'hit',state:{skin}}, {sequence:skin==='normal'?1:2});
                    if (!applied && skin === 'smile') continue;
                    for(let y=0.025;y<1;y+=0.05) for(let x=0.025;x<1;x+=0.05) {
                        const hit=await renderer.hitTest([x,y]);
                        const pixels=new Uint8Array(3*3*4);
                        gl.readPixels(Math.floor(x*canvas.width)-1, Math.floor((1-y)*canvas.height)-1,3,3,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
                        const alpha=Array.from({length:9},(_,i)=>pixels[i*4+3]);
                        // Exclude the rasterizer's antialias footprint from the oracle.
                        if (alpha.every(a=>a===0)) {counts.transparent++; if(hit) failures.push({skin,x,y,hit,alpha});}
                        else if (alpha.every(a=>a>32)) {counts.opaque++; if(!hit) failures.push({skin,x,y,hit,alpha});}
                        else counts.edge++;
                    }
                }
                const stats=renderer.hitTestStats(); renderer.dispose(); return {counts,failures,stats};
            }""", data)
            assert result['counts']['transparent'] > 100 and result['counts']['opaque'] > 20, result
            assert not result['failures'], result
            print(json.dumps(result, indent=2))
            mapping = page.evaluate("""async data => {
                document.body.replaceChildren();
                const link=document.createElement('link'); link.rel='stylesheet'; link.href='/desktop/frontend/styles.css';
                await new Promise((resolve,reject)=>{link.onload=resolve;link.onerror=reject;document.head.append(link);});
                const outer=document.createElement('div'); outer.className='visual-renderer';
                Object.assign(outer.style,{left:'20px',top:'30px',width:'440px',height:'600px'});
                outer.style.setProperty('--visual-surface-width','330px');
                outer.style.setProperty('--visual-surface-height','510px');
                document.body.append(outer);
                const {createRenderer}=await import('/plugins/optional/sakura_spine/renderer.mjs');
                const {createRendererHost}=await import('/desktop/frontend/pet/renderer-host.js');
                const {attachDynamicHitTest}=await import('/desktop/frontend/pet/dynamic-hit-test.js');
                let renderer, handler, session, answer;
                const host=createRendererHost({container:outer,
                    services:{setHitTest:(hitTest,context)=>attachDynamicHitTest({
                        // Before the fix RendererHost did not expose the mounted surface;
                        // app.js always used this outer slot for coordinate conversion.
                        container:context.container ?? outer, hitTest, signal:context.signal,
                        listen:async(_name,callback)=>{handler=callback;return ()=>{};},
                        invoke:async(command,args)=>{if(args.enabled) session=args.session;if(command==='submit_dynamic_hit_test') answer=args.hit;return true;}
                    })},
                    loadModule:async()=>({mount:async({container,host,signal})=>{
                        renderer=await createRenderer({container,rendererData:data,bindingId:'hit',resourceId:'hit',signal,enableHitTest:true,
                            resolveAssetUrl:path=>'/hit-model/'+Array.from(new TextEncoder().encode(path),c=>c.toString(16).padStart(2,'0')).join('')});
                        renderer.setPaused(true);
                        return {ready:host.setHitTest(renderer.hitTest),applyState(){},destroy:renderer.dispose};
                    }})});
                await host.bind({visual:{bindingId:'hit',resourceId:'hit',renderer:'fixture'}});
                const canvas=outer.querySelector('canvas'), gl=canvas.getContext('webgl');
                const failures=[], counts={opaque:0,transparent:0};
                let id=0;
                for(const scale of [0.75,1,1.4]) {
                    outer.style.setProperty('--portrait-render-scale',String(scale));
                    for(let y=0.05;y<1;y+=0.1) for(let x=0.05;x<1;x+=0.1) {
                        const rect=canvas.getBoundingClientRect();
                        await handler({payload:{session,id:++id,point:[rect.left+x*rect.width,rect.top+y*rect.height]}});
                        const pixels=new Uint8Array(36);
                        gl.readPixels(Math.floor(x*canvas.width)-1,Math.floor((1-y)*canvas.height)-1,3,3,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
                        const alpha=Array.from({length:9},(_,i)=>pixels[i*4+3]);
                        if(alpha.every(a=>a>32)){counts.opaque++;if(!answer)failures.push({scale,x,y,kind:'opaque passed through'});}
                        else if(alpha.every(a=>a===0)){counts.transparent++;if(answer)failures.push({scale,x,y,kind:'transparent blocked'});}
                    }
                }
                host.destroy();return {counts,failures};
            }""", data)
            assert not mapping['failures'], mapping
            print(json.dumps({'surfaceMapping': mapping}, indent=2))
            browser.close()
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': run()
