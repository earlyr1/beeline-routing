/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { target: 'es2022' },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Покрытие — только с --coverage (CI: job frontend, оттуда же бейдж). Считаются все файлы src, и те, которые
    // ни один тест не импортирует; не считаются сами тесты и их помощники в src/test.
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/**/*.d.ts'],
      // Строки — только те, где есть код, как у istanbul: без этого v8 засчитывает и пустые строки, комментарии
      // и объявления типов (около 98% вместо 97%). В Vitest 4 так по умолчанию.
      experimentalAstAwareRemapping: true,
      reporter: ['text-summary', 'json-summary'],
      // Отчёт пишется и при упавшем тесте: бейдж покрытия в README тогда красный с настоящим процентом.
      reportOnFailure: true,
      // Чуть ниже измеренного (строки 97,3%, ветви 86,5%, функции 97,1%, выражения 95,0% на 24.09.2026):
      // заметное падение покрытия роняет CI и красит бейдж. Выросло — порог можно поднять.
      thresholds: { lines: 96, branches: 85, functions: 96, statements: 94 },
    },
  },
});
