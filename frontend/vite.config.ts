import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const django = { target: 'http://127.0.0.1:8001', changeOrigin: false }
const local = {
  host: '127.0.0.1',
  port: 5173,
  strictPort: true,
  proxy: { '/api/': django, '/documentos/': django },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: local,
  preview: local,
})
