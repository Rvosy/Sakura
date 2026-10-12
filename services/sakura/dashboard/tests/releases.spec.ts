import { test, expect } from "@playwright/test";

test("saved release preview, explicit publish and conflicts", async ({
  page,
}) => {
  let draft: any = null;
  const published: any[] = [];
  let conflict = true;
  const conflictReason = "线上版本已变化，请刷新并核对后再发布。";
  const live = {
    updater: { version: "1.1.0" },
    consistent: true,
    revision: "original",
    fileUpdatedAt: "2026-09-13T12:00:00Z",
  };
  await page.route("**/admin/api/control/**", async (route) => {
    const url = route.request().url();
    const body =
      route.request().method() === "POST"
        ? route.request().postDataJSON()
        : null;
    let result: any;
    if (url.endsWith("/status"))
      result = {
        live,
        drafts: draft ? [draft] : [],
        operations: published,
        endpoint: "https://api.sakura.cialloo.cn/service/v1/latest.json",
      };
    else if (url.endsWith("/drafts"))
      result = draft = {
        id: "draft-1",
        version: body.version,
        state: "ready",
        created_at: "2026-09-13T12:00:00Z",
        payload: {
          release: {
            urgent: false,
            minimumSupported: null,
            downloads: {
              windowsX64Setup:
                "https://github.com/Rvosy/Sakura/releases/download/v1.2.0/Sakura-1.2.0-windows-x64-setup.exe",
            },
          },
          updater: { notes: "修复启动异常" },
        },
      };
    else if (url.endsWith("/save")) {
      expect(body.expectedPayload).toEqual(draft.payload);
      draft.payload.updater.notes = body.notes;
      draft.payload.release.urgent = body.urgent;
      draft.payload.release.minimumSupported = body.minimumSupported;
      result = draft;
    } else if (url.endsWith("/publish")) {
      expect(body.expectedRevision).toBe("original");
      expect(body.expectedPayload).toEqual(draft.payload);
      if (conflict) {
        conflict = false;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: conflictReason,
          }),
        });
        return;
      }
      draft.state = "published";
      live.updater.version = "1.2.0";
      published.push({
        id: 1,
        version: "1.2.0",
        action: "publish",
        state: "published",
        occurred_at: "2026-09-13T12:10:00Z",
      });
      result = draft;
    } else result = draft;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
  await page.goto("/admin/#releases");
  await expect(
    page.getByRole("heading", { name: "Sakura 1.1.0" }),
  ).toBeVisible();
  await page.getByLabel("GitHub 正式版本").fill("1.2.0");
  await page.getByRole("button", { name: "导入", exact: true }).click();
  await expect(page.getByLabel("更新说明")).toHaveValue("修复启动异常");
  await page.getByLabel("更新说明").fill("修复启动异常与导出");
  await expect(
    page.getByRole("button", { name: "发布版本", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(
    page.getByRole("button", { name: "发布版本", exact: true }),
  ).toBeEnabled();
  expect(draft.payload.updater.notes).toBe("修复启动异常与导出");
  expect(published).toEqual([]);
  await page.getByRole("button", { name: "发布版本", exact: true }).click();
  expect(published).toEqual([]);
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(conflictReason);
  expect(published).toEqual([]);
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Sakura 1.2.0" }),
  ).toBeVisible();
  await expect(page.getByLabel("更新说明")).toBeDisabled();
});
