import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: false },
  server: {
    port: 5173,
    // `npm run dev` talks to a container started with docker compose, so the
    // SPA can be developed against real host stats and a real Samba.
    proxy: { '/api': { target: 'http://localhost:8080', changeOrigin: true } },
  },
})
