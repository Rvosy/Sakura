import { test, expect } from "@playwright/test";

test("TTS samples show outcomes and submit environment filters", async ({
  page,
}, info) => {
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
  };
  await page.route("**/admin/api/**", (route) =>
    route.fulfill({ json: { stages: [sample], items: [sample] } }),
  );
  await page.goto("/admin/#tts");
  await expect(
    page.getByRole("heading", { name: "SakuraTTS 运行结果" }),
  ).toBeVisible();
  await expect(page.getByText("50.0%", { exact: true })).toHaveCount(2);
  await page.getByLabel("运行后端", { exact: true }).fill("cuda");
  const request = page.waitForRequest(
    (request) =>
      request.url().includes("v2/tts?") &&
      request.url().includes("backend=cuda"),
  );
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await request;
  await page.screenshot({
    path: info.outputPath("tts-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "筛选", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("tts-mobile.png"),
    fullPage: true,
  });
});
