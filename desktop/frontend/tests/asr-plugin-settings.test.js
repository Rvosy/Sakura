import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../../../plugins/builtin/sakura_asr_hub/frontend/input.js";
import { createAsrController } from "../audio/asr-controller.js";
import { browserFixture, settle } from "./fixtures/plugin-settings-fixture.js";

test("Hub module owns device drafts, keeps missing devices and rejects stale enumeration", async () => {
  const { document } = browserFixture();
  const values = { selectedProviderId: "fixture.asr", inputDeviceId: "missing-mic" };
  const devices = [], calls = [];
  const lifetime = new AbortController();
  const component = await mount({ document, signal: lifetime.signal,
    importModule: async () => ({ createAsrController }), listen: async () => () => {},
    read: key => values[key], write: (key, value) => { values[key] = value; },
    enhanceSelect() {}, refreshSelect() {}, onError: assert.fail,
    invoke: async name => {
      calls.push(name);
      if (name === "settings_asr_get") return { providers: [{ providerId: "fixture.asr", label: "Fixture ASR" }] };
      if (name === "settings_asr_devices") return new Promise(resolve => devices.push(resolve));
      throw new Error(name);
    },
  });
  document.body.append(component.element);
  const selector = component.element.querySelector('[aria-label="麦克风"]');
  const refresh = component.element.querySelectorAll("button").find(button => button.textContent === "刷新设备");
  const later = refresh.fire("click");
  values.inputDeviceId = "user-choice-during-enumeration";
  devices[1]({ devices: [{ id: "new", label: "New" }], defaultDeviceId: "new" });
  await later;
  assert.equal(selector.value, "user-choice-during-enumeration");
  devices[0]({ devices: [{ id: "obsolete", label: "Old" }], defaultDeviceId: "obsolete" });
  await settle();
  assert.equal(selector.children.some(option => option.value === "obsolete"), false);
  selector.value = "new"; await selector.fire("change");
  assert.equal(values.inputDeviceId, "new");
  const provider = component.element.querySelector('[aria-label="测试引擎"]');
  provider.value = "another-test-engine"; await provider.fire("change");
  assert.equal(values.selectedProviderId, "fixture.asr");
  values.inputDeviceId = "missing-mic"; component.update();
  assert.equal(selector.value, "missing-mic");
  assert.equal(selector.children.some(option => option.value === "missing-mic"), true);
  assert.equal(calls.some(name => name.includes("save")), false);
  lifetime.abort(); component.dispose();
});
