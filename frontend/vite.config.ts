import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiPort = Number(process.env.VIBRADIAG_DEV_API_PORT || 8010)
if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) throw new Error('A valid loopback API port is required')
const proxy = {
  '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
  '/health': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
}

export default defineConfig({ plugins: [vue()], server: { proxy }, preview: { proxy } })
