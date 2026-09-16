import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:4187",
    viewport: { width: 1440, height: 1050 },
    reducedMotion: "reduce",
  },
  webServer: {
    command: "npm run preview -- --port 4187",
    url: "http://127.0.0.1:4187/admin/",
    reuseExistingServer: true,
  },
  reporter: "list",
});
