import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { readFileSync } from 'node:fs'

// 构建时从根目录 VERSION 文件读取版本号，注入为全局常量 __APP_VERSION__
// 与后端 src/shared/version.py 共享同一个 VERSION 文件，确保前后端版本一致
const appVersion = readFileSync(resolve(__dirname, '../VERSION'), 'utf-8').trim()

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion)
  },
  // 构建产物直接输出到项目根目录的 static/，与 Dockerfile 中 `COPY static/ /app/static/` 对齐
  build: {
    outDir: resolve(__dirname, '../static'),
    // ``static/`` also holds versioned skill packages served by the backend.
    // Emptying the shared directory during an admin build silently removes
    // those packages, so Vite may replace only generated assets here.
    emptyOutDir: false
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true
      }
    }
  }
})
