import { defineConfig, devices } from '@playwright/test'

const slowMo = Number(process.env.E2E_SLOW_MO_MS ?? 0)
const expectTimeout = Number(process.env.E2E_EXPECT_TIMEOUT_MS ?? (slowMo > 0 ? 30_000 : 5_000))

export default defineConfig({
  testDir: './e2e',
  expect: {
    timeout: expectTimeout,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['junit', { outputFile: 'playwright-report/results.xml' }], ['html']] : 'html',
  use: {
    baseURL: 'http://localhost:5173',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
    launchOptions: slowMo > 0 ? { slowMo } : undefined,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
