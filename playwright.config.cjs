const { defineConfig } = require("@playwright/test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (fs.existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined);
module.exports = defineConfig({
  testDir: "./tests/browser",
  timeout: 120000,
  expect: { timeout: 15000 },
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5500",
    browserName: "chromium",
    launchOptions: { executablePath, args: ["--no-sandbox"] },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "python scripts/dev.py --no-browser",
    url: "http://127.0.0.1:5500/api/health",
    reuseExistingServer: false,
    env: { HEALTHRECORDS_DATA_DIR: fs.mkdtempSync(path.join(os.tmpdir(), "healthrecords-browser-")) },
    timeout: 120000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
  },
});
