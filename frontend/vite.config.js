import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

// 每次构建写入唯一的 app-build 标记：懒加载页面（如 OrderCreate）改代码时，
// 入口 index-*.js 的 hash 不一定变化，仅比较入口脚本会漏掉发版。
function appBuildStamp() {
  const stamp = new Date().toISOString()
  return {
    name: 'app-build-stamp',
    transformIndexHtml(html) {
      return html.replace('</head>', `  <meta name="app-build" content="${stamp}" />\n  </head>`)
    },
  }
}

export default defineConfig({
  plugins: [vue(), appBuildStamp()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/api/v1/chat/ws': {
        target: proxyTarget,
        changeOrigin: true,
        ws: true
      },
      '/api': {
        target: proxyTarget,
        changeOrigin: true
      },
      '/uploads': {
        target: proxyTarget,
        changeOrigin: true
      }
    }
  },
  test: {
    environment: 'node',
  },
})
