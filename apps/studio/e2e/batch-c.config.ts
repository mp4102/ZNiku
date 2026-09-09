/** 批次 C 的真实服务行为门禁独立输出，不清除其他批次证据。 */
import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: '.', testMatch: 'batch-c-regressions.ts', fullyParallel: false, workers: 1,
  timeout: 120_000, expect: { timeout: 15_000 }, retries: 0, outputDir: '../test-results/batch-c',
  use: { viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', trace: 'off', screenshot: 'off', actionTimeout: 15_000 },
})
