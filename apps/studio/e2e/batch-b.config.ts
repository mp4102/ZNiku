/** B 批生产几何验收独立输出，不覆盖前置实验或 A 批证据。 */
import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: '.', testMatch: 'batch-b-capture.ts', fullyParallel: false, workers: 1,
  timeout: 240_000, expect: { timeout: 15_000 }, retries: 0, outputDir: '../test-results/batch-b',
  use: { viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', trace: 'off', screenshot: 'off', actionTimeout: 15_000 },
})
