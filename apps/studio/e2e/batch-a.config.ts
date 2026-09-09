/** A 批生产页面截图独立运行；不覆盖实施前证据，也不替代常规 15 项生产门禁。 */
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: 'batch-a-capture.ts', fullyParallel: false, workers: 1,
  timeout: 240_000, expect: { timeout: 15_000 }, retries: 0,
  outputDir: '../test-results/batch-a',
  use: { viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', trace: 'off', screenshot: 'off', actionTimeout: 15_000 },
})
