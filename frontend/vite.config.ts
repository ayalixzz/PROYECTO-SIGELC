import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backendPort = Number(process.env.SIGELC_BACKEND_PORT ?? '8001')
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) {
  throw new Error('SIGELC_BACKEND_PORT debe ser un puerto entre 1024 y 65535.')
}
const django = { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false }
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
