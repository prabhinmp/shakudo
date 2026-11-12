import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The app is served by the API at /ui/, so assets must load from that base path.
// In dev, /api calls are proxied to the real API so the browser sees a single origin (no CORS).
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': process.env.API_URL || 'http://localhost:8080' } },
  build: { outDir: 'dist', sourcemap: false },
})
