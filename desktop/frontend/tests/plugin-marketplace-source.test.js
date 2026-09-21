import assert from "node:assert/strict";
import test from "node:test";
import { catalogPlugins, compareVersions, createMarketplaceSource } from "../settings/plugin-marketplace-source.js";

const release = (version, extra = {}) => ({ version, prerelease: version.includes("-"), yanked: false,
  manifest: { id: "demo", version, api: 4, name: "演示", author: "作者", description: "说明", requires: ["host.service"], presentation: { category: "model" } },
  package: { url: "https://example.test/plugin.zip", size: 10 }, ...extra });
const catalog = { schema_version: 1, plugins: [{ id: "demo", repository: "https://github.com/a/b", versions: [
  release("1.9.0"), release("2.0.0-beta.1"), release("1.10.0"), release("3.0.0", { yanked: true, yank_reason: "已撤回", manifest: null, package: null }),
] }] };

test("catalog supplies details and chooses the highest available stable compatible version", () => {
  const [p] = catalogPlugins(catalog, { api: 4, services: ["host.service"] });
  assert.equal(p.name, "演示"); assert.equal(p.author, "作者"); assert.equal(p.description, "说明"); assert.equal(p.category, "表现");
  assert.equal(p.recommendedVersion, "1.10.0");
  assert.equal(p.versions[0].yanked, "已撤回");
  assert.equal(catalogPlugins(catalog, { api: 4, services: [] })[0].recommendedVersion, undefined);
  assert.equal(catalogPlugins(catalog, { api: 5, services: ["host.service"] })[0].recommendedVersion, undefined);
  assert.ok(compareVersions("1.0.0-beta.10", "1.0.0-beta.2") > 0);
  assert.equal(compareVersions("1.0.0+build.1", "1.0.0+build.2"), 0);
});

class Channel { onmessage = () => {}; }
test("market installation uses current runtime identity and cancels in-flight downloads", async () => {
  let abort, installed;
  const calls = [], controller = new AbortController();
  const source = createMarketplaceSource({ Channel, randomUUID: () => "request", host: { isDirty: () => false },
    invoke: async (name, args) => {
      calls.push(name);
      if (name === "settings_plugins_get") return { plugins: [], revision: "revision", windowGeneration: 2, coreGenerationId: "core" };
      if (name === "settings_marketplace_cancel") { abort(); return; }
      if (name === "settings_marketplace_install") {
        installed = args;
        return new Promise((_, reject) => { abort = () => reject(new Error("cancelled")); args.progress.onmessage({ phase: "downloading" }); controller.abort(); });
      }
    },
  });
  const [plugin] = catalogPlugins(catalog, { api: 4, services: ["host.service"] });
  await assert.rejects(source.install(plugin, { signal: controller.signal, onProgress() {} }), /cancelled/);
  assert.equal(installed.version, "1.10.0"); assert.equal(installed.revision, "revision");
  assert.equal(installed.coreGenerationId, "core"); assert.ok(calls.includes("settings_marketplace_cancel"));
});

test("updates cannot replace enabled or bundled plugins or downgrade newer versions", async () => {
  const [plugin] = catalogPlugins(catalog, { api: 4, services: ["host.service"] });
  for (const existing of [ { enabled: true, source: "user", version: "1.0.0" },
    { enabled: false, source: "bundled", version: "1.0.0" }, { enabled: false, source: "user", version: "2.0.0" } ]) {
    const source = createMarketplaceSource({ Channel, host: { isDirty: () => false }, invoke: async name => {
      assert.equal(name, "settings_plugins_get"); return { plugins: [{ pluginId: "demo", ...existing }] };
    } });
    await assert.rejects(source.install(plugin, { signal: new AbortController().signal, onProgress() {} }));
  }
});


test("connection plugins keep their declared marketplace category", () => {
  const mobile = release("1.0.0");
  mobile.manifest.presentation.category = "connectivity";
  const [plugin] = catalogPlugins({ schema_version: 1, plugins: [{ id: "sakura_mobile", versions: [mobile] }] },
    { api: 4, services: ["host.service"] });
  assert.equal(plugin.category, "连接");
  assert.equal(plugin.recommendedVersion, "1.0.0");
});
