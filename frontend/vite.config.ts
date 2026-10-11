import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 9011,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:9010' },
      '/ws': { target: 'ws://127.0.0.1:9010', ws: true },
    },
  },
})
