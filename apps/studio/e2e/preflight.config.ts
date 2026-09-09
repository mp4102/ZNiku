/** v0.3.1 实施前观察独立运行，不把现状缺陷冒充正式产品通过或污染常规验收。 */
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: 'preflight-capture.ts', fullyParallel: false, workers: 1,
  timeout: 240_000, expect: { timeout: 15_000 }, retries: 0,
  outputDir: '../test-results/preflight',
  use: { viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', trace: 'off', screenshot: 'off', actionTimeout: 15_000 },
})
