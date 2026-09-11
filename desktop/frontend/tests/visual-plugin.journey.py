"""Opt-in browser journey: real Studio, plugin workers and renderer/editor modules.

The native invoke transport is replaced by a bridge to the production Core boundary.
Window hit testing and the custom resource protocol have separate Rust coverage.
"""
from __future__ import annotations

import functools
import base64
import http.server
import importlib.util
import json
import os
import sys
import tempfile
import threading
import wave
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright
from app.agent.runtime import AgentRuntime
from app.agent.actions import AgentEvent
from app.config.character_loader import CharacterRegistry
from app.core.chat_pipeline import ChatPipeline
from app.core_host.character_studio import CharacterStudioBoundary
from app.core_host.real_chat import _project_reply
from app.llm.api_client import ChatCompletionTurn, OpenAICompatibleClient
from app.llm.chat_reply import parse_chat_reply
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore

spec = importlib.util.spec_from_file_location("visual_fixture", ROOT / "tests/integration/test_visual_plugin_boundary.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="sakura-visual-browser-") as temporary, fixture.numeric_application(Path(temporary)) as (application, package, resource), sync_playwright() as playwright:
            boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
            # Distinct imported voice drafts make cross-character autosave corruption observable.
            voice_paths = {}
            for character_id in ("character", "other"):
                opened = boundary._dispatch("studio.character.open", {"characterId": character_id}) if character_id == "character" else boundary._dispatch("studio.character.create", {"doc": {"id": character_id, "displayName": "另一个角色"}})
                if character_id == "other":
                    opened = boundary._dispatch("studio.visual.create", {"workspaceId": character_id, "type": "fixture.numeric@1"})
                source = Path(temporary) / f"{character_id}.wav"
                with wave.open(str(source), "wb") as stream:
                    stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000); stream.writeframes(b"\x00\x00" * 2400)
                imported = boundary._dispatch("studio.asset.import", {"workspaceId": character_id, "kind": "referenceAudio", "path": str(source)})
                doc = opened["doc"]
                doc["voice"] = {"toneRefs": "voice/refs/ref.txt", "refLang": "ja", "textLang": "ja"}
                doc["referenceAudios"] = [{"audioPath": imported["relativePath"], "refLang": "ja", "refText": character_id, "tone": "中性"}]
                doc["theme"]["primaryColor"] = "#112233" if character_id == "character" else "#445566"
                boundary._dispatch("studio.draft.save", {"workspaceId": character_id, "doc": doc})
                voice_paths[character_id] = imported["relativePath"]
            errors, saves = [], []
            def invoke(command, params=None):
                params = params or {}
                if command == "studio_bootstrap":
                    return boundary._dispatch("studio.bootstrap", {"initialCharacterId": "character"})
                if command in {"show_studio", "close_character_studio"}:
                    return None
                if command != "studio_request":
                    raise ValueError(f"unexpected native command: {command}")
                method = params["method"]
                result = boundary._dispatch(method, params["params"])
                if method == "studio.draft.save":
                    saves.append(result["doc"])
                if method == "studio.visual.open":
                    result["presentation"]["visual"]["editor"] = origin + "/tests/fixtures/visual_numeric/frontend/editor.js"
                return result

            channel = os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None)
            browser = playwright.chromium.launch(channel=channel)
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_function("nativeInvoke", invoke)
            page.add_init_script("window.__TAURI__ = {core: {invoke: async (name, args) => {const result = await window.nativeInvoke(name, args); if (window.delayCreate && args?.method === 'studio.visual.create') await new Promise(resolve => { window.releaseCreate = resolve; }); return result;}}, event: {listen: async () => () => {}}};")
            page.goto(origin + "/desktop/frontend/studio/")
            expect(page.locator("#displayName")).to_have_value("角色")
            page.locator('[data-page="portrait"]').click()
            angle = page.locator('#expressionList input[type="number"]')
            expect(angle).to_have_value("20")
            angle.fill("28")
            # Explicit save uses the real collect/validate/autosave/publish path.
            page.locator("#saveButton").click()
            expect(page.locator("#saveButton")).to_be_enabled()
            page.wait_for_function("!document.getElementById('errorText').textContent")
            profile = CharacterRegistry(package.parents[1]).get("character")
            assert json.loads((profile.package_dir / "numeric/resource.json").read_text())["maxAngle"] == 28
            page.reload()
            expect(page.locator("#displayName")).to_have_value("角色")
            page.locator('[data-page="portrait"]').click()
            expect(page.locator('#expressionList input[type="number"]')).to_have_value("28")

            page.evaluate("document.getElementById('studioCharacterSelect').value='other'; document.getElementById('studioCharacterSelect').dispatchEvent(new Event('change'))")
            expect(page.locator("#displayName")).to_have_value("另一个角色")
            page.locator("#displayName").fill("另一个角色改名")
            page.wait_for_timeout(850)
            other_doc = boundary._dispatch("studio.character.open", {"characterId": "other"})["doc"]
            assert other_doc["referenceAudios"][0]["audioPath"] == voice_paths["other"]
            assert other_doc["theme"]["primaryColor"] == "#445566"
            assert (boundary._service._workspace_package("other") / voice_paths["other"]).is_file()
            assert all(saved["referenceAudios"][0]["audioPath"] == voice_paths[saved["id"]] for saved in saves if saved["referenceAudios"])
            page.evaluate("document.getElementById('studioCharacterSelect').value='character'; document.getElementById('studioCharacterSelect').dispatchEvent(new Event('change'))")
            expect(page.locator("#displayName")).to_have_value("角色")
            page.locator('[data-page="portrait"]').click()
            page.evaluate("window.delayCreate=true")
            page.locator("#addExpressionButton").click()
            dialog = page.get_by_role("dialog", name="添加形态", exact=True)
            dialog.get_by_role("textbox", name="形态名称", exact=True).fill("第二套形态")
            dialog.get_by_role("button", name="添加", exact=True).click()
            page.wait_for_function("typeof window.releaseCreate === 'function'")
            expect(page.locator("#studioCharacterSelect")).to_be_disabled()
            page.evaluate("window.releaseCreate(); window.delayCreate=false")
            expect(page.locator("#visualResourceList .form-card")).to_have_count(2)
            expect(page.locator("#studioCharacterSelect")).to_be_enabled()
            page.locator("#removeVisualButton").click()
            page.get_by_role("dialog", name="移除「第二套形态」", exact=True).get_by_role("button", name="移除", exact=True).click()
            expect(page.locator("#visualResourceList .form-card")).to_have_count(1)
            expect(page.locator('#expressionList input[type="number"]')).to_have_value("28")

            binding = application.application.visuals.bind(profile.id, profile.package_dir, profile.current_visual_resource)
            public = binding.presentation()
            public["renderer"] = origin + "/tests/fixtures/visual_numeric/frontend/renderer.js"
            raw = json.dumps({"segments": [{"ja": "こんにちは", "zh": "你好", "tone": "中性", "control": {"version": 1, "resourceId": resource.id, "payload": {"angle": 27, "wave": True}}}]})
            client = MagicMock(spec=OpenAICompatibleClient)
            client.complete_with_tools.return_value = ChatCompletionTurn(content=raw, tool_calls=[], message={"role": "assistant", "content": raw})
            client.chat.return_value = parse_chat_reply(raw)
            client.resolve_dialogue_params.return_value = (0.8, {})
            runtime = AgentRuntime(client, "数值角色", character_id=profile.id)
            runtime.set_visual_binding(binding)
            assert "28" in runtime._build_tool_system_prompt()
            pipeline = ChatPipeline(runtime)
            replies = [pipeline.run_user_message([{"role": "user", "content": "招手"}]), pipeline.run_event(AgentEvent("reminder_due", {"message": "招手"}))]
            timeline = TimelineStore(Path(temporary) / "timeline.sqlite3")
            timeline.initialize()
            controls = []
            for index, result in enumerate(replies):
                segments = _project_reply(result.reply)
                timeline.append(NewTimelineEntry(entry_id=f"reply-{index}", turn_id=f"turn-{index}", character_id=profile.id, kind=TimelineKind.ASSISTANT, origin="chat", created_at="2026-09-12T10:00:00+08:00", payload={"segments": segments}))
                controls.append(segments[0]["control"])

            surface = browser.new_page()
            surface.on("pageerror", lambda error: errors.append(str(error)))
            surface.goto(origin + "/tests/fixtures/visual_numeric/")
            # Only the native window is simulated here. Renderer and playback host are real.
            surface.add_style_tag(url=origin + "/desktop/frontend/styles.css")
            surface.evaluate("document.body.innerHTML = '<main id=stage class=pet-stage><section class=portrait><div class=visual-renderer></div></section></main>'")
            result = surface.evaluate("""async ({publicVisual, controls}) => {
              const {createRendererHost} = await import('/desktop/frontend/pet/renderer-host.js');
              const {createChatPresentationReducer} = await import('/desktop/frontend/chat/chat-presentation.js');
              const {applyVisualSurfaceAppearance} = await import('/desktop/frontend/pet/visual-surface.js');
              const {computePetLayout,applyPetLayout} = await import('/desktop/frontend/pet/layout.js');
              const layout = computePetLayout(await (await fetch('/desktop/frontend/pet/layout-contract.json')).json());
              const stage = document.getElementById('stage');
              applyPetLayout(stage, layout, 1, [0,0,...layout.windowSize]);
              const sizes = [];
              const host = createRendererHost({container: stage.querySelector('.visual-renderer'), services: {setSurface: (size) => {sizes.push(size); applyVisualSurfaceAppearance(stage,layout,size,100); return true;}}});
              await host.bind({visual: publicVisual});
              const model = document.querySelector('[aria-label="数值模型"]');
              const geometry = [50,150].map(percent => {
                const hits = applyVisualSurfaceAppearance(stage,layout,sizes.at(-1),percent);
                const rect = model.parentElement.getBoundingClientRect();
                const glyph = model.getBoundingClientRect();
                return {rect:[rect.x,rect.y,rect.width,rect.height],hit:hits.drag[0],glyph:[glyph.width,glyph.height]};
              });
              const before = model.dataset.waves || '0';
              const reducer = createChatPresentationReducer({initialMessage:'你好'});
              reducer.reduce({type:'lifecycle',generationId:'g',generationNumber:1,revision:1,status:'ready'});
              host.begin('chat');
              await host.play(controls[0], 'chat', 0);
              await host.play(controls[0], 'chat', 0);
              const once = model.dataset.waves;
              host.begin('event');
              const active = host.play(controls[1], 'event', 0);
              await new Promise((resolve) => setTimeout(resolve, 30));
              host.cancel();
              await active;
              const stopped = model.getAnimations().length;
              const waves = model.dataset.waves;
              reducer.reduce({type:'history.loaded',segments:[{text:'过去',control:controls[0]}]});
              host.clear();
              await host.bind({visual:{...publicVisual,bindingId:'b'.repeat(32)}});
              host.begin('stale');
              const rejected = !await host.play(controls[0], 'stale', 0);
              const oldSignal = model.isConnected;
              host.destroy();
              return {before,once,waves,stopped,rejected,oldSignal,angle:model.dataset.angle,sizes,geometry};
            }""", {"publicVisual": public, "controls": controls})
            small, large = result.pop("geometry")
            for measured in [small, large]:
                assert all(abs(visual - hit) < 2 for visual, hit in zip(measured["rect"], measured["hit"])), measured
            assert all(abs(big / little - 3) < 0.01 for little, big in zip(small["glyph"], large["glyph"])), (small, large)
            assert result == {"before": "0", "once": "1", "waves": "2", "stopped": 0, "rejected": True, "oldSignal": False, "angle": "27", "sizes": [{"width": 320, "height": 420}, {"width": 320, "height": 420}]}, result
            png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==")
            surface.route("**/portrait-*.png", lambda route: route.fulfill(body=png, content_type="image/png"))
            portrait_result = surface.evaluate("""async () => {
              const {mount} = await import('/plugins/builtin/sakura_portrait/frontend/renderer.js');
              const observations=[];
              for (const phase of ['prepare','commit']) {
                const target=document.createElement('div'); document.body.append(target);
                let release, entered;
                const waiting=new Promise(resolve => {entered=resolve});
                const gate=() => {entered(); return new Promise(resolve=>{release=resolve})};
                const signal=new AbortController();
                const instance=mount({container:target,signal:signal.signal,
                  resource:{assets:{A:location.origin+'/portrait-a.png',B:location.origin+'/portrait-b.png'},data:{defaultKey:'A',metadata:{A:{width:1,height:1},B:{width:1,height:1}}}},
                  host:{prepareSurface:()=>phase==='prepare'?gate():true,setSurface:({assetKey})=>assetKey==='B'&&phase==='commit'?gate():true,finishSurface:()=>true,unavailable:(error)=>{throw new Error(error)}}});
                await instance.ready;
                const pending=instance.applyState({key:'B'},{signal:signal.signal});
                await waiting;
                instance.cancel(); release(true); await pending;
                observations.push({current:target.shadowRoot.querySelector('.portrait-image--current').src.endsWith('/portrait-a.png'),preview:target.shadowRoot.querySelector('.portrait-image--next').hasAttribute('src')});
                instance.destroy(); target.remove();
              }
              return observations;
            }""")
            assert portrait_result == [{"current": True, "preview": False}, {"current": True, "preview": False}], portrait_result
            surface.route("**/portrait-broken.png", lambda route: route.fulfill(body=b"invalid PNG", content_type="image/png"))
            portrait_failure = surface.evaluate("""async () => {
              const {mount} = await import('/plugins/builtin/sakura_portrait/frontend/renderer.js');
              const observations=[];
              for (const phase of ['decode','commit']) {
                const target=document.createElement('div'); document.body.append(target);
                let fallback=false, active='';
                const errors=[], commits=[];
                const signal=new AbortController();
                const instance=mount({container:target,signal:signal.signal,
                  resource:{assets:{A:location.origin+'/portrait-a.png',B:location.origin+(phase==='decode'?'/portrait-broken.png':'/portrait-b.png')},data:{defaultKey:'A',metadata:{A:{width:1,height:1},B:{width:1,height:1}}}},
                  host:{prepareSurface:()=>true,setSurface:async ({assetKey})=>{if(assetKey==='B') throw new Error('commit failed'); active=assetKey; commits.push(assetKey); fallback=false; return true;},finishSurface:()=>true,cancelSurface:()=>{},reportError:code=>errors.push(code),unavailable:()=>{fallback=true;active='';}}});
                await instance.ready;
                await instance.applyState({key:'B'},{signal:signal.signal});
                const clean=!target.shadowRoot.querySelector('.portrait-image--next').hasAttribute('src') && !target.shadowRoot.querySelector('.portrait-frame').classList.contains('is-transitioning');
                await instance.applyState({key:'A'},{signal:signal.signal});
                observations.push({active,fallback,clean,commits,errors,current:target.shadowRoot.querySelector('.portrait-image--current').src.endsWith('/portrait-a.png')});
                instance.destroy(); target.remove();
              }
              return observations;
            }""")
            for phase, observed in zip(["DECODE", "COMMIT"], portrait_failure):
                assert observed == {"active": "A", "fallback": False, "clean": True, "commits": ["A"], "errors": [f"PORTRAIT_{phase}_FAILED"], "current": True}, observed
            application.application.set_plugin_enabled("fixture.numeric", False)
            expect(page.locator('#expressionList input[type="number"]')).to_have_count(0)
            assert binding.parse_control({"version": 1, "resourceId": resource.id, "payload": {"angle": 1}}).control is None
            assert timeline.read_all(profile.id)[0].payload["segments"][0]["text"] == "こんにちは"
            assert not errors, errors
            browser.close()
            print("PASS: real Studio edit/save/reopen -> plugin prompt/parse -> chat/event -> history -> browser state/action/cancel/rebind; numeric resource has no image")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
