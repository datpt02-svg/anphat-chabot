import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for M8a e2e.
 * Plan §4: webServer boots both Next dev + FastAPI; reuseExistingServer lets
 * dev workflow skip re-spawning when servers are already running.
 *
 * JWT_SECRET is forwarded to the backend subprocess so verify_token can decode
 * the dev JWT minted for /api/build_pc tests.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "list" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "pnpm dev",
      url: "http://localhost:3000",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "cd .. && JWT_SECRET=test-dev-secret COPILOTKIT_DEV_AUTH_BYPASS=true CORS_ALLOWED_ORIGINS='http://localhost:3000,http://localhost:5173' uv run uvicorn api.main:app --port 8000",
      url: "http://localhost:8000/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        JWT_SECRET: "test-dev-secret",
        COPILOTKIT_DEV_AUTH_BYPASS: "true",
        CORS_ALLOWED_ORIGINS: "http://localhost:3000,http://localhost:5173",
      },
    },
  ],
});
