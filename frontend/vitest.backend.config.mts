import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src'), 'server-only': path.resolve(import.meta.dirname, 'tests/support/server-only.ts') } },
  test: { include: ['tests/backend-integration/**/*.test.ts'], environment: 'node', testTimeout: 120_000, hookTimeout: 120_000, fileParallelism: false },
});
