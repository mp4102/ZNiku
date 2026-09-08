import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        // 保持 React/图画布依赖一组，Schema 与表单校验一组；不靠提高 warning 阈值掩盖体积。
        codeSplitting: { groups: [
          { name: 'graph-ui', test: /node_modules[\\/](?:@xyflow|react|react-dom|scheduler|zustand|d3-|use-sync-external-store)/, priority: 20 },
          { name: 'schema', test: /(?:node_modules[\\/](?:ajv|ajv-formats|fast-|json-schema-traverse|require-from-string)|project-service\.schema\.json)/, priority: 10 },
        ] },
      },
    },
  },
  test: {
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
