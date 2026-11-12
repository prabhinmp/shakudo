import { defineConfig, devices } from '@playwright/test'

// Default: tests run against the production build served by `vite preview`, with the API mocked
// in-browser (fast, deterministic). Set LIVE_URL to run the live specs against a real deployment.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: { baseURL: process.env.LIVE_URL || 'http://localhost:4173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.LIVE_URL
    ? undefined
    : { command: 'npm run build && npm run preview', url: 'http://localhost:4173/ui/', reuseExistingServer: !process.env.CI, timeout: 120_000 },
})
