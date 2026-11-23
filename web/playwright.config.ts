import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  timeout: 45000,
  reporter: "list",
  outputDir: "../.local/browser-results",
  use: {
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        channel: process.env.CORE_TEST_BROWSER_CHANNEL || undefined,
      },
    },
  ],
});
