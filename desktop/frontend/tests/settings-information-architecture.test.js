import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("../settings/index.html", import.meta.url), "utf8");
const settingsJs = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");

test("system owns storage controls and legacy system toggles are absent", () => {
  const systemPage = html.match(/<section id="page-system"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(systemPage, /id="storageUserRoot"/);
  assert.match(systemPage, /id="storageTtsRoot"/);
  assert.doesNotMatch(html, /data-page="storage"|id="page-storage"|数据与存储/);
  assert.doesNotMatch(html, /agentTraceEnabled|debugLogEnabled|launchAtLogin|调试日志/);
});

test("about page exposes compact product links, sponsorship, and update checks", () => {
  const aboutPage = html.match(/<section id="page-about"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(aboutPage, /id="aboutWebsiteButton"/);
  assert.match(aboutPage, /id="aboutRepositoryButton"/);
  assert.match(aboutPage, /id="aboutChangelogButton"/);
  assert.match(aboutPage, /id="aboutSponsorButton"/);
  assert.match(aboutPage, /id="aboutVersion"/);
  assert.match(aboutPage, /id="updateCheckButton"/);
  assert.match(aboutPage, /id="aboutComponentsSummary"/);
  assert.match(aboutPage, /id="aboutComponentsRefresh"/);
  assert.match(aboutPage, /id="aboutComponentsList"/);
  assert.doesNotMatch(aboutPage, /始终陪在桌面的 AI 角色助手|aboutRepositoryUrl|updateActionButton|<fieldset/);
});

test("interaction owns screen awareness and omits unimplemented backchannel settings", () => {
  const interactionPage = html.match(/<section id="page-interaction"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(interactionPage, /主动屏幕感知/);
  assert.match(interactionPage, /id="screenResolution"/);
  assert.doesNotMatch(html, /data-page="privacy"|id="page-privacy"/);
  assert.doesNotMatch(html, /backchannel|快速接话|<legend>接话<\/legend>/i);
});

test("model context budget is an advanced parameter with one-million-token support", () => {
  const modelPage = html.match(/<section id="page-model"[\s\S]*?<\/section>/)?.[0] || "";
  const modelSlots = modelPage.match(/<fieldset class="settings-group">[\s\S]*?<\/fieldset>/)?.[0] || "";

  assert.match(modelPage, /<summary>高级参数<\/summary>[\s\S]*?id="contextWindowTokens"/);
  assert.match(modelPage, /上下文预算 \(tokens\)[\s\S]*?留空默认 32K/);
  assert.match(modelPage, /min="4096" max="2000000"[\s\S]*?例如 1000000/);
  assert.doesNotMatch(modelPage, /上下文窗口/);
  assert.doesNotMatch(modelSlots, /上下文预算|contextWindowTokens/);
  assert.doesNotMatch(modelPage, /memoryModelResourceCard|本地记忆模型|resource-foldout/);
});

test("voice and model pages do not duplicate component download controls", () => {
  const voicePage = html.match(/<section id="page-voice"[\s\S]*?<\/section>/)?.[0] || "";
  assert.doesNotMatch(voicePage, /ttsResourceCard|整合包|重新安装|在线安装/);
  assert.doesNotMatch(html, /id="memoryModelResourceCard"/);
});

test("about component actions restore focus by resource when the action label changes", () => {
  assert.match(settingsJs, /options\.focusActions \? resourceKey : ""/);
  assert.match(settingsJs, /renderAboutComponents\(\{ restoreResourceKey: restoreAboutResourceKey \}\)/);
});
