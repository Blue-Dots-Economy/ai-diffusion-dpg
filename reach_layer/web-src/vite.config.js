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
    outDir: '../web/dist',
    emptyOutDir: true,
  },
})
