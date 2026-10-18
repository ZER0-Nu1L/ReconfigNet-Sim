import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  use: {
    baseURL: 'http://127.0.0.1:4323',
    trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: 'npm run preview -- --port 4323 --ignore-lock',
    url: 'http://127.0.0.1:4323/ReconfigNet-Sim/en/',
    // Let Playwright own the process even when Astro detects an agent environment.
    env: { ASTRO_PREVIEW_BACKGROUND: '1' },
    reuseExistingServer: false,
  },
});
