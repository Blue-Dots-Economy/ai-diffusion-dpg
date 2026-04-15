import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/chat': 'http://localhost:8005',
      '/app-config': 'http://localhost:8005',
      '/user-history': 'http://localhost:8005',
      '/health': 'http://localhost:8005',
    },
  },
  build: {
    outDir: '../dist',       // local dev: outputs to reach_layer/web/dist (sibling of server.py)
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/main.jsx', 'src/test/**'],
    },
  },
})
