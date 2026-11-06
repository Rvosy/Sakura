import { test, expect } from "@playwright/test";

test("TTS samples show outcomes and submit environment filters", async ({
  page,
}, testInfo) => {
  const sample = {
    stage: "synthesis",
    installations: 1,
    attempts: 3,
    succeeded: 1,
    failed: 1,
    cancelled: 1,
    failureRate: 50,
    averageMs: 100,
    backend: "cuda",
    gpuName: "NVIDIA RTX 4060",
    bundleVersion: "1.0.0",
    performanceSamples: 1,
    inferenceSamples: 1,
    averageAudioSeconds: 10,
    p50RequestMs: 2000,
    p95RequestMs: 9000,
    p50Rtf: 0.2,
    p95Rtf: 0.9,
    averageReferenceMs: 200,
    averageGptMs: 700,
    averageSovitsMs: 400,
  };
  await page.route("**/admin/api/**", (route) =>
    route.fulfill({ json: { stages: [sample], items: [sample] } }),
  );
  await page.goto("/admin/#tts");
  await expect(page.getByText("50.0%", { exact: true }).first()).toBeVisible();
  const performance = page.getByRole("table", {
    name: "TTS 合成性能",
    exact: true,
  });
  await expect(performance.getByText("0.200", { exact: true })).toBeVisible();
  await expect(performance.getByText("0.900", { exact: true })).toBeVisible();
  const stages = page.getByRole("table", {
    name: "TTS 合成阶段耗时",
    exact: true,
  });
  await expect(stages.getByText("700", { exact: true })).toBeVisible();
  await page.getByLabel("运行后端", { exact: true }).fill("cuda");
  const request = page.waitForRequest(
    (request) =>
      request.url().includes("v2/tts?") &&
      request.url().includes("backend=cuda"),
  );
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await request;
  await page.screenshot({
    path: testInfo.outputPath("tts-performance.png"),
    fullPage: true,
  });
});

test("older TTS results keep missing performance distinct from zero", async ({
  page,
}) => {
  await page.route("**/admin/api/**", (route) =>
    route.fulfill({
      json: {
        stages: [],
        items: [{ stage: "synthesis", backend: "cpu", performanceSamples: 0 }],
      },
    }),
  );
  await page.goto("/admin/#tts");
  const performance = page.getByRole("table", {
    name: "TTS 合成性能",
    exact: true,
  });
  await expect(
    performance.getByRole("cell", { name: "0", exact: true }),
  ).toBeVisible();
  await expect(
    performance.getByRole("cell", { name: "—", exact: true }).first(),
  ).toBeVisible();
  await expect(performance).not.toContainText("0.000");
  await expect(performance).not.toContainText("NaN");
});
