import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxy = {
  '/api': { target: 'http://127.0.0.1:8010', changeOrigin: false },
  '/health': { target: 'http://127.0.0.1:8010', changeOrigin: false },
}

export default defineConfig({ plugins: [vue()], server: { proxy }, preview: { proxy } })
