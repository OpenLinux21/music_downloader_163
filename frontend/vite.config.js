import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物输出到 frontend/dist，由 FastAPI 静态托管（main.py 的 / 与 /assets）
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 开发模式（npm run dev）下把 API 请求代理到本地后端
      '/health': 'http://127.0.0.1:5000',
      '/search': 'http://127.0.0.1:5000',
      '/song': 'http://127.0.0.1:5000',
      '/playlist': 'http://127.0.0.1:5000',
      '/album': 'http://127.0.0.1:5000',
      '/download': 'http://127.0.0.1:5000',
      '/rank': 'http://127.0.0.1:5000',
      '/recommend': 'http://127.0.0.1:5000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
