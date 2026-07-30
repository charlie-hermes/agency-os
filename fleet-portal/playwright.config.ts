import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:3190",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:3190/",
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      FLEET_PORTAL_IDENTITY_MODE: "fixture",
      FLEET_PORTAL_FIXTURE_ACK: "local-test-only",
      FLEET_PORTAL_CLIENT_HOST: "fleet.madebyfleet.com",
    },
  },
});
