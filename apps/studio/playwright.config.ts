import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  retries: 0,
  outputDir: './test-results',
  // 原始 trace/HAR/自动截图可能携带选择票据或路径；失败由受控诊断和遮罩截图覆盖。
  use: { viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', trace: 'off', screenshot: 'off', actionTimeout: 15_000 },
})
