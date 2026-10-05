"""Browser regression for portrait image handoff through playback and history review."""
from __future__ import annotations

import functools
import http.server
import os
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            channel = os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None)
            browser = playwright.chromium.launch(channel=channel)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/tests/fixtures/visual_numeric/")
            portrait_handoff = page.evaluate("""async () => {
              const {mount} = await import('/plugins/builtin/sakura_portrait/frontend/renderer.js');
              const {createRendererHost} = await import('/desktop/frontend/pet/renderer-host.js');
              const target=document.createElement('div'); target.style.cssText='width:64px;height:64px'; document.body.append(target);
              const canvas=document.createElement('canvas'); canvas.width=canvas.height=64;
              const ctx=canvas.getContext('2d');
              const assets={};
              for (const [key,color] of [['A','red'],['B','blue']]) {
                ctx.fillStyle=color; ctx.fillRect(0,0,64,64); assets[key]=canvas.toDataURL();
              }
              const resource={bindingId:'a'.repeat(32),resourceId:'portrait',renderer:'fixture:portrait',assets,
                data:{defaultKey:'A',metadata:{A:{width:64,height:64},B:{width:64,height:64}}}};
              const observations=[];
              let painted,instance,holdFinish;
              const renderer=createRendererHost({container:target,loadModule:async()=>({mount:options=>instance=mount(options)}),services:{
                prepareSurface:()=>true,
                setSurface:async()=>{
                  const root=target.firstElementChild.shadowRoot;
                  if (!root.querySelector('.is-transitioning')) return true;
                  painted=root.querySelector('.portrait-image--next');
                  await Promise.all(root.querySelector('.portrait-frame').getAnimations({subtree:true}).map(animation=>animation.finished));
                  return true;
                },
                finishSurface:()=>{
                  const root=target.firstElementChild.shadowRoot;
                  const current=root.querySelector('.portrait-image--current');
                  ctx.clearRect(0,0,64,64); ctx.drawImage(current,0,0);
                  observations.push({retainedPaintedImage:current===painted,decoded:current.complete,
                    pixel:[...ctx.getImageData(0,0,1,1).data],
                    clean:!root.querySelector('.portrait-image--next').hasAttribute('src') && !root.querySelector('.is-transitioning')});
                  if (holdFinish) {
                    const hold=holdFinish; holdFinish=null;
                    return hold();
                  }
                  return true;
                },
                cancelSurface:()=>{},
              }});
              await renderer.bind({visual:resource});
              const segments=[{},{}];
              renderer.begin('reply');
              for (const [index,key] of ['A','B'].entries()) {
                await renderer.play({version:1,bindingId:resource.bindingId,resourceId:resource.resourceId,state:{key}},'reply',index,segments[index]);
              }
              // Exercise the same snapshot/cancel/applyState chain as the bubble's previous/next buttons.
              await renderer.review(segments[0]);
              await renderer.review(segments[1]);
              let entered,release;
              const waiting=new Promise(resolve=>{entered=resolve});
              const gate=new Promise(resolve=>{release=resolve});
              holdFinish=()=>{entered(); return gate;};
              const restoring=renderer.review(segments[0]);
              await waiting;
              const observe=()=>{
                const current=target.firstElementChild.shadowRoot.querySelector('.portrait-image--current');
                ctx.clearRect(0,0,64,64); ctx.drawImage(current,0,0);
                return {key:instance.snapshotState().key,pixel:[...ctx.getImageData(0,0,1,1).data]};
              };
              const duringFinish=observe();
              await renderer.review(segments[1]);
              release(true); await restoring;
              const afterReview=observe();
              renderer.destroy(); target.remove();
              return {observations,duringFinish,afterReview};
            }""")
            assert portrait_handoff["duringFinish"] == {"key": "A", "pixel": [255, 0, 0, 255]}, portrait_handoff
            assert portrait_handoff["afterReview"] == {"key": "B", "pixel": [0, 0, 255, 255]}, portrait_handoff
            assert portrait_handoff["observations"] == [
                {"retainedPaintedImage": True, "decoded": True, "pixel": pixel, "clean": True}
                for pixel in ([0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 255, 255])
            ], portrait_handoff
            portrait_pending = page.evaluate("""async () => {
              const {mount} = await import('/plugins/builtin/sakura_portrait/frontend/renderer.js');
              const canvas=document.createElement('canvas'); canvas.width=canvas.height=64;
              const ctx=canvas.getContext('2d');
              const assets={};
              for (const [key,color] of [['A','red'],['B','blue'],['C','lime']]) {
                ctx.fillStyle=color; ctx.fillRect(0,0,64,64); assets[key]=canvas.toDataURL();
              }
              const observations=[];
              for (const phase of ['decode-cancel','decode-replace','commit-cancel','commit-replace','commit-failure']) {
                const target=document.createElement('div'); target.style.cssText='width:64px;height:64px'; document.body.append(target);
                const signal=new AbortController();
                let entered,release;
                const waiting=new Promise(resolve=>{entered=resolve});
                const gate=new Promise(resolve=>{release=resolve});
                const errors=[];
                const instance=mount({container:target,signal:signal.signal,
                  resource:{assets,data:{defaultKey:'A',metadata:Object.fromEntries(['A','B','C'].map(key=>[key,{width:64,height:64}]))}},
                  host:{prepareSurface:()=>true,setSurface:async({assetKey})=>{
                    if (assetKey==='B' && phase.startsWith('commit')) {
                      entered(); await gate;
                      if (phase==='commit-failure') throw new Error('commit failed');
                    }
                    return true;
                  },finishSurface:()=>true,cancelSurface:()=>{},
                  reportError:code=>errors.push(code),unavailable:()=>errors.push('unavailable')}});
                await instance.ready;
                const root=target.shadowRoot;
                const spare=root.querySelector('.portrait-image--next');
                const decode=spare.decode.bind(spare);
                spare.decode=async()=>{
                  const source=spare.src;
                  await decode();
                  if (source===assets.B && phase.startsWith('decode')) { entered(); await gate; }
                };
                const pending=instance.applyState({key:'B'},{signal:signal.signal});
                await waiting;
                const waitingForDecode=phase.startsWith('decode') && !root.querySelector('.is-transitioning');
                if (phase.endsWith('cancel')) instance.cancel();
                if (phase.endsWith('replace')) await instance.applyState({key:'C'},{signal:signal.signal});
                release(); await pending;
                ctx.clearRect(0,0,64,64); ctx.drawImage(root.querySelector('.portrait-image--current'),0,0);
                observations.push({phase,waitingForDecode,errors,pixel:[...ctx.getImageData(0,0,1,1).data],
                  key:instance.snapshotState().key,
                  clean:!root.querySelector('.portrait-image--next').hasAttribute('src') && !root.querySelector('.is-transitioning')});
                instance.destroy(); target.remove();
              }
              return observations;
            }""")
            for observed in portrait_pending:
                phase = observed["phase"]
                replaced = phase.endswith("replace")
                assert observed == {
                    "phase": phase,
                    "waitingForDecode": phase.startswith("decode"),
                    "errors": ["PORTRAIT_COMMIT_FAILED"] if phase == "commit-failure" else [],
                    "pixel": [0, 255, 0, 255] if replaced else [255, 0, 0, 255],
                    "key": "C" if replaced else "A",
                    "clean": True,
                }, observed
            form_transitions = page.evaluate("""async () => {
              const {createRendererHost} = await import('/desktop/frontend/pet/renderer-host.js');
              const css=document.createElement('link'); css.rel='stylesheet'; css.href='/desktop/frontend/styles.css';
              const loaded=new Promise(resolve=>{css.onload=resolve}); document.head.append(css); await loaded;
              const target=document.createElement('div');
              target.style.cssText='position:relative;width:320px;height:480px;--portrait-render-scale:1';
              document.body.append(target);
              const canvas=document.createElement('canvas'); canvas.width=64; canvas.height=128;
              const ctx=canvas.getContext('2d'); ctx.fillStyle='red'; ctx.fillRect(0,0,64,128);
              const portrait={bindingId:'portrait-a',resourceId:'portrait',
                renderer:'/plugins/builtin/sakura_portrait/frontend/renderer.js',assets:{A:canvas.toDataURL()},
                data:{defaultKey:'A',metadata:{A:{width:64,height:128}}}};
              const numeric={bindingId:'numeric-b',resourceId:'numeric',
                renderer:'/tests/fixtures/visual_numeric/frontend/renderer.js',assets:{},data:{maxAngle:30}};
              const destroyed=[],settled=[],surfaces=[];
              let animations=[],started;
              const animate=Element.prototype.animate;
              // Control the browser's actual Animation clock, without sleeping.
              Element.prototype.animate=function(...args) {
                const animation=animate.apply(this,args);
                if (this.parentElement===target) {
                  animation.pause(); animations.push(animation);
                  if (animations.length===2) started();
                }
                return animation;
              };
              const renderer=createRendererHost({container:target,
                loadModule:async url=>{
                  const module=await import(url);
                  return {mount:options=>{
                    const instance=module.mount(options);
                    return {...instance,destroy(){destroyed.push(options.resource.bindingId);instance.destroy();}};
                  }};
                },
                services:{
                  setSurface:({width,height},{replacing})=>{
                    surfaces.push(replacing);
                    target.style.setProperty('--visual-surface-width',`${width}px`);
                    target.style.setProperty('--visual-surface-height',`${height}px`);
                    return true;
                  },
                  finishSurface:()=>{settled.push(true);return true;},
                  cancelSurface:()=>{},
                },
              });
              async function transition(visual) {
                animations=[];
                const begun=new Promise(resolve=>{started=resolve});
                const pending=renderer.bind({visual});
                await begun;
                for(const animation of animations) animation.currentTime=150;
                return {pending};
              }
              try {
                await renderer.bind({visual:portrait});
                const old=target.firstElementChild;
                const before=old.getBoundingClientRect();
                const first=await transition(numeric);
                const during=old.getBoundingClientRect();
                const opacity=[...target.children].map(node=>Number(getComputedStyle(node).opacity));
                const intermediate={count:target.children.length,alive:old.shadowRoot.querySelector('img').complete,
                  frozen:before.width===during.width && before.height===during.height,opacity,destroyed:[...destroyed]};
                for(const animation of animations) animation.finish();
                const completed=await first.pending;
                const second=await transition({...portrait,bindingId:'portrait-c'});
                const third=await transition({...numeric,bindingId:'numeric-d'});
                const cancelled=await second.pending;
                for(const animation of animations) animation.finish();
                const last=await third.pending;
                const result={intermediate,completed,cancelled,last,count:target.children.length,
                  current:renderer.current().bindingId,destroyed:[...destroyed],settled:settled.length,surfaces};
                renderer.destroy();
                result.destroyedAfterClose=[...destroyed];
                return result;
              } finally {Element.prototype.animate=animate;renderer.destroy();target.remove();}
            }""")
            assert form_transitions["intermediate"]["count"] == 2
            assert form_transitions["intermediate"]["alive"]
            assert form_transitions["intermediate"]["frozen"]
            assert form_transitions["intermediate"]["destroyed"] == []
            assert all(0.45 < value < 0.55 for value in form_transitions["intermediate"]["opacity"])
            assert form_transitions["completed"] and form_transitions["last"]
            assert not form_transitions["cancelled"]
            assert form_transitions["count"] == 1 and form_transitions["current"] == "numeric-d"
            assert form_transitions["destroyed"] == ["portrait-a", "numeric-b", "portrait-c"]
            assert form_transitions["destroyedAfterClose"] == ["portrait-a", "numeric-b", "portrait-c", "numeric-d"]
            assert form_transitions["settled"] == 3
            assert form_transitions["surfaces"] == [False, True, True, True]
            assert not errors, errors
            browser.close()
            print("PASS: portrait handoff, cross-type form fades, layout retention and interrupted transition cleanup")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
