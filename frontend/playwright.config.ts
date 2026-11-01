import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:33039", browserName: "chromium" },
  webServer: {
    command: "npm run start -- --port 33039",
    url: "http://127.0.0.1:33039",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
