import { test, expect } from "@playwright/test";
test("original error evidence is visible as text and copied intact", async ({
  page,
  context,
}, testInfo) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const evidence = {
    diagnostic: "no such table: memories <img src=x onerror=alert(1)>",
    exception_chain:
      "RuntimeError: import failed\nCaused by: OperationalError: no such table: memories",
    exception_stack:
      "at migration:import_rows:42\nC:\\Users\\测试 用户\\Sakura\\data\\memory.db",
    endpoint: "https://provider.test/v1?model=demo&token=[REDACTED]",
  };
  const report = {
    schema: 3,
    reportId: "550e8400-e29b-41d4-a716-446655440001",
    receivedAt: "2026-09-12 12:00:00",
    installationId: "550e8400-e29b-41d4-a716-446655440000",
    runId: "run-1",
    error: {
      code: "LEGACY_IMPORT_INTERNAL",
      component: "core",
      event: "migration.failed",
      severity: "error",
    },
    app: { version: "1.1.0" },
    system: { platform: "windows" },
    context: {},
    stack: [],
    breadcrumbs: [],
    evidence,
    rawReport: { schema: 3, evidence },
  };
  await page.route("**/admin/api/**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        route.request().url().includes("/reports/")
          ? report
          : { items: [], total: 0 },
      ),
    }),
  );
  await page.goto(`/admin/#diagnostics?report_id=${report.reportId}`);
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("heading", { name: "原始错误" })).toBeVisible();
  await expect(drawer.locator("pre").first()).toHaveText(evidence.diagnostic);
  await expect(drawer.locator("img")).toHaveCount(0);
  await expect(
    drawer.getByText(evidence.exception_stack, { exact: true }),
  ).toBeVisible();
  await drawer.getByRole("button", { name: "复制完整报告" }).click();
  await expect(
    drawer.getByRole("region", { name: "原始错误" }).getByRole("status"),
  ).toHaveText("已复制");
  expect(
    JSON.parse(await page.evaluate(() => navigator.clipboard.readText())),
  ).toEqual(report.rawReport);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1050 });
    await drawer
      .locator(".offcanvas-body")
      .evaluate((element) => (element.scrollTop = 0));
    await expect(drawer.locator("pre").first()).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`original-error-${width}.png`),
    });
  }
});

test("SQL filters, pagination and export states preserve the selected scope", async ({
  page,
}, testInfo) => {
  const requests: string[] = [];
  let posted: any;
  await page.route("**/admin/api/**", async (route) => {
    const url = route.request().url();
    requests.push(url);
    let body: any = { items: [], total: 0 };
    if (url.includes("/v2/groups"))
      body = {
        items: [
          {
            fingerprint: "f-one",
            error_code: "TTS_RUNTIME_TIMEOUT",
            reports: 220,
            occurrences: 500,
            installations: 2,
          },
        ],
        total: 1,
      };
    if (url.includes("/records/errors"))
      body = {
        items: [
          {
            id: 1,
            report_id: "a",
            error_code: "TTS_RUNTIME_TIMEOUT",
            run_id: "run-one",
            installation_id: "install-one",
            details: { stage: "device_probe" },
          },
        ],
        total: 205,
        hasMore: !url.includes("cursor="),
        nextCursor: "next",
      };
    if (url.endsWith("/v2/exports")) {
      posted = route.request().postDataJSON();
      body = { id: "job-one", status: "running" };
    }
    if (url.endsWith("/exports/job-one"))
      body = { id: "job-one", status: "ready" };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/admin/#diagnostics?tab=reports&build=release-one");
  await expect(page.getByText("共 205 条记录")).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect
    .poll(() =>
      requests.some(
        (u) => u.includes("cursor=next") && u.includes("build=release-one"),
      ),
    )
    .toBe(true);
  await page.getByRole("button", { name: "生成分析包", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 ZIP" })).toBeVisible();
  expect(posted.build).toBe("release-one");
  for (const width of [1440, 900, 390]) {
    await page.setViewportSize({ width, height: 1050 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(width);
    await page
      .getByRole("button", { name: "首页", exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole("button", { name: "首页", exact: true }),
    ).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    if (width === 390) {
      await expect(page.locator(".sidebar")).toHaveAttribute("inert", "");
      expect(
        await page
          .locator(".sidebar")
          .evaluate((el) => el.getBoundingClientRect().right),
      ).toBeLessThanOrEqual(0);
    }
    await page.screenshot({
      path: testInfo.outputPath(`diagnostics-${width}.png`),
      fullPage: true,
    });
  }
});
test("operation-only links require an explicit run", async ({ page }) => {
  await page.route("**/admin/api/**", (r) =>
    r.fulfill({
      contentType: "application/json",
      body: '{"items":[],"total":0}',
    }),
  );
  await page.goto("/admin/#diagnostics?tab=timeline&operation=duplicate");
  await expect(
    page.getByText(
      "请先指定 Installation ID 和 Run ID。旧链接只有 Operation ID 时，需要选择具体运行。",
    ),
  ).toBeVisible();
});
test("export failure is visible and does not offer a partial ZIP", async ({
  page,
}) => {
  await page.route("**/admin/api/**", async (r) => {
    let body: any = { items: [], total: 0 };
    if (r.request().method() === "POST")
      body = { id: "limited", status: "running" };
    else if (r.request().url().endsWith("/exports/limited"))
      body = { status: "failed", error: "EXPORT_SIZE_LIMIT" };
    await r.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/admin/#diagnostics");
  await page.getByRole("button", { name: "生成分析包", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("缩小时间范围");
  await expect(page.getByRole("link", { name: "下载 ZIP" })).toHaveCount(0);
});
