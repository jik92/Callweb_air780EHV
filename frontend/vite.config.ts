import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { APP_NAME } from './src/constants'
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'console-title',
      transformIndexHtml() {
        return [{ tag: 'title', children: APP_NAME, injectTo: 'head' }]
      },
    },
  ],
  server: {
    port: 9011,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:9010' },
      '/ws': { target: 'ws://127.0.0.1:9010', ws: true },
    },
  },
})
