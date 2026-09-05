import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 产物直接输出到后端静态目录，桌面壳由 FastAPI 同源托管，无需再复制
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8721', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8721', changeOrigin: true },
    },
  },
  build: {
    outDir: fileURLToPath(new URL('../backend/app/static', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          'element-plus': ['element-plus'],
          vue: ['vue', 'vue-router'],
        },
      },
    },
  },
})
